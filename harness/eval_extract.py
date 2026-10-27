"""Field-extraction accuracy against the manifest ground truth.

Usage: python -m harness.eval_extract [--corpus C] [--split all|train|val|test] [--source digital|scan|all]
                                      [--doc-type T] [--limit N] [--worst 15] [--json out.json]
A field is correct when its value equals the manifest value after lower-casing and removing everything but
letters and digits. Reported: accuracy by document type and source, accuracy of fields whose conf >= 0.9 / < 0.7,
the worst keys, covered-text handling on forged documents (visible value kept? flagged? hidden original in alt_values?),
and layout-check false positives on clean documents. Every row carries n. Templates come from config/templates.yaml
(built from corpus_v2 TRAIN only), so train rows are in-sample.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys

from harness import corpus_io as c
from modules import extract as X
from modules import templates_build as TB

SKIP = {"qr", "seal", "signature"}


def norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def evaluate(split=None, source="all", doc_type=None, limit=None, force_ocr=False, labels=None):
    tpl = X.load_templates(); cfg = X.load_config(); lcfg = TB.load_config()
    rows, fields = [], []
    layout_fp = collections.Counter(); layout_n = 0
    n = 0
    for e in c.iter_docs(doc_type=doc_type, split=None if split in (None, "all") else split):
        sc = c.is_scan(e)
        if source == "digital" and sc or source == "scan" and not sc:
            continue
        if labels and not any(l["name"] in labels for l in e["labels"]):
            continue
        n += 1
        if limit and n > limit:
            break
        doc, info = X.extract(e["path"], e["doc_id"], e["doc_type"], tpl, cfg, force_ocr)
        src = "digital" if not sc else ("scan-gate-fail" if (e.get("quality") or {}).get("expected_gate") == "fail" else "scan")
        truth = {k: v for k, v in e["fields"].items() if k not in SKIP and v.get("value") not in (None, "")}
        ok = 0
        for k, v in truth.items():
            f = doc.fields.get(k)
            good = f is not None and norm(f["value"]) == norm(v["value"])
            ok += good
            fields.append({"doc_type": e["doc_type"], "src": src, "key": re.sub(r"\[\d+\]", "[i]", k), "ok": good,
                           "found": f is not None, "conf": f["conf"] if f else 0.0, "doc_id": e["doc_id"],
                           "got": f["value"] if f else None, "want": v["value"]})
        extra = [k for k in doc.fields if k not in truth]
        rows.append({"doc_id": e["doc_id"], "doc_type": e["doc_type"], "src": src, "n": len(truth), "ok": ok,
                     "extra": len(extra), "labels": [l["name"] for l in e["labels"]], "clean": c.is_clean(e),
                     "fields": doc.fields, "truth": e["fields"]})
        if c.is_clean(e) and e["doc_type"] in tpl:
            layout_n += 1
            fs = TB.check_layout(doc.fields, tpl[e["doc_type"]], e["doc_id"], info["source"], lcfg)
            for f in fs:
                if f.kind != "info":
                    layout_fp[(src, f.id)] += 1
    return {"rows": rows, "fields": fields, "layout_fp": layout_fp, "layout_n": layout_n}


def report(res, worst=15) -> str:
    out = []
    by = collections.defaultdict(lambda: [0, 0, 0, 0])
    for f in res["fields"]:
        t = by[(f["src"], f["doc_type"])]
        t[0] += f["ok"]; t[1] += 1
    docs = collections.Counter((r["src"], r["doc_type"]) for r in res["rows"])
    out.append("| source | doc type | documents | fields | correct | accuracy |\n|---|---|---|---|---|---|")
    for (s, dt), (ok, n, _, _) in sorted(by.items()):
        nd = docs[(s, dt)]
        out.append(f"| {s} | {dt} | {nd}{' (weak)' if nd < 10 else ''} | {n} | {ok} | {ok / n:.1%} |")
    for s in ("digital", "scan", "scan-gate-fail"):
        fs = [f for f in res["fields"] if f["src"] == s]
        if not fs:
            continue
        hi = [f for f in fs if f["conf"] >= 0.9]; lo = [f for f in fs if f["conf"] < 0.7]
        nd = len({f["doc_id"] for f in fs})
        out.append(f"\n**{s}** (n={nd} documents, {len(fs)} fields): accuracy {sum(f['ok'] for f in fs) / len(fs):.1%}; "
                   f"conf >= 0.9: {sum(f['ok'] for f in hi) / max(1, len(hi)):.1%} of {len(hi)}; "
                   f"conf < 0.7 (incl. not found): {sum(f['ok'] for f in lo) / max(1, len(lo)):.1%} of {len(lo)}; "
                   f"not found: {sum(not f['found'] for f in fs)}")
    keys = collections.defaultdict(lambda: [0, 0])
    for f in res["fields"]:
        t = keys[(f["src"], f["doc_type"], f["key"])]
        t[0] += f["ok"]; t[1] += 1
    bad = sorted(((1 - a / n, n, k) for k, (a, n) in keys.items() if a < n), reverse=True)[:worst]
    if bad:
        out.append("\nWorst keys (error rate, n fields):")
        out += [f"- {k[0]} {k[1]} {k[2]}: {er:.0%} of {n}" for er, n, k in bad]
    fr = [r for r in res["rows"] if any(l in ("spliced_expiry_forgery", "incremental_edit_forgery") for l in r["labels"])]
    if fr:
        vis = flag = alt = 0
        for r in fr:
            f = r["fields"].get("valid_to"); t = r["truth"]["valid_to"]
            if f:
                vis += norm(f["value"]) == norm(t["value"]); flag += "covered_text" in f.get("flags", [])
                alt += norm(t.get("hidden_original", "")) in {norm(a) for a in f.get("alt_values", [])}
        out.append(f"\nCovered-text forgeries (n={len(fr)}, weak evidence): visible forged value kept {vis}/{len(fr)}, "
                   f"flagged covered_text {flag}/{len(fr)}, hidden original in alt_values {alt}/{len(fr)}")
    lf = res["layout_fp"]
    out.append(f"\nLayout check on clean documents (n={res['layout_n']}): false findings "
               + (", ".join(f"{s} {i}: {k}" for (s, i), k in sorted(lf.items())) if lf else "none"))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--split", default="all"); ap.add_argument("--source", default="all")
    ap.add_argument("--doc-type"); ap.add_argument("--limit", type=int); ap.add_argument("--worst", type=int, default=15)
    ap.add_argument("--force-ocr", action="store_true"); ap.add_argument("--json")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    res = evaluate(a.split, a.source, a.doc_type, a.limit, a.force_ocr)
    print(f"### extract on {c.corpus_root().name} (split={a.split}, source={a.source})\n")
    print(report(res, a.worst))
    if a.json:
        json.dump({"fields": res["fields"]}, open(a.json, "w"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
