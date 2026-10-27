"""Pipeline: chains intake -> perspective -> quality -> extract -> forensics -> QR -> dedup -> adapters -> rules -> score.

This is the ONE module that imports the others (it is the composition layer; the modules themselves stay independent).

Input : an application {inst_id, application_date, application_type, programme_type, name?} and its uploads
        [{doc_id, doc_type, path}], plus a registry Source (adapters), issuer keys (QR), and optional shared state:
        a dedup Index (grows as applications are processed) and an AuditLog.
Output: process_application(...) -> result dict
          {mode, app, documents:[{doc_id, doc_type, kind, source, fields, adapter, findings}], rules:[...], findings:[...all...],
           score:{score, category, reasons}, review:{double_review}, facts, generated_at}
        client_view(result)  what the APPLICANT portal may show: fixable problems only, in plain words, plus the pre-fill values
                             (never scores, categories, forensic or registry findings, covered-text evidence, other institutions)
        staff_view(result)   everything, for the review dashboard
        build_evidence(result, out_pdf) the staff evidence PDF
Modes: full (a submission) and dry_run (applicant pre-check): dry_run runs only intake, perspective, quality, extract, layout, type
        check and the printed-date / missing-document rules; it touches no registry, no dedup index, no audit log and its client view
        is built the same way, so the forensic detectors are never exercised or exposed.
Robustness: every step runs inside a guard; a step that raises becomes an info finding `step_failed` and the pipeline continues.
        Nothing reads the wall clock (`now` is a parameter).
Does NOT: store anything (the API does), decide anything (a score and a category are advice), or retry registries beyond adapters' policy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

import yaml

from contracts import Finding
from modules import adapters, dedup, ela, evidence_pdf, extract, pdf_forensics, perspective, qr_verify, quality, rules, score, templates_build
from modules import review as review_mod

MODULE = "pipeline"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "pipeline.yaml"


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _f(id_, kind, tier, p, msg, doc_id, ev=None) -> Finding:
    return Finding(id_, MODULE, kind, tier, p, msg, doc_id, None, ev or {})


# ----------------------------------------------------------------------------- intake
def intake(path: str | Path, cfg: dict) -> tuple[Optional[str], list[Finding]]:
    """('pdf' | 'image' | None, findings). Decides by content, not by file extension."""
    p = Path(path)
    doc_id = p.stem
    try:
        if not p.is_file():
            return None, [_f("file_unreadable", "soft", 3, 0.5, "The file could not be found or opened.", doc_id)]
        if p.stat().st_size > cfg["max_file_mb"] * 1024 * 1024:
            return None, [_f("file_too_large", "soft", 3, 0.5, f"The file is larger than {cfg['max_file_mb']} MB.", doc_id)]
        head = p.read_bytes()[:16]
        if head.startswith(b"%PDF"):
            import pymupdf
            with pymupdf.open(str(p)) as d:
                if d.needs_pass:
                    return None, [_f("file_unreadable", "soft", 3, 0.5, "The PDF is password protected.", doc_id)]
                if d.page_count == 0:
                    return None, [_f("file_unreadable", "soft", 3, 0.5, "The PDF has no pages.", doc_id)]
            return "pdf", []
        if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"BM")) or (head[:4] == b"RIFF" and p.read_bytes()[8:12] == b"WEBP"):
            import cv2
            if cv2.imread(str(p), cv2.IMREAD_COLOR) is None:
                return None, [_f("file_unreadable", "soft", 3, 0.5, "The image could not be decoded.", doc_id)]
            return "image", []
        return None, [_f("file_unreadable", "soft", 3, 0.5, "This file type is not supported. Please upload a PDF or a photo (JPG, PNG, TIFF).", doc_id)]
    except Exception as exc:
        return None, [_f("file_unreadable", "soft", 3, 0.5, "The file could not be read.", doc_id, {"error": f"{type(exc).__name__}: {exc}"})]


# ----------------------------------------------------------------------------- the pipeline
class Pipeline:
    def __init__(self, source=None, issuer_keys: Optional[dict] = None, cfg: Optional[dict] = None, templates: Optional[dict] = None,
                 rulebook: Optional[dict] = None, norms: Optional[dict] = None, index: Optional[dedup.Index] = None,
                 audit=None, work_dir: Optional[str | Path] = None, sleep: Callable[[float], None] = lambda s: None):
        self.cfg = cfg or load_config()
        self.source, self.keys = source, issuer_keys or {}
        self.templates = templates if templates is not None else extract.load_templates()
        self.rulebook = rulebook or rules.load_rulebook()
        self.norms = norms if norms is not None else rules.load_norms()
        self.index = index if index is not None else dedup.Index(dedup.load_config())
        self.audit, self.sleep = audit, sleep
        self.work_dir = Path(work_dir) if work_dir else None
        if self.work_dir:
            self.work_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers
    def _guard(self, name: str, doc_id: str, fn) -> list[Finding]:
        try:
            out = fn()
            return list(out) if out is not None else []
        except Exception as exc:
            return [_f("step_failed", "info", 3, 0.0, f"The '{name}' check could not be completed.", doc_id,
                       {"step": name, "error": f"{type(exc).__name__}: {exc}"})]

    def _coverage(self, fields: dict, doc_type: str) -> float:
        t = self.templates.get(doc_type)
        req = [k for k, v in (t or {}).get("scalars", {}).items() if not v.get("optional") and not v.get("y_variable") and k not in ("qr",)]
        if not req:
            return 1.0
        return sum(1 for k in req if k in fields and fields[k].get("conf", 1.0) >= 0.5) / len(req)

    # -- one document
    def process_document(self, app: dict, up: dict, dry_run: bool = False) -> dict:
        did, dt, path = up["doc_id"], up["doc_type"], up["path"]
        fnd: list[Finding] = []
        res = {"doc_id": did, "doc_type": dt, "kind": None, "source": None, "fields": {}, "adapter": None, "findings": fnd, "path": str(path)}
        kind, f = intake(path, self.cfg)
        for x in f:
            x.doc_id = did
        fnd += f
        res["kind"] = kind
        if kind is None:
            return res
        apath = path
        if kind == "image":
            out = (self.work_dir / f"{did}_flat.png") if self.work_dir else None
            pf = self._guard("perspective", did, lambda: perspective.analyze(path, did, out_path=out))
            fnd += pf
            if out is not None and any(x.id == "perspective_corrected" for x in pf) and Path(out).exists():
                apath = out
        fnd += self._guard("quality", did, lambda: quality.analyze(apath, did, doc_type=dt))
        pair = [None]

        def _extract():
            pair[0] = extract.extract(apath, did, dt, self.templates)
            return extract.analyze(apath, did, dt, self.templates, precomputed=pair[0])
        fnd += self._guard("extract", did, _extract)
        fields, info = (pair[0][0].fields, pair[0][1]) if pair[0] else ({}, {})
        res["fields"], res["source"] = fields, info.get("source")
        raster = info.get("source") == "ocr" or kind == "image"
        # does the content look like the declared type?
        if fields is not None and self.templates.get(dt) and info.get("ocr_available") is not False and self._coverage(fields, dt) < self.cfg["classify_min_coverage"]:
            def _classify():
                best = (0.0, None)
                for other in self.templates:
                    if other == dt:
                        continue
                    d2, _ = extract.extract(apath, did, other, self.templates)
                    cv = self._coverage(d2.fields, other)
                    if cv > best[0]:
                        best = (cv, other)
                if best[1] and best[0] >= self.cfg["classify_accept_coverage"]:
                    return [_f("wrong_document_type", "soft", 3, 0.6,
                               f"This looks like a {best[1].replace('_', ' ')}, not a {dt.replace('_', ' ')}. Please upload the correct document.", did,
                               {"declared": dt, "detected": best[1], "coverage": round(best[0], 2)})]
                return []
            fnd += self._guard("classify", did, _classify)
        if self.cfg.get("layout_check") and fields and dt in self.templates:
            fnd += self._guard("layout", did, lambda: templates_build.check_layout(
                fields, self.templates[dt], did, info.get("source") or "text_layer", None, None))
        if dry_run:
            return res
        # ---- forensic and registry checks: never run in dry-run mode
        if kind == "pdf":
            fnd += self._guard("pdf_forensics", did, lambda: pdf_forensics.analyze(apath, did, dt))
        if dt in self.cfg["qr_doc_types"] and self.keys:
            def _qr():
                key = "approval_date" if dt == "eoa" else "valid_to"
                v = fields.get(key)
                printed = v["value"] if v and (v.get("source") == "text_layer" or v.get("conf", 0) >= self.cfg["qr_min_conf"]) else None
                return qr_verify.analyze(apath, did, self.keys, printed, app["inst_id"], doc_type=dt)
            fnd += self._guard("qr_verify", did, _qr)
        if raster and self.cfg["run_ela_on_raster"]:
            fnd += self._guard("ela", did, lambda: ela.analyze(apath, did))

        def _dedup():
            sig = dedup.signature(apath, self.index.cfg, dt, fields)
            self.index.add(did, app["inst_id"], dt, sig)
            return dedup.analyze(apath, did, self.index, app["inst_id"], dt, fields=fields)
        fnd += self._guard("dedup", did, _dedup)
        if self.source is not None:
            ar = adapters.verify_document(dt, fields, app["application_date"], app["inst_id"], self.source, sleep=self.sleep)
            res["adapter"] = ar
            fnd += self._guard("adapters", did, lambda: adapters.analyze(dt, fields, app["application_date"], app["inst_id"], self.source, did,
                                                                         sleep=self.sleep, result=ar))
        return res

    # -- one application
    def process_application(self, app: dict, uploads: list[dict], dry_run: bool = False, now: str = "") -> dict:
        docs = [self.process_document(app, u, dry_run) for u in uploads]
        fields_by_type = {d["doc_type"]: d["fields"] for d in docs if d["fields"]}
        submitted = {d["doc_type"]: d["fields"] for d in docs if d["kind"] is not None}
        adapter_results = {d["doc_type"]: d["adapter"] for d in docs if d["adapter"] is not None}
        app_id = app["inst_id"]
        findings: list[Finding] = []
        registry = None
        if not dry_run and self.source is not None:
            registry = self._safe(lambda: adapters.registry_facts(adapter_results, self.source, app["inst_id"], sleep=self.sleep), None)
        facts = rules.build_facts({"inst_id": app["inst_id"], "date": app["application_date"], "type": app.get("application_type"),
                                   "programme_type": app.get("programme_type")}, submitted, registry, self.norms, self.rulebook["required_documents"])
        rule_results = rules.evaluate(facts, self.rulebook)
        findings += self._guard("rules", app_id, lambda: rules.analyze(facts, self.rulebook, app_id))
        doc_findings = [x for d in docs for x in d["findings"]]
        allf = doc_findings + findings
        sc = score.score_findings(allf)
        out = {"mode": "dry_run" if dry_run else "full", "app": {k: app.get(k) for k in ("inst_id", "name", "application_date", "application_type", "programme_type")},
               "documents": [{**{k: v for k, v in d.items() if k not in ("findings", "adapter")}, "adapter": d["adapter"].to_json() if d["adapter"] else None,
                              "findings": [x.to_json() for x in d["findings"]]} for d in docs],
               "rules": [r.to_json() for r in rule_results], "findings": [x.to_json() for x in allf], "score": sc,
               "review": {"double_review": review_mod.needs_double_review(sc["score"], sc["category"])}, "facts": facts, "generated_at": now}
        if self.audit is not None and not dry_run:
            self._safe(lambda: self.audit.append({"event": "pipeline_processed", "inst_id": app_id, "documents": [d["doc_id"] for d in docs],
                                                  "score": sc["score"], "category": sc["category"], "at": now}), None)
        return out

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    def build_evidence(self, result: dict, out_pdf: str | Path, case_id: Optional[str] = None) -> dict:
        case = {"case_id": case_id or result["app"]["inst_id"], "generated_at": result.get("generated_at", ""),
                "institution": {"name": result["app"].get("name") or result["app"]["inst_id"], "inst_id": result["app"]["inst_id"],
                                "application_date": result["app"]["application_date"]},
                "score": result["score"], "documents": [{"doc_id": d["doc_id"], "doc_type": d["doc_type"], "path": d["path"]} for d in result["documents"]],
                "findings": [f for f in result["findings"]],
                "rules": {r["id"]: {"name": r["name"], "handbook_ref": next((x.get("handbook_ref") for x in self.rulebook["rules"] if x["id"] == r["id"]), None)}
                          for r in result["rules"]}}
        return evidence_pdf.build_evidence_pdf(case, out_pdf)


def doc_to_json(d: dict) -> dict:
    """A process_document() result as plain JSON (findings and the adapter result serialised)."""
    return {**{k: v for k, v in d.items() if k not in ("findings", "adapter")}, "adapter": d["adapter"].to_json() if d.get("adapter") else None,
            "findings": [x.to_json() for x in d["findings"]]}


# ----------------------------------------------------------------------------- views
def client_view(result: dict, cfg: Optional[dict] = None) -> dict:
    """What the applicant portal may see. Built by allow-list: only finding ids in client_visible_ids and rules in
    client_visible_rules, with plain-language text; the score, category, forensic / registry / duplicate findings, covered-text
    evidence (alt_values, flags) and the reviewers' side are never included."""
    cfg = cfg or load_config()
    msg = cfg["client_messages"]
    docs, n_issues = [], 0
    for d in result["documents"]:
        issues = []
        for f in d["findings"]:
            if f["id"] in cfg["client_visible_ids"] and f["kind"] in ("soft", "hard"):
                issues.append({"code": f["id"], "message": msg.get(f["id"], f["message"]),
                               "where": ({"page": f["bbox"][0], "box": f["bbox"][1:]} if f.get("bbox") and f["id"] == "low_ocr_confidence" else None)})
        # pre-fill: value + whether OCR was sure. A text-layer value is always "confident": a lowered confidence there means
        # covered text, which is forensic evidence and must not be hinted at to the applicant (no alt_values, no flags either)
        fields = {k: {"value": v["value"], "confident": True if v.get("source") == "text_layer" else v.get("conf", 1.0) >= 0.7}
                  for k, v in d["fields"].items()}
        n_issues += len(issues)
        docs.append({"doc_id": d["doc_id"], "doc_type": d["doc_type"], "status": "needs_attention" if issues else "ok", "issues": issues,
                     "prefill": fields})
    app_issues = []
    for r in result["rules"]:
        if r["id"] in cfg["client_visible_rules"] and r["result"] == "fail":
            app_issues.append({"code": r["id"], "message": msg.get(r["id"], "Please check this item."), "detail": r["detail"]})
    n_issues += len(app_issues)
    return {"mode": result["mode"], "application": result["app"]["inst_id"],
            "status": "action_needed" if n_issues else ("ready_to_submit" if result["mode"] == "dry_run" else "received"),
            "documents": docs, "application_issues": app_issues}


