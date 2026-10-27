"""rule_import: a STRUCTURED rule-update PDF -> a checked change plan -> layered, versioned overrides of the norms and rule settings.

Input : a PDF with two kinds of ruled tables (found by their header row, in any order, across pages):
          Norms table  | Programme type | Norm  | Value | Handbook reference |   e.g. Engineering | students_per_faculty | 18 | Clause 5.1
          Rules table  | Rule           | Setting | Value | Handbook reference |   e.g. R001 | p | 0.8 | Clause 5.1
        optional header lines: "Norms version: <text>", "Effective from: YYYY-MM-DD", "Values validated against the handbook: yes|no".
        Settings of a rule: kind (hard|soft), severity (minor|major|critical), p (0-1), enabled (yes|no), handbook_ref (text).
        `python -m modules.rule_import --template out.pdf` writes this format filled with today's values, ready to edit.
Output: a Plan: every change with old value, new value and direction (increase / decrease / changed), rows that were rejected with page and
        reason, warnings, the new overrides document. `apply_changes` is pure; `RuleStore` keeps staged plans, the override file, and a numbered
        history (each version stores the PDF, the change log and the full overrides) so any version can be restored.
Layering: config/norms.yaml and config/rulebook.yaml stay the hand-written BASE; the PDF only writes numbers and settings into an overrides
        file (data directory). A value equal to the base drops its override, so "restore version 0" is exactly the shipped configuration.
Does NOT: read unstructured PDFs (a prose handbook; that needs the AI step, and says so), change rule expressions, add norms or programme types
        that the rulebook does not already use, or decide whether the numbers are right. Changes that can silently loosen or remove checks
        (large moves, a rule switched off, hard -> soft) are marked `needs_ack` and the caller must confirm them.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pymupdf
import yaml

MODULE = "rule_import"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
LARGE_CHANGE_PCT = 50.0
KINDS, SEVERITIES = ("hard", "soft"), ("minor", "major", "critical")
RULE_SETTINGS = ("kind", "severity", "p", "enabled", "handbook_ref")
PROGRAMME_ALIASES = {"management": "mba", "mba": "mba", "engg": "engineering", "engineering": "engineering", "polytechnic": "polytechnic", "diploma": "polytechnic"}
NORM_HEAD, RULE_HEAD = ("programmetype", "norm", "value"), ("rule", "setting", "value")


class RuleImportError(Exception):
    """The file cannot be used at all (not a PDF, no recognisable tables, unstructured)."""


# ----------------------------------------------------------------------------- reading the PDF
def _key(s) -> str:
    return re.sub(r"[^a-z]", "", str(s or "").lower())


def _cell(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _tables(page) -> list[list[list[str]]]:
    for strategy in ("lines", "text"):
        try:
            found = page.find_tables(strategy=strategy).tables
        except Exception:
            found = []
        # cell text is read from the page inside each cell rectangle: Table.extract() rewrites underscores ("a_b" -> "a b _")
        out = [[[_cell(page.get_text("text", clip=pymupdf.Rect(c)) if c else "") for c in row.cells] for row in t.rows] for t in found]
        out = [t for t in out if len(t) >= 2]
        if out:
            return out
    return []


def _columns(header: list[str]) -> Optional[tuple[str, dict]]:
    keys = [_key(h) for h in header]
    ref = next((i for i, k in enumerate(keys) if k.startswith(("handbook", "clause", "reference"))), None)
    for kind, need in (("norm", NORM_HEAD), ("rule", RULE_HEAD)):
        if all(n in keys for n in need):
            cols = {n: keys.index(n) for n in need}
            cols["ref"] = ref
            return kind, cols
    return None


def read_pdf(path: str | Path | bytes) -> dict:
    """{'meta': {...}, 'norm_rows': [...], 'rule_rows': [...]} with page numbers; raises RuleImportError when nothing structured is found."""
    try:
        doc = pymupdf.open(stream=path, filetype="pdf") if isinstance(path, (bytes, bytearray)) else pymupdf.open(path)
    except Exception as exc:
        raise RuleImportError(f"this file could not be opened as a PDF ({type(exc).__name__})")
    meta: dict = {}
    norm_rows, rule_rows = [], []
    last_kind = last_cols = None
    try:
        text = "\n".join(p.get_text() for p in doc)
        for pat, name in ((r"norms?\s+version\s*:\s*(.+)", "norms_version"), (r"effective\s+from\s*:\s*(\d{4}-\d{2}-\d{2})", "effective_from"),
                          (r"validated[^:\n]*:\s*(yes|no|true|false)", "validated")):
            m = re.search(pat, text, re.I)
            if m:
                meta[name] = m.group(1).strip()
        if "validated" in meta:
            meta["validated"] = meta["validated"].lower() in ("yes", "true")
        for pno, page in enumerate(doc, start=1):
            for tab in _tables(page):
                spec = _columns(tab[0])
                body = tab[1:]
                if spec:
                    last_kind, last_cols = spec
                elif last_cols and len(tab[0]) >= 3 and last_kind:         # a continued table: the header is not repeated
                    body = tab
                else:
                    continue
                for row in body:
                    if not any(row):
                        continue
                    get = lambda n: row[last_cols[n]] if last_cols.get(n) is not None and last_cols[n] < len(row) else ""
                    rec = {"page": pno, "ref": get("ref")}
                    if last_kind == "norm":
                        norm_rows.append({**rec, "programme": get("programmetype"), "norm": get("norm"), "value": get("value")})
                    else:
                        rule_rows.append({**rec, "rule": get("rule"), "setting": get("setting"), "value": get("value")})
    finally:
        doc.close()
    if not norm_rows and not rule_rows:
        raise RuleImportError("no rule tables were found. This tool reads PDFs laid out as the template (a Norms table and/or a Rules table with "
                              "their header rows). A prose or scanned handbook is an unstructured document: use \"Read it with AI\" in the rules tool (it needs an AI API key to be set up).")
    return {"meta": meta, "norm_rows": norm_rows, "rule_rows": rule_rows}


# ----------------------------------------------------------------------------- effective configuration
def empty_overrides() -> dict:
    return {"version": 0, "meta": {}, "norms": {}, "norm_refs": {}, "rules": {}}


def effective(base_rulebook: dict, base_norms_doc: dict, overrides: Optional[dict] = None) -> tuple[dict, dict]:
    """(rulebook, norms by programme type) = base with the overrides applied. Pure; the base objects are not modified."""
    ov = overrides or empty_overrides()
    rb, nd = copy.deepcopy(base_rulebook), copy.deepcopy(base_norms_doc)
    norms = nd["programme_types"]
    for prog, vals in (ov.get("norms") or {}).items():
        norms.setdefault(prog, {}).update(vals)
    rules = []
    for r in rb["rules"]:
        o = (ov.get("rules") or {}).get(r["id"], {})
        if o.get("enabled") is False:
            continue
        rules.append({**r, **{k: v for k, v in o.items() if k != "enabled"}})
    rb["rules"] = rules
    meta = ov.get("meta") or {}
    if base_norms_doc.get("norms_version"):
        rb.setdefault("norms_version", base_norms_doc["norms_version"])
    if meta.get("norms_version"):
        rb["norms_version"] = meta["norms_version"]
    if "validated" in meta:
        rb["norms_validated"] = bool(meta["validated"])
    return rb, norms


# ----------------------------------------------------------------------------- planning
def _num(s: str) -> Optional[float]:
    t = str(s).strip().replace(",", "")
    if t.endswith("%"):
        try:
            return float(t[:-1]) / 100
        except ValueError:
            return None
    return float(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def _rule_id(s: str) -> Optional[str]:
    m = re.fullmatch(r"r\D*0*(\d+)", str(s).strip(), re.I)
    return f"R{int(m.group(1)):03d}" if m else None


def _direction(old, new) -> str:
    if isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool):
        return "increase" if new > old else "decrease" if new < old else "same"
    return "changed" if old != new else "same"


def _pct(old, new) -> Optional[float]:
    if isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool) and old:
        return round(100.0 * (new - old) / abs(old), 1)
    return None


def _norm_value(raw: str, old):
    v = _num(raw)
    if v is None:
        return None, "write the value as a plain number (no units, ranges or ratios)"
    if v <= 0:
        return None, "the value must be greater than zero"
    if isinstance(old, int) and not isinstance(old, bool):
        if v != int(v):
            return None, f"this norm is a whole number (now {old})"
        return int(v), None
    return v, None


def _rule_value(setting: str, raw: str):
    s = raw.strip()
    if setting == "kind":
        return (s.lower(), None) if s.lower() in KINDS else (None, "kind must be hard or soft")
    if setting == "severity":
        return (s.lower(), None) if s.lower() in SEVERITIES else (None, "severity must be minor, major or critical")
    if setting == "p":
        v = _num(s)
        return (v, None) if v is not None and 0 < v <= 1 else (None, "p must be a number above 0 and at most 1 (or a percentage)")
    if setting == "enabled":
        t = s.lower()
        return (True, None) if t in ("yes", "true", "1", "on") else (False, None) if t in ("no", "false", "0", "off") else (None, "enabled must be yes or no")
    return (s, None) if s else (None, "the reference text is empty")


def build_plan(parsed: dict, base_rulebook: dict, base_norms_doc: dict, current: Optional[dict] = None, source: Optional[dict] = None) -> dict:
    """Compare the PDF with the CURRENT effective values. Returns the plan (JSON-safe)."""
    cur = current or empty_overrides()
    rb_now, norms_now = effective(base_rulebook, base_norms_doc, cur)
    base_rb, base_norms = effective(base_rulebook, base_norms_doc, None)
    rules_now = {r["id"]: r for r in rb_now["rules"]}
    all_rules = {r["id"]: r for r in base_rulebook["rules"]}
    new = copy.deepcopy(cur)
    new.setdefault("norm_refs", {}); new.setdefault("norms", {}); new.setdefault("rules", {})
    changes, rejected, warnings, unchanged = [], [], [], 0
    seen: dict = {}
    ref_done: set = set()
    handled: set = set()

    def reject(row, why):
        rejected.append({"page": row["page"], "row": " | ".join(str(row.get(k, "")) for k in ("programme", "norm", "rule", "setting", "value") if row.get(k)), "reason": why})

    # duplicates with different values would make the result depend on row order: reject both
    for kind, rows, keyf in (("norm", parsed["norm_rows"], lambda r: (PROGRAMME_ALIASES.get(_key(r["programme"]), _key(r["programme"])), re.sub(r"[^a-z0-9]+", "_", r["norm"].lower()).strip("_"))),
                             ("rule", parsed["rule_rows"], lambda r: (_rule_id(r["rule"]), _key(r["setting"])))):
        for r in rows:
            seen.setdefault((kind, keyf(r)), []).append(r)
    conflict = {k for k, v in seen.items() if len({x["value"] for x in v}) > 1}

    for r in parsed["norm_rows"]:
        prog = PROGRAMME_ALIASES.get(_key(r["programme"]), _key(r["programme"]))
        norm = re.sub(r"[^a-z0-9]+", "_", r["norm"].lower()).strip("_")
        if prog not in norms_now:
            reject(r, f"unknown programme type; known: {', '.join(sorted(norms_now))}"); continue
        if norm not in norms_now[prog]:
            reject(r, f"unknown norm for {prog}; known: {', '.join(sorted(norms_now[prog]))}"); continue
        if ("norm", (prog, norm)) in conflict:
            reject(r, "the same norm appears twice with different values"); continue
        if ("norm", prog, norm) in handled:
            continue
        handled.add(("norm", prog, norm))
        old = norms_now[prog][norm]
        val, why = _norm_value(r["value"], old)
        if why:
            reject(r, why); continue
        if _direction(old, val) == "same":
            unchanged += 1
            if r["ref"] and new["norm_refs"].get(f"{prog}.{norm}") != r["ref"]:
                new["norm_refs"][f"{prog}.{norm}"] = r["ref"]
            continue
        pct = _pct(old, val)
        ack = [f"moves by {pct:+.0f}% (more than {LARGE_CHANGE_PCT:.0f}%)"] if pct is not None and abs(pct) >= LARGE_CHANGE_PCT else []
        changes.append({"target": "norm", "programme_type": prog, "key": norm, "label": f"{prog}: {norm.replace('_', ' ')}", "old": old, "new": val,
                        "direction": _direction(old, val), "pct": pct, "ref": r["ref"], "page": r["page"], "quote": r.get("quote", ""), "needs_ack": ack})
        if val == base_norms[prog][norm]:
            new["norms"].get(prog, {}).pop(norm, None)
        else:
            new["norms"].setdefault(prog, {})[norm] = val
        if r["ref"]:
            new["norm_refs"][f"{prog}.{norm}"] = r["ref"]

    for r in parsed["rule_rows"]:
        rid = _rule_id(r["rule"])
        setting = {"handbookref": "handbook_ref", "reference": "handbook_ref", "clause": "handbook_ref"}.get(_key(r["setting"]), _key(r["setting"]))
        if rid is None or rid not in all_rules:
            reject(r, f"unknown rule; known: {', '.join(sorted(all_rules))}"); continue
        if setting not in RULE_SETTINGS:
            reject(r, f"unknown setting; use {', '.join(RULE_SETTINGS)}"); continue
        if ("rule", (rid, _key(r["setting"]))) in conflict:
            reject(r, "the same setting appears twice with different values"); continue
        if ("rule", rid, setting) in handled:
            continue
        handled.add(("rule", rid, setting))
        val, why = _rule_value(setting, r["value"])
        if why:
            reject(r, why); continue
        shipped = all_rules[rid]
        merged = {**shipped, **cur.get("rules", {}).get(rid, {})}          # the values in force, including a rule that is currently switched off
        old = (rid in rules_now) if setting == "enabled" else merged.get(setting)
        ref = r["ref"]
        if _direction(old, val) == "same":
            unchanged += 1
        else:
            ack = []
            if setting == "enabled" and val is False:
                ack.append("switches this check off")
            if setting == "kind" and old == "hard" and val == "soft":
                ack.append("turns a hard check into a soft one")
            pct = _pct(old, val)
            if setting == "p" and pct is not None and abs(pct) >= LARGE_CHANGE_PCT:
                ack.append(f"moves by {pct:+.0f}% (more than {LARGE_CHANGE_PCT:.0f}%)")
            changes.append({"target": "rule", "rule": rid, "key": setting, "label": f"{rid} {shipped['name'].replace('_', ' ')}: {setting.replace('_', ' ')}",
                            "old": old, "new": val, "direction": _direction(old, val), "pct": pct, "ref": ref, "page": r["page"], "quote": r.get("quote", ""), "needs_ack": ack})
            o = new["rules"].setdefault(rid, {})
            if val == (True if setting == "enabled" else shipped.get(setting)):
                o.pop(setting, None)
            else:
                o[setting] = val
        if ref and setting != "handbook_ref" and merged.get("handbook_ref") != ref and rid not in ref_done:
            ref_done.add(rid)
            changes.append({"target": "rule", "rule": rid, "key": "handbook_ref", "label": f"{rid} {shipped['name'].replace('_', ' ')}: handbook ref",
                            "old": merged.get("handbook_ref"), "new": ref, "direction": "changed", "pct": None, "ref": ref, "page": r["page"], "quote": r.get("quote", ""), "needs_ack": []})
            new["rules"].setdefault(rid, {})["handbook_ref"] = ref
    new["rules"] = {k: v for k, v in new["rules"].items() if v}
    new["norms"] = {k: v for k, v in new["norms"].items() if v}

    meta = parsed["meta"]
    if meta.get("validated") is True:
        warnings.append("This file states that the values are validated against the handbook. That turns the 'illustrative norms' notice off for every case.")
    if "validated" in meta or "norms_version" in meta or "effective_from" in meta:
        new["meta"] = {**(new.get("meta") or {}), **{k: v for k, v in meta.items()}}
    if rejected:
        warnings.append(f"{len(rejected)} row(s) were not used; see the list. Everything else can still be applied.")
    if not changes and not rejected and not meta:
        warnings.append("Every value in this file already matches the current rules; nothing to change.")
    pct_changed = {"increase": sum(c["direction"] == "increase" for c in changes), "decrease": sum(c["direction"] == "decrease" for c in changes),
                   "changed": sum(c["direction"] == "changed" for c in changes)}
    return {"meta": meta, "changes": changes, "rejected": rejected, "warnings": warnings, "unchanged": unchanged, "counts": pct_changed,
            "yaml": yaml.safe_dump(new, sort_keys=False, allow_unicode=True),
            "needs_ack": [{"label": c["label"], "why": w} for c in changes for w in c["needs_ack"]], "new_overrides": new, "source": source or {}}


# ----------------------------------------------------------------------------- template / writer
def write_rule_pdf(path: str | Path, meta: dict, norm_rows: list[list], rule_rows: list[list]) -> None:
    """Write the structured format (ruled tables, repeated headers). Used for the downloadable template and by the tests."""
    doc = pymupdf.open()
    widths = {"norm": [110, 170, 90, 145], "rule": [90, 120, 120, 185]}
    heads = {"norm": ["Programme type", "Norm", "Value", "Handbook reference"], "rule": ["Rule", "Setting", "Value", "Handbook reference"]}
    state = {"page": None, "y": 0.0}

    def new_page():
        state["page"], state["y"] = doc.new_page(width=595, height=842), 50.0

    def line(txt, size=10, bold=False, gap=16):
        if state["y"] > 780:
            new_page()
        state["page"].insert_text((40, state["y"]), txt, fontsize=size, fontname="hebo" if bold else "helv")
        state["y"] += gap

    def table(kind, rows):
        def row(cells, head=False):
            if state["y"] + 20 > 800:
                new_page(); row(heads[kind], True)
            x = 40.0
            for w, c in zip(widths[kind], cells):
                r = pymupdf.Rect(x, state["y"], x + w, state["y"] + 20)
                state["page"].draw_rect(r, color=(0, 0, 0), fill=(0.9, 0.93, 0.97) if head else None, width=0.6)
                state["page"].insert_text((x + 4, state["y"] + 14), str(c)[:42], fontsize=9, fontname="hebo" if head else "helv")
                x += w
            state["y"] += 20
        row(heads[kind], True)
        for r in rows:
            row(r)
        state["y"] += 18

    new_page()
    line("RULE UPDATE", 16, True, 24)
    if "norms_version" in meta:
        line(f"Norms version: {meta['norms_version']}")
    if "effective_from" in meta:
        line(f"Effective from: {meta['effective_from']}")
    if "validated" in meta:
        line(f"Values validated against the handbook: {'yes' if meta['validated'] else 'no'}")
    state["y"] += 8
    if norm_rows:
        line("Norms", 12, True, 20); table("norm", norm_rows)
    if rule_rows:
        line("Rules", 12, True, 20); table("rule", rule_rows)
    doc.save(str(path), deflate=True, garbage=3)
    doc.close()


def template_rows(base_rulebook: dict, base_norms_doc: dict, overrides: Optional[dict] = None) -> tuple[list, list]:
    """Rows of the editable template: every norm and every rule (also the ones switched off, shown as enabled = no) with the values in force."""
    ov = overrides or empty_overrides()
    rb, norms = effective(base_rulebook, base_norms_doc, ov)
    on = {r["id"] for r in rb["rules"]}
    refs = ov.get("norm_refs") or {}
    nrows = [[p, k, v, refs.get(f"{p}.{k}", "")] for p, vals in norms.items() for k, v in vals.items()]
    rrows = []
    for shipped in base_rulebook["rules"]:
        r = {**shipped, **(ov.get("rules") or {}).get(shipped["id"], {})}
        ref = "" if r.get("handbook_ref") in (None, "TODO") else r["handbook_ref"]
        rrows += [[r["id"], "kind", r.get("kind", "soft"), ref], [r["id"], "severity", r.get("severity", "major"), ""],
                  [r["id"], "p", r.get("p", 0.7), ""], [r["id"], "enabled", "yes" if r["id"] in on else "no", ""]]
    return nrows, rrows


# ----------------------------------------------------------------------------- storage with history
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RuleStore:
    """Directory layout: current.yaml (overrides in force; absent = shipped config), versions/vNNNN.{yaml,json,pdf}, staged/<id>.{json,pdf}."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        for d in ("versions", "staged"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def current(self) -> dict:
        p = self.root / "current.yaml"
        return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else empty_overrides()

    def version(self) -> int:
        return int(self.current().get("version", 0))

    def history(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "versions").glob("v*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: d[k] for k in ("version", "kind", "at", "by", "source_name", "sha256", "n_changes", "counts", "restored") if k in d})
        return out

    def stage(self, plan: dict, pdf: bytes, now: Optional[str] = None) -> str:
        sid = hashlib.sha256(pdf).hexdigest()[:16]
        plan = {**plan, "id": sid, "based_on_version": self.version(), "staged_at": now or _now()}
        (self.root / "staged" / f"{sid}.pdf").write_bytes(pdf)
        (self.root / "staged" / f"{sid}.json").write_text(json.dumps(plan, indent=1, default=str), encoding="utf-8")
        return sid

    def staged(self, sid: str) -> Optional[dict]:
        p = self.root / "staged" / f"{re.sub(r'[^0-9a-f]', '', sid)}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def _commit(self, overrides: dict, entry: dict, pdf: Optional[bytes]) -> int:
        n = self.version() + 1
        hist = sorted((self.root / "versions").glob("v*.json"))
        n = max([n] + [int(p.stem[1:]) + 1 for p in hist])
        overrides = {**overrides, "version": n}
        text = yaml.safe_dump(overrides, sort_keys=False, allow_unicode=True)
        (self.root / "versions" / f"v{n:04d}.yaml").write_text(text, encoding="utf-8")
        (self.root / "versions" / f"v{n:04d}.json").write_text(json.dumps({**entry, "version": n}, indent=1, default=str), encoding="utf-8")
        if pdf is not None:
            (self.root / "versions" / f"v{n:04d}.pdf").write_bytes(pdf)
        (self.root / "current.yaml").write_text(text, encoding="utf-8")
        return n

    def apply(self, sid: str, by: str, acknowledged: bool, now: Optional[str] = None) -> dict:
        plan = self.staged(sid)
        if plan is None:
            raise RuleImportError("this preview no longer exists; upload the file again")
        if plan["based_on_version"] != self.version():
            raise RuleImportError("the rules changed after this preview was made; upload the file again so it is compared with the current values")
        if plan["needs_ack"] and not acknowledged:
            raise RuleImportError("this update contains changes that need your explicit confirmation")
        if not plan["changes"] and not plan["meta"]:
            raise RuleImportError("there is nothing to apply")
        pdf = (self.root / "staged" / f"{plan['id']}.pdf").read_bytes()
        entry = {"kind": "apply", "at": now or _now(), "by": by, "source_name": plan.get("source", {}).get("name"), "sha256": hashlib.sha256(pdf).hexdigest(),
                 "n_changes": len(plan["changes"]), "counts": plan["counts"], "changes": plan["changes"], "meta": plan["meta"], "acknowledged": plan["needs_ack"]}
        return {"version": self._commit(plan["new_overrides"], entry, pdf), "changes": len(plan["changes"])}

    def revert(self, version: int, by: str, now: Optional[str] = None) -> dict:
        if version == 0:
            target = empty_overrides()
        else:
            p = self.root / "versions" / f"v{int(version):04d}.yaml"
            if not p.exists():
                raise RuleImportError(f"there is no version {version}")
            target = yaml.safe_load(p.read_text(encoding="utf-8"))
        entry = {"kind": "revert", "at": now or _now(), "by": by, "restored": int(version), "source_name": None, "n_changes": 0}
        return {"version": self._commit(target, entry, None), "restored": int(version)}


def main(argv: Optional[list[str]] = None) -> int:
    from modules import rules as _rules                           # CLI convenience only; the library functions above import nothing
    ap = argparse.ArgumentParser(prog="python -m modules.rule_import", description="Preview a structured rule PDF against the shipped rules.")
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--template", metavar="OUT.pdf", help="write the current rules in the structured format")
    a = ap.parse_args(argv)
    rb, nd = _rules.load_rulebook(), yaml.safe_load(open(CONFIG_DIR / "norms.yaml", encoding="utf-8"))
    if a.template:
        nrows, rrows = template_rows(rb, nd)
        write_rule_pdf(a.template, {"norms_version": rb.get("norms_version", ""), "validated": bool(rb.get("norms_validated"))}, nrows, rrows)
        print(f"wrote {a.template}")
        return 0
    if not a.pdf:
        ap.error("give a PDF or --template")
    try:
        plan = build_plan(read_pdf(a.pdf), rb, nd)
    except RuleImportError as exc:
        print(f"cannot read: {exc}", file=sys.stderr)
        return 2
    json.dump({k: plan[k] for k in ("meta", "changes", "rejected", "warnings", "unchanged", "counts", "needs_ack")}, sys.stdout, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
