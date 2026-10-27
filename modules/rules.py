"""Rule engine: evaluate a YAML rulebook against canonical facts with a whitelist-only expression evaluator (simpleeval).

Input : facts = build_facts(...) (plain JSON: ISO date strings, numbers, strings, lists) and a rulebook (config/rulebook.yaml).
Output: evaluate() -> list[RuleResult] {id, name, result, detail, tier, severity, missing, error} with result one of
          pass | fail | not_applicable (applies_if false) | not_evaluable (a required fact is missing/None, or the expression
          could not be computed: never a guess)
        analyze() -> list[Finding]: a failed rule is a Finding with the rule id (R004, ...), kind/p/tier from the rulebook
        (registry- and certificate-based rules hard, norm-based rules soft while norms_validated is false); not_evaluable
        is a soft `rule_not_evaluable` (a person decides); passing rules produce nothing; one info `rules_ok` if all pass.
Safety: expressions run in simpleeval with no builtins, a fixed function whitelist (ceil, floor, abs, len, sum, min, max, all, any,
        exists, normalize, str, int, float, round), no attribute access to private names, simpleeval's size/iteration limits, and
        a per-expression length cap. A rule that raises is not_evaluable with the error text; it never crashes the run.
simulate(old_rulebook, new_rulebook, facts_by_case) is the rule-change impact simulator: it re-runs both rulebooks over stored
        canonical facts (no PDFs touched) and lists every case whose result changes.
Does NOT: fetch registry data (adapters), read documents (extract), or decide anything - a failed rule is a reason to look.
        The norms are illustrative (config/norms.yaml) and every handbook_ref is TODO until the regulator fills them in.
CLI   : python -m modules.rules FACTS.json [--rulebook r.yaml]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from simpleeval import EvalWithCompoundTypes

from contracts import Finding

MODULE = "rules"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_RULEBOOK = CONFIG_DIR / "rulebook.yaml"
DEFAULT_NORMS = CONFIG_DIR / "norms.yaml"
RESULTS = ("pass", "fail", "not_applicable", "not_evaluable")


def load_rulebook(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_RULEBOOK, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_norms(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_NORMS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["programme_types"]


# ----------------------------------------------------------------------------- facts
def normalize(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s if s is not None else "").lower().replace("&", "and"))


MIN_OCR_CONF = 0.6


def _val(fields: dict, key: str, min_conf: float = None):
    """Field value, or None when absent / empty / an OCR read below min_conf (an unreliable read is not a fact: the rule that
    needs it becomes not_evaluable instead of accusing the applicant of what a blurred scan said). Text-layer values are exact
    and always kept, including covered-text fields whose conf is lowered on purpose."""
    v = (fields or {}).get(key)
    if isinstance(v, dict):
        if v.get("source") == "ocr" and v.get("conf", 1.0) < (MIN_OCR_CONF if min_conf is None else min_conf):
            return None
        v = v.get("value")
    return None if v in ("", None) else v


def _iso(s) -> Optional[str]:
    from datetime import datetime
    s = str(s or "").strip()
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %B %Y", "%d %b %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def _num(s) -> Optional[float]:
    m = re.sub(r"[^\d.]", "", str(s if s is not None else ""))
    try:
        return float(m)
    except ValueError:
        return None


def _int(s) -> Optional[int]:
    n = _num(s)
    return None if n is None else int(round(n))


def _money(s) -> Optional[int]:
    if s in (None, ""):
        return None
    t = str(s)
    digits = re.sub(r"\D", "", re.sub(r"(?i)\brs\.?", "", t))        # "Rs. 4,96,03,427" -> 49603427 (Indian grouping, no decimals)
    if not digits:
        return None
    neg = "-" in t or ("(" in t and ")" in t)
    return -int(digits) if neg else int(digits)


def _rows(fields: dict, prefix: str) -> list[dict]:
    """Table rows {col: value} of `prefix[i].col` keys, ordered by row index. A row with an unreadable cell is dropped whole
    (a half-read row would give a wrong total)."""
    rows: dict[int, dict] = {}
    bad: set[int] = set()
    for k in (fields or {}):
        m = re.fullmatch(rf"{re.escape(prefix)}\[(\d+)\]\.(\w+)", k)
        if m:
            v = _val(fields, k)
            if v is None:
                bad.add(int(m.group(1)))
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = v
    return [rows[i] for i in sorted(rows) if i not in bad]


def build_facts(application: dict, docs: dict, registry: Optional[dict] = None, norms: Optional[dict] = None,
                required_docs: Optional[list] = None) -> dict:
    """Canonical facts of ONE application.

    application = {inst_id, date, type (existing/new), programme_type}; docs = {doc_type: extract fields}; registry = adapters'
    registry view (None for a missing source). Everything is plain JSON (dates are ISO strings)."""
    norms = norms if norms is not None else load_norms()
    f = {"application": {"inst_id": application["inst_id"], "date": _iso(application["date"]), "type": application.get("type"),
                         "programme_type": application.get("programme_type")},
         "norms": norms.get(application.get("programme_type"), {}),
         "submitted_docs": sorted(docs), "required_docs": list(required_docs or load_rulebook()["required_documents"]),
         "registry": registry or {}}
    ff = docs.get("faculty_list")
    f["faculty_ids"] = [r["faculty_id"] for r in _rows(ff, "faculty") if r.get("faculty_id")] if ff else None
    f["faculty_count"] = len(_rows(ff, "faculty")) if ff else None
    af = docs.get("affidavit")
    f["applied"] = [{"course": r.get("course"), "level": r.get("level"), "intake": _int(r.get("intake"))} for r in _rows(af, "applied")] if af else []
    inf = docs.get("infra_statement")
    f["infra"] = {"total": _int(_val(inf, "area.total.a")), "pcs": _int(_val(inf, "pcs_available")),
                  "instructional": _int(_val(inf, "area.instructional.a")), "administrative": _int(_val(inf, "area.administrative.a")),
                  "amenities": _int(_val(inf, "area.amenities.a"))} if inf else {}
    fc, bp = docs.get("fire_cert"), docs.get("building_plan")
    f["fire"] = {"cert_no": _val(fc, "cert_no"), "valid_to": _iso(_val(fc, "valid_to")), "issued_on": _iso(_val(fc, "issued_on"))} if fc else {}
    f["building"] = {"cert_no": _val(bp, "cert_no"), "valid_to": _iso(_val(bp, "valid_to")), "issued_on": _iso(_val(bp, "issued_on")),
                     "builtup_sqm": _int(_val(bp, "sanctioned_builtup_sqm"))} if bp else {}
    ld = docs.get("land_doc")
    if ld:
        raw = str(_val(ld, "tenure") or "")
        f["land"] = {"survey_no": _val(ld, "survey_no"), "tenure": "lease" if "lease" in raw.lower() else "owned", "tenure_raw": raw,
                     "lease_years": _int(_val(ld, "lease_years")), "area_acres": _num(_val(ld, "area_acres"))}
    else:
        f["land"] = {}
    fin = docs.get("financial")
    f["fin"] = {"income": _money(_val(fin, "fin.income.a")), "expenditure": _money(_val(fin, "fin.expenditure.a")),
                "surplus": _money(_val(fin, "fin.surplus.a"))} if fin else {}
    f["institution_names"] = {dt: _val(d, "institution_name") for dt, d in docs.items() if _val(d, "institution_name")}
    f["society_names"] = {dt: _val(d, "society_name") for dt, d in docs.items() if _val(d, "society_name")}
    f["adapter_status"] = (registry or {}).get("status", {})
    return f


# ----------------------------------------------------------------------------- evaluator
class _Obj:
    """Read-only attribute view over dicts/lists for expressions (missing attribute -> AttributeError -> not_evaluable)."""

    def __init__(self, v):
        object.__setattr__(self, "_v", v)

    def __getattr__(self, k):
        v = object.__getattribute__(self, "_v")
        if k.startswith("_") or not isinstance(v, dict) or k not in v:
            raise AttributeError(k)
        return _wrap(v[k])

    def __getitem__(self, k):
        return _wrap(object.__getattribute__(self, "_v")[k])

    def __contains__(self, k):
        return k in object.__getattribute__(self, "_v")

    def __iter__(self):
        return iter(object.__getattribute__(self, "_v"))

    def __len__(self):
        return len(object.__getattribute__(self, "_v"))

    def __bool__(self):
        return bool(object.__getattribute__(self, "_v"))

    def __eq__(self, o):
        return object.__getattribute__(self, "_v") == (object.__getattribute__(o, "_v") if isinstance(o, _Obj) else o)


def _wrap(v):
    if isinstance(v, dict):
        return _Obj(v)
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


def _exists(x) -> bool:
    return bool(x)


def _tokens(s, abbr: dict) -> list[str]:
    words = re.findall(r"[a-z0-9]+", str(s if s is not None else "").lower().replace("&", " and "))
    return [abbr.get(w, w) for w in words]


def make_similar(abbr: dict, threshold: float, generic: Optional[set] = None):
    """similar(a, b): do two names denote the same institution / society?

    Abbreviations are expanded first (Inst. -> institute, Coll. -> college ...). Then the DISTINCTIVE words (everything that is
    not a generic word such as institute, engineering, technology, society, trust) are compared: they must be at least
    `threshold` alike. Comparing whole strings would call "Vidya Sagar Institute of Engineering & Technology" and "Hoysala
    Institute of Engineering & Technology" the same, because the generic words dominate. When one side has no distinctive word
    left (a truncated read such as "of Technology") the names cannot be judged and are NOT reported as different."""
    import difflib
    generic = generic or set()

    def similar(a, b) -> bool:
        ta, tb = _tokens(a, abbr), _tokens(b, abbr)
        if not ta or not tb:
            return False
        da, db = [t for t in ta if t not in generic], [t for t in tb if t not in generic]
        if not da or not db:
            return True
        x, y = "".join(da), "".join(db)
        return x == y or difflib.SequenceMatcher(None, x, y).ratio() >= threshold
    return similar


FUNCTIONS = {"ceil": math.ceil, "floor": math.floor, "abs": abs, "len": len, "sum": sum, "min": min, "max": max,
             "all": all, "any": any, "exists": _exists, "normalize": normalize, "similar": None, "str": str, "int": int, "float": float, "round": round}


def _evaluator(names: dict, rb: Optional[dict] = None) -> EvalWithCompoundTypes:
    fn = dict(FUNCTIONS)
    rb = rb or {}
    fn["similar"] = make_similar(rb.get("abbreviations", {}), rb.get("name_similarity", 0.85), set(rb.get("generic_name_words", [])))
    return EvalWithCompoundTypes(functions=fn, names=dict(names))


def _path(names: dict, dotted: str):
    cur = names
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


@dataclass
class RuleResult:
    id: str
    name: str
    result: str
    detail: str = ""
    tier: int = 3
    severity: str = ""
    missing: list = field(default_factory=list)
    error: Optional[str] = None

    def to_json(self) -> dict:
        return asdict(self)


def _names(facts: dict, rb: dict) -> dict:
    names = {k: (_wrap(v) if isinstance(v, (dict, list)) else v) for k, v in facts.items()}
    plain = dict(facts)
    ev = _evaluator(names, rb)
    for k, expr in (rb.get("derived") or {}).items():
        try:
            val = ev.eval(expr)
            val = list(val) if isinstance(val, list) else val
        except Exception:
            val = None
        names[k] = val; plain[k] = val
        ev.names = dict(names)
    names["_plain"] = plain
    return names


def evaluate(facts: dict, rulebook: Optional[dict] = None) -> list[RuleResult]:
    rb = rulebook or load_rulebook()
    names = _names(facts, rb)
    plain = names.pop("_plain")
    ev = _evaluator(names, rb)
    cap = rb.get("max_expression_chars", 600)
    out = []
    for r in rb["rules"]:
        base = dict(id=r["id"], name=r["name"], tier=r["tier"], severity=r.get("severity", ""))
        try:
            for e in (r.get("applies_if"), r["expr"], r.get("detail")):
                if e and len(e) > cap:
                    raise ValueError(f"expression longer than {cap} characters")
            if r.get("applies_if"):
                try:
                    if not ev.eval(r["applies_if"]):
                        out.append(RuleResult(**base, result="not_applicable")); continue
                except Exception:
                    out.append(RuleResult(**base, result="not_evaluable", missing=["applies_if"])); continue
            missing = [p for p in r.get("requires", []) if _path(plain, p) is None]
            if missing:
                out.append(RuleResult(**base, result="not_evaluable", missing=missing)); continue
            ok = ev.eval(r["expr"])
            if ok is None:
                out.append(RuleResult(**base, result="not_evaluable", error="expression gave no value")); continue
            detail = ""
            if not ok and r.get("detail"):
                try:
                    detail = str(ev.eval(r["detail"]))
                except Exception:
                    detail = ""
            out.append(RuleResult(**base, result="pass" if ok else "fail", detail=detail))
        except Exception as exc:                       # fail safe: a broken rule is not_evaluable, never a crash or a guess
            out.append(RuleResult(**base, result="not_evaluable", error=f"{type(exc).__name__}: {exc}"))
    return out


def analyze(facts: dict, rulebook: Optional[dict] = None, doc_id: str = "") -> list[Finding]:
    rb = rulebook or load_rulebook()
    by_id = {r["id"]: r for r in rb["rules"]}
    out: list[Finding] = []
    for res in evaluate(facts, rb):
        r = by_id[res.id]
        if res.result == "fail":
            kind = r.get("kind", "soft")
            out.append(Finding(res.id, MODULE, kind, r["tier"], r.get("p", 0.7),
                               r["message"].format(detail=res.detail or res.name.replace("_", " ")), doc_id, None,
                               {"rule": res.name, "detail": res.detail, "severity": r.get("severity"), "handbook_ref": r.get("handbook_ref"),
                                "norms_validated": rb.get("norms_validated")}))
        elif res.result == "not_evaluable":
            out.append(Finding("rule_not_evaluable", MODULE, "soft", 3, 0.2,
                               f"Rule {res.id} ({res.name.replace('_', ' ')}) could not be checked"
                               + (f": missing {', '.join(res.missing)}" if res.missing else "") + ".", doc_id, None,
                               {"rule_id": res.id, "missing": res.missing, "error": res.error}))
    if not out:
        out.append(Finding("rules_ok", MODULE, "info", 3, 0.0, "All rules pass.", doc_id))
    return out


def simulate(old_rulebook: dict, new_rulebook: dict, facts_by_case: dict[str, dict]) -> dict:
    """Rule-change impact: re-run two rulebooks over stored canonical facts. Returns
    {"changed": [{case, rule, old, new}], "by_rule": {rule: {old_result->new_result: n}}, "cases": n}."""
    changed, by_rule = [], {}
    for case, facts in facts_by_case.items():
        a = {r.id: r.result for r in evaluate(facts, old_rulebook)}
        b = {r.id: r.result for r in evaluate(facts, new_rulebook)}
        for rid in sorted(set(a) | set(b)):
            if a.get(rid) != b.get(rid):
                changed.append({"case": case, "rule": rid, "old": a.get(rid), "new": b.get(rid)})
                by_rule.setdefault(rid, {}).setdefault(f"{a.get(rid)}->{b.get(rid)}", 0)
                by_rule[rid][f"{a.get(rid)}->{b.get(rid)}"] += 1
    return {"changed": changed, "by_rule": by_rule, "cases": len(facts_by_case)}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.rules")
    ap.add_argument("facts_json"); ap.add_argument("--rulebook")
    a = ap.parse_args(argv)
    rb = load_rulebook(a.rulebook)
    facts = json.load(open(a.facts_json, encoding="utf-8"))
    json.dump({"results": [r.to_json() for r in evaluate(facts, rb)], "findings": [f.to_json() for f in analyze(facts, rb)]},
              sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
