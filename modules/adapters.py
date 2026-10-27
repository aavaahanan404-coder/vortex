"""Source adapters: verify a document's issuer data against a registry, with a six-state result and retry/backoff.

Input : doc_type, the document's extracted fields ({key: value} or extract's {key: {value, conf,...}}), the application
        date, the applicant's inst_id, a Source (anything with lookup(source, key[, inst_id]) -> list[row]).
Output: verify_document(...) -> AdapterResult {status, reason, source, key, attempts, waited_s, record, checks}.
        status is exactly one of
          verified               the registry record exists, belongs to the applicant and agrees with the document
          mismatch_detected      record missing, held by another institution, or a printed value differs from the registry
          expired                the registry validity ends before the application date
          verification_pending   the authority answered "pending" (not retried; retry_after_s says when to ask again)
          source_unavailable     the authority failed (503) on every attempt
          manual_review_required the lookup was ambiguous (several records) or the identifier could not be read
        plus "not_applicable" for document types with no external issuer (affidavit, infra statement).
        analyze(...) -> list[Finding]: source_unavailable / verification_pending / manual_review_required (soft, Tier 2:
        a person must look; the ids are in scoring.yaml needs_human_ids) and registry_mismatch (hard, Tier 2) / registry_expired
        (soft, Tier 2); info `registry_verified`.
Retry: only SourceUnavailable is retried, with exponential backoff (base_delay_s * factor^k, capped); the sleep function is
injected so tests and batch runs do not wait. Attempts and the total waiting time are in the result.
Precedence when several checks fail: not found, other institution, printed value differs, expired.
WorldSource is the MOCK authority over the corpus's read-only world.sqlite (including its scenario flags); a real deployment
implements the same one-method interface per authority.
Does NOT: sign requests, cache, or call the network; compare anything it was not told to (checks are listed in SPECS).
CLI   : python -m modules.adapters FILE.json --doc-type T --inst ID --date YYYY-MM-DD --world world.sqlite
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import yaml

from contracts import Finding

MODULE = "adapters"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "adapters.yaml"
STATES = ("verified", "mismatch_detected", "expired", "verification_pending", "source_unavailable", "manual_review_required")
_DATE_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %B %Y", "%d %b %Y", "%d-%m-%Y")


class SourceUnavailable(Exception):
    """The authority did not answer (HTTP 503 / timeout): retried."""


class SourcePending(Exception):
    """The authority accepted the request but has no answer yet: not retried."""


class AmbiguousMatch(Exception):
    """The lookup key matched several records where one was expected."""


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def parse_date(s) -> Optional[date]:
    if isinstance(s, date):
        return s
    s = str(s or "").strip()
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("&", "and"))


def _val(fields: dict, key: str):
    v = fields.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _int(s) -> Optional[int]:
    n = _num(s)
    return None if n is None else int(round(n))


def _num(s) -> Optional[float]:
    m = re.sub(r"[^\d.]", "", str(s or ""))
    try:
        return float(m)
    except ValueError:
        return None


def _money(s) -> Optional[int]:
    """'Rs. 4,96,03,427' -> 49603427 (digits only; the 'Rs.' dot must not survive)."""
    d = re.sub(r"\D", "", re.sub(r"(?i)\brs\.?", "", str(s or "")))
    return int(d) if d else None


def _is_ocr(fields: dict, key: str) -> bool:
    v = fields.get(key)
    return isinstance(v, dict) and v.get("source") == "ocr"


@dataclass
class AdapterResult:
    status: str
    reason: str = ""
    doc_type: str = ""
    source: Optional[str] = None
    key: Optional[str] = None
    attempts: int = 0
    waited_s: float = 0.0
    retry_after_s: Optional[float] = None
    record: Optional[dict] = None
    checks: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- mock authority
class WorldSource:
    """Mock registries over the corpus world database (opened read-only) with its scenario flags."""

    TABLES = {"fire": ("fire_certs", "cert_no"), "building": ("building_approvals", "cert_no"),
              "land": ("land_records", "survey_no"), "society": ("society_registry", "reg_no"),
              "university": ("university_affiliations", "aff_no"), "ca": ("ca_registry", "firm_no"),
              "faculty": ("faculty_employment", "faculty_id"), "institution": ("institutions", "inst_id"), "society_by_id": ("society_registry", "society_id"),
              "university_by_inst": ("university_affiliations", "inst_id"), "aicte_by_inst": ("aicte_history", "inst_id"),
              "financials": ("financials", "inst_id")}

    def __init__(self, db_path: str | Path, cfg: Optional[dict] = None, scenarios: bool = True):
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.cfg = cfg or load_config()
        # scenario flags are aimed at one institution (the note column): they only fire for that requester
        self.flags = {(r["source"], r["record_key"]): (r["behavior"], r["note"]) for r in self.con.execute("select * from scenario_flags")} if scenarios else {}
        self.calls: collections.Counter = collections.Counter()

    def close(self):
        self.con.close()

    def lookup(self, source: str, key: str, inst_id: Optional[str] = None) -> list[dict]:
        self.calls[(source, key, inst_id)] += 1
        n = self.calls[(source, key, inst_id)]
        flag, target = self.flags.get((source, key), (None, None))
        if flag and target and inst_id and str(target).startswith("SYN-I") and target != inst_id:
            flag = None                                     # the scenario is for another institution
        if flag == "always_503":
            raise SourceUnavailable(f"{source} authority returned 503")
        if flag == "flaky_then_ok" and n <= self.cfg["mock"]["flaky_failures"]:
            raise SourceUnavailable(f"{source} authority returned 503 (attempt {n})")
        if flag == "pending_then_ok" and n <= self.cfg["mock"]["pending_calls"]:
            raise SourcePending(f"{source} authority: request accepted, answer pending")
        table, col = self.TABLES[source]
        rows = [dict(r) for r in self.con.execute(f"select * from {table} where {col} = ?", (key,))]
        if flag == "ambiguous_match" and rows:
            rows = rows + [{**rows[0], "society_id": "SYN-S-AMBIGUOUS"}]
        return rows


# ----------------------------------------------------------------------------- retry
def call_with_retry(source, name: str, key: str, retry: dict, sleep: Callable[[float], None] = time.sleep,
                    inst_id: Optional[str] = None) -> tuple[list, int, float]:
    """(rows, attempts, waited_s). Retries SourceUnavailable with exponential backoff; raises it after the last attempt.
    SourcePending and AmbiguousMatch propagate at once."""
    waited, attempts = 0.0, 0
    for k in range(retry["max_attempts"]):
        attempts += 1
        try:
            return (source.lookup(name, key, inst_id) if inst_id else source.lookup(name, key)), attempts, waited
        except SourceUnavailable:
            if attempts >= retry["max_attempts"]:
                raise
            d = min(retry["max_delay_s"], retry["base_delay_s"] * retry["factor"] ** k)
            sleep(d); waited += d
    raise SourceUnavailable("unreachable")


# ----------------------------------------------------------------------------- per document type checks
def _check_validity(printed, rec_valid_to, app_date, checks) -> tuple[Optional[str], Optional[str]]:
    pd_, rd = parse_date(printed), parse_date(rec_valid_to)
    checks["printed_valid_to"], checks["registry_valid_to"] = str(printed), str(rec_valid_to)
    if pd_ is not None and rd is not None and pd_ != rd:
        return "mismatch_detected", "validity differs from registry"
    if rd is not None and app_date is not None and rd < app_date:
        return "expired", "valid_to before application date"
    return None, None


def _evaluate(doc_type: str, fields: dict, rows: list[dict], inst_id: str, app_date: Optional[date], cfg: dict, source, retry, sleep):
    """-> (status, reason, record, checks) from the registry rows of the document's own identifier."""
    checks: dict = {}
    if doc_type in ("fire_cert", "building_plan"):
        if not rows:
            return "mismatch_detected", "certificate not in registry", None, checks
        r = rows[0]
        if r["inst_id"] != inst_id:
            return "mismatch_detected", "certificate issued to another institution", r, {**checks, "registry_inst": r["inst_id"]}
        key = "valid_to"
        st, why = _check_validity(_val(fields, key), r["valid_to"], app_date, checks)
        return (st or "verified", why or "", r, checks)
    if doc_type == "land_doc":
        if not rows:
            return "mismatch_detected", "parcel not in registry", None, checks
        r = rows[0]
        if r["holder_inst_id"] != inst_id:
            return "mismatch_detected", "parcel held by another institution", r, {"registry_holder": r["holder_inst_id"]}
        pa = _num(_val(fields, "area_acres"))
        checks.update(printed_area=pa, registry_area=r["area_acres"])
        if pa is not None and abs(pa - r["area_acres"]) >= cfg["area_tolerance_acres"]:
            return "mismatch_detected", "area differs", r, checks
        return "verified", "", r, checks
    if doc_type == "society_reg":
        if len(rows) > 1:
            raise AmbiguousMatch(f"{len(rows)} society records for one registration number")
        if not rows:
            return "mismatch_detected", "society not in registry", None, checks
        r = rows[0]
        nm = _val(fields, "society_name")
        if nm and _norm(nm) != _norm(r["name"]):
            return "mismatch_detected", "society name differs", r, {"printed": nm, "registry": r["name"]}
        if r["status"] != "active":
            return "mismatch_detected", f"society status is {r['status']}", r, checks
        return "verified", "", r, checks
    if doc_type == "aff_letter":
        if not rows:
            return "mismatch_detected", "affiliation not in registry", None, checks
        if any(r["inst_id"] != inst_id for r in rows):
            return "mismatch_detected", "affiliation held by another institution", rows[0], checks
        st, why = _check_validity(_val(fields, "valid_to"), rows[0]["valid_to"], app_date, checks)
        return (st or "verified", why or "", {"rows": rows}, checks)
    if doc_type == "eoa":
        printed = {}
        for k, v in fields.items():
            m = re.fullmatch(r"courses\[(\d+)\]\.(course|intake)", k)
            if m:
                printed.setdefault(int(m.group(1)), {})[m.group(2)] = _val(fields, k)
        printed = [r for r in printed.values() if r.get("course") and r.get("intake")]
        if not rows:
            return "mismatch_detected", "no approval history for this institution", None, checks
        if not printed:
            raise AmbiguousMatch("the approved-course table could not be read from the document")
        latest = max(r["academic_year"] for r in rows)
        reg = {(_norm(r["course"]), int(r["approved_intake"])) for r in rows if r["academic_year"] == latest}
        bad = [r["course"] for r in printed if (_norm(r["course"]), _int(r["intake"])) not in reg]
        checks.update(academic_year=latest, printed_courses=len(printed), registry_courses=len(reg))
        if bad:
            return "mismatch_detected", "approved intake differs from registry for: " + "; ".join(bad), {"rows": rows}, checks
        return "verified", "", {"rows": rows}, checks
    if doc_type == "financial":
        if not rows:
            return "mismatch_detected", "CA firm not in registry", None, checks
        if rows[0]["status"] != "active":
            return "mismatch_detected", "CA firm not active", rows[0], checks
        fin = [dict(r) for r in source.con.execute("select * from financials where inst_id = ?", (inst_id,))] if hasattr(source, "con") else []
        if fin:
            inc, exp = _money(_val(fields, "fin.income.a")), _money(_val(fields, "fin.expenditure.a"))
            checks.update(printed_income=inc, registry_income=fin[0]["total_income"])
            if (inc is not None and abs(inc - fin[0]["total_income"]) > cfg["money_tolerance"]) or \
               (exp is not None and abs(exp - fin[0]["total_expenditure"]) > cfg["money_tolerance"]):
                return "mismatch_detected", "income or expenditure differs from registry", rows[0], checks
        return "verified", "", rows[0], checks
    raise KeyError(doc_type)


