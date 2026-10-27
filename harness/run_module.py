"""Run a module over corpus documents; print per-label detection rate and false-positive rate.

Usage: python -m harness.run_module MODULE [--corpus corpus_v2|corpus_test_v2|PATH]
           [--split all|train|val|test] [--doc-type T] [--limit N] [--csv misses.csv]

A label counts as detected if the module emits at least one finding whose id is in TARGETS[label].
False positive = a clean document (no non-control label; the graded-blur set is a separate
manifest and is never clean) with any soft/hard finding (info findings are ignored).
Every row carries n; rows with n < 10 are marked "weak evidence".

REGISTRY entry: run(entry, ctx) -> list[Finding]; applies(entry) -> bool; targets: label -> finding
ids; optional prepare(all_entries) -> ctx (corpus-wide state, e.g. the dedup index; built from ALL
documents of the corpus, then only the --split subset is scored); optional expected(entry, finding)
-> True if a finding on a clean document is a correct one (e.g. the donor end of a duplicate pair).
"""
from __future__ import annotations

import argparse, collections, csv, sys
from typing import Callable

from harness import corpus_io as c


def _quality(e, ctx=None):
    from modules import quality
    return quality.analyze(e["path"], e["doc_id"], doc_type=e["doc_type"])


def _qr_required():
    from modules import qr_verify
    return set(qr_verify.load_config()["qr_required_doc_types"])


def _ocr_printed_date(e, issuer: str, min_conf: float = 0.9):
    """Printed validity date of a scan from extract (OCR). Returned only when the field was read with conf >= min_conf and
    parses as a date: an OCR misread must never become a hard qr_date_mismatch (it is passed on as 'not available')."""
    from modules import qr_verify
    key = "approval_date" if issuer == "aicte" else "valid_to"
    f = _extracted_fields(e).get(key)
    if f and f["conf"] >= min_conf and qr_verify.parse_date(f["value"]):
        return f["value"]
    return None


def _qr(e, ctx=None):
    from modules import qr_verify
    f = e["fields"]
    if "qr" in f:
        iss = f["qr"]["value"].split("|")[1]
        pv = f.get("approval_date") if iss == "aicte" else f.get("valid_to")
        if pv and not c.is_scan(e):
            pv = pv["value"]
        elif pv and c.is_scan(e):
            pv = _ocr_printed_date(e, iss)                          # scans: the date read by extract, only if it is certain
        else:
            pv = None
    else:
        pv = None
    return qr_verify.analyze(e["path"], e["doc_id"], c.issuer_keys(), pv, e["inst_id"],
                             doc_type=e["doc_type"])


def _pdf(e, ctx=None):
    from modules import pdf_forensics
    return pdf_forensics.analyze(e["path"], e["doc_id"], doc_type=e["doc_type"])


def _extracted_fields(e):
    """Fields of one corpus document from the extract module (text layer or OCR); {} if extraction fails."""
    from modules import extract
    try:
        return extract.extract(e["path"], e["doc_id"], e["doc_type"])[0].fields
    except Exception:
        return {}


def _dedup_prepare(entries):
    """Index of the whole corpus. Field values come from extract (set SIH_DEDUP_NO_FIELDS=1 for the pixel-only channel)."""
    import os
    from modules import dedup
    use = not os.environ.get("SIH_DEDUP_NO_FIELDS")
    return dedup.build_index([(e["doc_id"], e["inst_id"], e["doc_type"], e["path"], _extracted_fields(e) if use else None)
                              for e in entries])


def _dedup(e, ctx=None):
    from modules import dedup
    return dedup.analyze(e["path"], e["doc_id"], ctx, inst_id=e["inst_id"], doc_type=e["doc_type"])


def _dedup_expected(e, f):
    """The donor end of a labelled pair is a correct link, not a false positive."""
    ev = f.evidence.get("matches") or []
    donor_of = {d["derived_from"]["doc_id"] for d in c._manifest().values() if d.get("derived_from")}
    dups = {d["doc_id"] for d in c._manifest().values() if d.get("derived_from")}
    return e["doc_id"] in donor_of and all(m["doc_id"] in dups for m in ev)


def _ela(e, ctx=None):
    from modules import ela
    return ela.analyze(e["path"], e["doc_id"])


REGISTRY: dict[str, dict] = {
    "quality": {"run": _quality, "applies": lambda e: True,
                "targets": {"scan_degraded": {"blurry", "tilted"}}},
    "qr_verify": {"run": _qr,
                  "applies": lambda e: "qr" in e["fields"] or e["doc_type"] in _qr_required(),
                  "targets": {"qr_bad_signature": {"qr_bad_signature"},
                              "qr_date_mismatch": {"qr_date_mismatch"},
                              "spliced_expiry_forgery": {"qr_date_mismatch"},
                              "incremental_edit_forgery": {"qr_date_mismatch"},
                              "certificate_reuse": {"qr_wrong_institution"}}},
    "pdf_forensics": {"run": _pdf, "applies": lambda e: True,
                      "targets": {"spliced_expiry_forgery": {"covered_text"},
                                  "incremental_edit_forgery": {"incremental_update", "covered_text"},
                                  "editor_metadata": {"editor_metadata"}}},
    "dedup": {"run": _dedup, "prepare": _dedup_prepare, "expected": _dedup_expected,
              "applies": lambda e: True,
              "targets": {"exact_duplicate": {"exact_duplicate"},
                          "visual_near_duplicate": {"visual_near_duplicate", "text_near_duplicate"}}},
    "ela": {"run": _ela, "applies": lambda e: True, "targets": {}},
}