def staff_view(result: dict) -> dict:
    return result


def rule_change_impact(results: dict[str, dict], old: tuple[dict, dict], new: tuple[dict, dict], list_limit: int = 50) -> dict:
    """What a rule update would have done to already-processed cases. results = {inst_id: stored pipeline result (with its facts)};
    old / new = (rulebook, norms by programme type). Both sides are recomputed from the stored facts (rules + score only; nothing else is
    re-run), so the comparison is like for like. Returns pass/fail changes per rule and the cases whose category or score would move."""
    out = {"cases_checked": 0, "rule_changes": {}, "category_changes": [], "score_changes": 0}

    def run(res: dict, rb: dict, norms: dict) -> tuple[dict, dict]:
        facts = {**res["facts"], "norms": norms.get(res["facts"]["application"].get("programme_type"), {})}
        rr = {r.id: r.result for r in rules.evaluate(facts, rb)}
        kept = [f for f in res["findings"] if f.get("module") != "rules"]
        allf = kept + [f.to_json() for f in rules.analyze(facts, rb, res["app"]["inst_id"])]
        return rr, score.score_findings(allf)

    for inst, res in sorted(results.items()):
        if not res.get("facts"):
            continue
        out["cases_checked"] += 1
        ra, sa = run(res, *old)
        rb_, sb = run(res, *new)
        for rid in sorted(set(ra) | set(rb_)):
            if ra.get(rid) != rb_.get(rid):
                k = f"{ra.get(rid, 'absent')} -> {rb_.get(rid, 'absent')}"
                out["rule_changes"].setdefault(rid, {})[k] = out["rule_changes"].setdefault(rid, {}).get(k, 0) + 1
        if abs(sa["score"] - sb["score"]) > 1e-9:
            out["score_changes"] += 1
        if sa["category"] != sb["category"] and len(out["category_changes"]) < list_limit:
            out["category_changes"].append({"inst_id": inst, "old": sa["category"], "new": sb["category"],
                                            "old_score": round(sa["score"], 3), "new_score": round(sb["score"], 3)})
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.pipeline")
    ap.add_argument("app_json", help="{app: {...}, uploads: [{doc_id, doc_type, path}]}"); ap.add_argument("--world", required=True)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--view", choices=["staff", "client"], default="staff")
    a = ap.parse_args(argv)
    spec = json.load(open(a.app_json, encoding="utf-8"))
    src = adapters.WorldSource(a.world)
    try:
        keys = {r[0]: r[1] for r in src.con.execute("select issuer, secret_hex from issuer_keys")}
        res = Pipeline(src, keys).process_application(spec["app"], spec["uploads"], a.dry_run)
    finally:
        src.close()
    json.dump(client_view(res) if a.view == "client" else res, sys.stdout, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
