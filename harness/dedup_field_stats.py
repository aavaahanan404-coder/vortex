"""Best field-region score of every scan against the digital documents of its type (dedup channel 4).

Usage: python -m harness.dedup_field_stats [--corpus corpus_v2]
For each scan: its highest field_score over same-type digital documents of OTHER institutions, whether
that best match is its labelled donor, and the runner-up. Prints markdown; this is the evidence behind
`min_field_score` in config/dedup.yaml (labelled re-scans vs every other scan).
"""
from __future__ import annotations

import argparse

from harness import corpus_io as c
from modules import dedup as d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    cfg = d.load_config()
    ents = list(c.iter_docs()); by = {e["doc_id"]: e for e in ents}
    idx = d.build_index([(e["doc_id"], e["inst_id"], e["doc_type"], e["path"]) for e in ents], cfg)
    donor = {e["doc_id"]: e["derived_from"]["doc_id"] for e in ents
             if e.get("derived_from") and e["derived_from"]["mode"] == "near"}
    rows = []
    for e in ents:
        if not c.is_scan(e):
            continue
        tc = idx._type_cache(e["doc_type"])
        if tc is None:
            continue
        mine = idx._crops_of(tc, idx.get(e["doc_id"]))
        sc = sorted(((d.field_score(mine, idx._crops_of(tc, x), cfg), x["doc_id"]) for x in idx.docs
                     if x["doc_type"] == e["doc_type"] and not x["raster"] and x["inst_id"] != e["inst_id"]),
                    reverse=True)
        if len(sc) < 2:
            continue
        rows.append((e["doc_id"], e["doc_type"], "labelled re-scan" if e["doc_id"] in donor else
                     "other scan (" + ",".join(l["name"] for l in e["labels"]) + ")",
                     sc[0][0], sc[0][1] == donor.get(e["doc_id"]), sc[1][0], len(tc["boxes"])))
    rows.sort(key=lambda r: -r[3])
    near = [r[3] for r in rows if r[2] == "labelled re-scan"]; oth = [r[3] for r in rows if r[2] != "labelled re-scan"]
    print(f"# field-region scores: {c.corpus_root().name}\n")
    print(f"labelled re-scans (n={len(near)}): min {min(near):.3f} max {max(near):.3f}; best match is the donor in "
          f"{sum(1 for r in rows if r[2] == 'labelled re-scan' and r[4])}/{len(near)}")
    print(f"other scans (n={len(oth)}): max best-match score {max(oth):.3f}, median {sorted(oth)[len(oth) // 2]:.3f}")
    print(f"threshold min_field_score = {cfg['min_field_score']}\n")
    print("| scan | type | kind | best score | best is donor | runner-up | boxes |\n|---|---|---|---|---|---|---|")
    for r in rows[:12]:
        print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.3f} | {r[4]} | {r[5]:.3f} | {r[6]} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
