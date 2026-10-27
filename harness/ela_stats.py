"""Distribution of the ELA localisation score by document group (descriptive; no tuning on forgeries).

Usage: python -m harness.ela_stats [--corpus corpus_v2] [--limit N]
Groups: clean digital / clean scan (by split), digital documents with a forensic label, copy_move_scan,
other labelled scans. Used to set the PLACEHOLDER suspicious_score in config/ela.yaml from the
train-clean maximum, and to show (not claim) how ELA scores forged documents.
"""
from __future__ import annotations

import argparse
import collections

import numpy as np

from harness import corpus_io as c
from modules import ela


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    cfg = ela.load_config()
    groups = collections.defaultdict(list)
    for n, e in enumerate(c.iter_docs()):
        if a.limit and n >= a.limit:
            break
        f = ela.analyze(e["path"], e["doc_id"], cfg)[0]
        if f.id != "ela_summary":
            continue
        sc = f.evidence["score"]
        labs = {l["name"] for l in e["labels"] if l["kind"] != "control"}
        kind = "scan" if c.is_scan(e) else "digital"
        if not labs:
            groups[f"clean {kind} ({c.split_of(e['inst_id'])})"].append(sc)
        elif "copy_move_scan" in labs:
            groups["copy_move_scan"].append(sc)
        elif labs & {"spliced_expiry_forgery", "incremental_edit_forgery"}:
            groups["covered-text forgeries (digital)"].append(sc)
        else:
            groups[f"other labelled {kind}"].append(sc)
    print(f"# ELA score distribution: {c.corpus_root().name} (jpeg_quality={cfg['jpeg_quality']}, "
          f"block={cfg['block']}, z_hot={cfg['z_hot']})\n")
    print("| group | n | min | median | p95 | max |\n|---|---|---|---|---|---|")
    for g, v in sorted(groups.items()):
        v = np.array(v)
        print(f"| {g} | {len(v)} | {v.min():.1f} | {np.median(v):.1f} | {np.percentile(v, 95):.1f} | {v.max():.1f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
