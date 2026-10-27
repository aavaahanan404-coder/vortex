"""OCR field accuracy against blur level on the graded-blur set (the data behind the quality gate's blur policy).

Usage: python -m harness.calibrate_blur_ocr [--corpus corpus_v2] [--limit-per-sigma N] > reports/blur_vs_ocr_<corpus>.md
For every graded file (24 clean sources x 9 sigma levels) extract() runs on the scan and its fields are compared with the
manifest (lower-cased letters and digits only). Reported per sigma: fields read correctly, accuracy of fields the extractor was
sure of (conf >= 0.9), how many documents the extractor itself flags (low_ocr_confidence), and the VoL range of the files.
Templates and thresholds are NOT tuned on these numbers: they only show where OCR starts to fail so a policy can be chosen.
"""
from __future__ import annotations

import argparse
import collections
import re

import numpy as np

from harness import corpus_io as c
from modules import extract as X
from modules import quality as Q

SKIP = {"qr", "seal", "signature"}


def norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--limit-per-sigma", type=int)
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    tpl, cfg, qcfg = X.load_templates(), X.load_config(), Q.load_config()
    stat = collections.defaultdict(lambda: {"n": 0, "ok": 0, "hi_n": 0, "hi_ok": 0, "docs": 0, "flag": 0, "vol": [], "doc_ok": 0})
    seen = collections.Counter()
    for g in c.iter_graded():
        s = g["labels"][0]["blur_sigma"]
        if a.limit_per_sigma and seen[s] >= a.limit_per_sigma:
            continue
        seen[s] += 1
        doc, info = X.extract(g["path"], g["doc_id"], g["doc_type"], tpl, cfg)
        fl = any(f.id == "low_ocr_confidence" for f in X.analyze(g["path"], g["doc_id"], g["doc_type"], tpl, cfg))
        truth = {k: v for k, v in g["fields"].items() if k not in SKIP and v.get("value") not in (None, "")}
        t = stat[s]
        t["docs"] += 1; t["flag"] += fl; t["vol"].append(Q.measure(g["path"], 0, qcfg)["vol"])
        good_doc = True
        for k, v in truth.items():
            f = doc.fields.get(k)
            good = f is not None and norm(f["value"]) == norm(v["value"])
            t["n"] += 1; t["ok"] += good; good_doc &= good
            if f and f["conf"] >= 0.9:
                t["hi_n"] += 1; t["hi_ok"] += good
        t["doc_ok"] += good_doc
    print(f"# OCR accuracy vs blur: {c.corpus_root().name} (graded set; n = documents per sigma)\n")
    print("| sigma | docs | VoL min..max | fields correct | documents fully correct | conf>=0.9 fields | accuracy of conf>=0.9 | docs flagged low_ocr_confidence |")
    print("|---|---|---|---|---|---|---|---|")
    for s in sorted(stat):
        t = stat[s]
        print(f"| {s:g} | {t['docs']} | {min(t['vol']):.0f}..{max(t['vol']):.0f} | {t['ok'] / t['n']:.1%} | {t['doc_ok']}/{t['docs']} | "
              f"{t['hi_n']} | {t['hi_ok'] / max(1, t['hi_n']):.1%} | {t['flag']}/{t['docs']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
