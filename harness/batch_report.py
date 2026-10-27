"""Detection / false-positive tables for several modules over corpus_v2 (all + each split) and
corpus_test_v2, as markdown. Each module runs ONCE per corpus; splits are aggregated afterwards.

Usage: python -m harness.batch_report pdf_forensics dedup ela [--out reports/tables_batch_b.md]
Every row carries n; n < 10 is marked weak evidence.
"""
from __future__ import annotations

import argparse

from harness import corpus_io as c
from harness import run_module as rm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modules", nargs="+", choices=sorted(rm.REGISTRY)); ap.add_argument("--out")
    a = ap.parse_args(argv)
    out = ["# Detection / false-positive tables\n",
           "Splits are md5(inst_id) 70/15/15 of corpus_v2; configs are built from `train` only, tuned on `val`, "
           "reported on `test` and on the held-out corpus_test_v2. n < 10 is weak evidence.\n"]
    for m in a.modules:
        rows = {}
        for cd in ("corpus_v2", "corpus_test_v2"):
            c.set_corpus(cd)
            rows[cd] = (rm.collect(m), c.corpus_root().name)
        out.append(f"## {m}\n")
        for cd, split in (("corpus_v2", "all"), ("corpus_v2", "train"), ("corpus_v2", "val"), ("corpus_v2", "test"),
                          ("corpus_test_v2", "all")):
            c.set_corpus(cd)
            out.append(rm.render(rm.summarize(m, rows[cd][0], split)) + "\n")
    text = "\n".join(out)
    print(text)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