def _verify_faculty(fields: dict, inst_id: str, source, retry, sleep) -> AdapterResult:
    ids = sorted({str(v["value"] if isinstance(v, dict) else v) for k, v in fields.items() if re.fullmatch(r"faculty\[\d+\]\.faculty_id", k)})
    away, attempts, waited, employment = [], 0, 0.0, {}
    for fid in ids:
        rows, a, w = call_with_retry(source, "faculty", fid, retry, sleep, inst_id)
        attempts += a; waited += w
        cur = [r for r in rows if r["to_date"] in (None, "")]
        employment[fid] = cur[0]["inst_id"] if cur else None
        if employment[fid] != inst_id:
            away.append(fid)
    if away:
        return AdapterResult("mismatch_detected", f"{len(away)} faculty not employed here per registry", "faculty_list", "faculty", None,
                             attempts, waited, record={"employment": employment}, checks={"not_employed_here": away})
    return AdapterResult("verified", "", "faculty_list", "faculty", None, attempts, waited, record={"employment": employment})


def verify_document(doc_type: str, fields: dict, application_date, inst_id: str, source, cfg: Optional[dict] = None,
                    sleep: Callable[[float], None] = time.sleep) -> AdapterResult:
    """Verify one document against its registry. Never raises."""
    cfg = cfg or load_config()
    spec = cfg["sources"].get(doc_type)
    if spec is None:
        return AdapterResult("not_applicable", cfg["not_applicable_reason"], doc_type)
    app = parse_date(application_date)
    name, keyf = spec["source"], spec["key"]
    try:
        if doc_type == "faculty_list":
            return _verify_faculty(fields, inst_id, source, cfg["retry"], sleep)
        key = _val(fields, keyf)
        if not key:
            return AdapterResult("manual_review_required", f"identifier '{keyf}' could not be read from the document", doc_type, name)
        lookup_name = "university" if name == "university" else "aicte_by_inst" if name == "aicte" else name
        lookup_key = str(key)
        if name == "aicte":                                     # approvals are looked up by institution
            lookup_key = inst_id
        rows, attempts, waited = call_with_retry(source, lookup_name, lookup_key, cfg["retry"], sleep, inst_id)
        st, why, rec, checks = _evaluate(doc_type, fields, rows, inst_id, app, cfg, source, cfg["retry"], sleep)
        if st == "mismatch_detected" and rec is None and name != "aicte" and _is_ocr(fields, keyf):
            # "not found" for an identifier that OCR read from a scan may just be a misread: a person decides, no accusation
            return AdapterResult("manual_review_required", f"{why}; the identifier '{key}' was read by OCR and may be a misread",
                                 doc_type, name, str(key), attempts, waited, checks={"ocr_conf": fields[keyf].get("conf")})
        return AdapterResult(st, why, doc_type, name, str(key), attempts, waited, record=rec, checks=checks)
    except SourceUnavailable as exc:
        return AdapterResult("source_unavailable", f"{exc} on every attempt", doc_type, name, str(_val(fields, keyf)),
                             cfg["retry"]["max_attempts"], _total_wait(cfg["retry"]))
    except SourcePending:
        return AdapterResult("verification_pending", "authority has not answered yet", doc_type, name, str(_val(fields, keyf)), 1,
                             retry_after_s=cfg["pending_retry_after_s"])
    except AmbiguousMatch as exc:
        return AdapterResult("manual_review_required", str(exc), doc_type, name, str(_val(fields, keyf)), 1)
    except Exception as exc:                                    # fail safe: a broken adapter must not crash the pipeline
        return AdapterResult("manual_review_required", f"adapter error: {type(exc).__name__}: {exc}", doc_type, name)