def _all_entries():
    return list(c.iter_docs())


def collect(name: str, doc_type=None, limit=None) -> list[dict]:
    """Run `name` once over the selected corpus; one row per applicable document."""
    spec = REGISTRY[name]
    ctx = spec["prepare"](_all_entries()) if "prepare" in spec else None
    rows = []
    for e in c.iter_docs(doc_type=doc_type):
        if not spec["applies"](e):
            continue
        if limit and len(rows) >= limit:
            break
        fs = spec["run"](e, ctx)
        active = [f for f in fs if f.kind in ("hard", "soft")]
        rows.append({"doc_id": e["doc_id"], "split": c.split_of(e["inst_id"]), "clean": c.is_clean(e),
                     "labels": [l["name"] for l in e["labels"] if l["kind"] != "control"],
                     "ids": {f.id for f in fs}, "active": {f.id for f in active},
                     "expected": {f.id for f in active if spec.get("expected") and spec["expected"](e, f)}})
    return rows


def summarize(name: str, rows: list[dict], split=None) -> dict:
    """Detection / false-positive statistics for the rows of one split (None/'all' = everything)."""
    spec = REGISTRY[name]
    det = collections.defaultdict(lambda: [0, 0]); misses = []; fp = [0, 0]; fp_docs = []
    fp_by_id = collections.Counter(); expected_links = []
    untargeted = collections.Counter(); extra_ids = collections.Counter(); n = 0
    for r in rows:
        if split not in (None, "all") and r["split"] != split:
            continue
        n += 1
        for lab in set(r["labels"]):
            t = spec["targets"].get(lab)
            if t is None:
                untargeted[lab] += 1
                continue
            det[lab][1] += 1
            if r["ids"] & t:
                det[lab][0] += 1
            else:
                misses.append((lab, r["doc_id"], ";".join(sorted(r["ids"]))))
        if r["clean"]:
            fp[1] += 1
            bad = r["active"] - r["expected"]
            expected_links += [(r["doc_id"], i) for i in r["expected"]]
            if bad:
                fp[0] += 1; fp_docs.append((r["doc_id"], sorted(bad)))
                fp_by_id.update(bad)
        elif not any(spec["targets"].get(l) for l in r["labels"]):
            extra_ids.update(r["active"])               # fired on a labelled doc this module does not target
    return {"module": name, "corpus": c.corpus_root().name, "split": split or "all", "det": dict(det),
            "fp": fp, "fp_docs": fp_docs, "fp_by_id": fp_by_id, "misses": misses,
            "untargeted": untargeted, "expected_links": expected_links, "n_scored": n, "extra_ids": extra_ids}


def evaluate(name: str, doc_type=None, limit=None, split=None) -> dict:
    """Score `name` on the currently selected corpus (collect + summarize)."""
    return summarize(name, collect(name, doc_type, limit), split)


def weak(n: int) -> str:
    return f"n={n}, weak evidence" if n < 10 else f"n={n}"


def render(r: dict) -> str:
    out = [f"### {r['module']} on {r['corpus']} (split={r['split']}, {r['n_scored']} documents scored)\n",
           "| label | detected | n | rate | note |", "|---|---|---|---|---|"]
    for l, (d, n) in sorted(r["det"].items()):
        out.append(f"| {l} | {d} | {n} | {d / n:.0%} | {weak(n)} |")
    if not r["det"]:
        out.append("| (module targets no label) | | | | |")
    fp = r["fp"]
    out.append(f"\nFalse positives on clean documents: **{fp[0]}/{fp[1]}**")
    if r["fp_by_id"]:
        out.append("\n| clean-document FP by finding | docs |\n|---|---|")
        out += [f"| {i} | {k} |" for i, k in sorted(r["fp_by_id"].items())]
        out.append(f"\nFP documents: {r['fp_docs'][:8]}")
    else:
        out.append("\n| clean-document FP by finding | docs |\n|---|---|\n| (none) | 0 |")
    if r["expected_links"]:
        out.append(f"\nCorrect link endpoints on unlabelled donors (not counted as FP): {len(r['expected_links'])}")
    if r["extra_ids"]:
        out.append(f"\nFired on labelled documents this module does not target: {dict(r['extra_ids'])}")
    out.append(f"\nMisses: {len(r['misses'])}" + (f"  e.g. {r['misses'][:5]}" if r["misses"] else ""))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.run_module")
    ap.add_argument("module", choices=sorted(REGISTRY))
    ap.add_argument("--corpus", help="corpus_v2 | corpus_test_v2 | path (default: env SIH_CORPUS or corpus_v2)")
    ap.add_argument("--split", choices=["all", "train", "val", "test"], default="all")
    ap.add_argument("--doc-type"); ap.add_argument("--limit", type=int); ap.add_argument("--csv")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    r = evaluate(a.module, a.doc_type, a.limit, a.split)
    print(render(r))
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["label", "doc_id", "finding_ids"]); w.writerows(r["misses"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