def registry_facts(results: dict, source, inst_id: str, cfg: Optional[dict] = None, sleep: Callable[[float], None] = time.sleep) -> dict:
    """The registry view the rule engine reads, from the adapter results of one application (results: {doc_type: AdapterResult}).

    registry[kind] is the record (dict), {} when the registry answered "not found", and None when the answer is unknown (the
    document was not submitted, or the authority was unavailable / pending / ambiguous): a rule that needs it is not evaluable.
    Also: institution, society (the institution's own society), affiliations {course: approved_intake}, faculty_employment
    {faculty_id: current institution or None}, status {doc_type: adapter status}."""
    cfg = cfg or load_config()
    unknown = ("source_unavailable", "verification_pending", "manual_review_required")

    def rec(doc_type: str):
        r = results.get(doc_type)
        if r is None or r.status in unknown:
            return None
        return r.record or {}

    def one(name: str, key: str):
        try:
            rows, _, _ = call_with_retry(source, name, key, cfg["retry"], sleep, inst_id)
            return rows
        except (SourceUnavailable, SourcePending):
            return None
    inst = one("institution", inst_id)
    soc = one("society_by_id", inst[0]["society_id"]) if inst else None
    aff = one("university_by_inst", inst_id)
    fac = results.get("faculty_list")
    return {"institution": (inst[0] if inst else None), "society": (soc[0] if soc else None),
            "fire": rec("fire_cert"), "building": rec("building_plan"), "land": rec("land_doc"),
            "affiliations": ({r["course"]: r["approved_intake"] for r in aff} if aff is not None else None),
            "faculty_employment": (fac.record or {}).get("employment") if fac and fac.status in ("verified", "mismatch_detected") else None,
            "status": {dt: r.status for dt, r in results.items()}}


def _total_wait(retry: dict) -> float:
    return sum(min(retry["max_delay_s"], retry["base_delay_s"] * retry["factor"] ** k) for k in range(retry["max_attempts"] - 1))


def analyze(doc_type: str, fields: dict, application_date, inst_id: str, source, doc_id: str = "", cfg: Optional[dict] = None,
            sleep: Callable[[float], None] = time.sleep, result: Optional[AdapterResult] = None) -> list[Finding]:
    """Findings of a verification. result = an AdapterResult already obtained (no second lookup: scenario authorities count calls)."""
    r = result if result is not None else verify_document(doc_type, fields, application_date, inst_id, source, cfg, sleep)
    ev = {"status": r.status, "reason": r.reason, "source": r.source, "key": r.key, "attempts": r.attempts, "checks": r.checks}
    msg = {"mismatch_detected": f"The issuing registry does not agree with this document: {r.reason}.",
           "expired": f"The registry shows this document expired before the application date ({r.reason}).",
           "verification_pending": "The issuing authority has not answered yet; verification is pending.",
           "source_unavailable": "The issuing authority could not be reached after several attempts; check again later.",
           "manual_review_required": f"The registry check needs a person to look at it: {r.reason}."}
    if r.status == "mismatch_detected":
        return [Finding("registry_mismatch", MODULE, "hard", 2, 0.9, msg[r.status], doc_id, None, ev)]
    if r.status == "expired":
        return [Finding("registry_expired", MODULE, "soft", 2, 0.7, msg[r.status], doc_id, None, ev)]
    if r.status in ("verification_pending", "source_unavailable", "manual_review_required"):
        return [Finding(r.status, MODULE, "soft", 2, 0.3 if r.status != "manual_review_required" else 0.4, msg[r.status], doc_id, None, ev)]
    return [Finding("registry_verified" if r.status == "verified" else "registry_not_applicable", MODULE, "info", 2, 0.0,
                    "Registry record found and consistent." if r.status == "verified" else r.reason, doc_id, None, ev)]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.adapters")
    ap.add_argument("fields_json", help="JSON {key: value | {value,...}}"); ap.add_argument("--doc-type", required=True)
    ap.add_argument("--inst", required=True); ap.add_argument("--date", required=True); ap.add_argument("--world", required=True)
    a = ap.parse_args(argv)
    src = WorldSource(a.world)
    try:
        r = verify_document(a.doc_type, json.load(open(a.fields_json, encoding="utf-8")), a.date, a.inst, src, sleep=lambda s: None)
    finally:
        src.close()
    json.dump(r.to_json(), sys.stdout, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
