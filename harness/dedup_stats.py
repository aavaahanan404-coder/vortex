"""Similarity distributions over document pairs, to justify the thresholds in config/dedup.yaml.

Usage: python -m harness.dedup_stats [--corpus corpus_v2] [--split all|train|val|test]

"Unrelated" = a pair from different institutions, same doc type, not byte-identical and not linked
by the manifest's derived_from. Pair classes: dig-dig, mixed (one scan), scan-scan. With --split S
a pair counts if BOTH documents are in split S. Labelled pairs come from derived_from (mode near).
Prints markdown: distribution of pHash normalised Hamming distance and MinHash Jaccard per class,
the labelled near-duplicate pairs, and a threshold trade-off table (labelled pairs caught vs
unrelated pairs and distinct documents that would be linked).
"""
from __future__ import annotations

import argparse
import collections

import numpy as np

from harness import corpus_io as c
from modules import dedup as d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--split", default="all")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    cfg = d.load_config()
    ents = list(c.iter_docs())
    idx = d.build_index([(e["doc_id"], e["inst_id"], e["doc_type"], e["path"]) for e in ents], cfg)
    ids = [x["doc_id"] for x in idx.docs]
    by = {e["doc_id"]: e for e in ents}
    ph, has_ph, mh, has_mh = idx._mats()
    sha = [x["sha256"] for x in idx.docs]
    linked = {frozenset((e["doc_id"], e["derived_from"]["doc_id"])): e["derived_from"]["mode"]
              for e in ents if e.get("derived_from")}
    keep = [i for i, k in enumerate(ids) if a.split == "all" or c.split_of(by[k]["inst_id"]) == a.split]
    cls_name = {0: "dig-dig", 1: "mixed (one scan)", 2: "scan-scan"}
    dist = collections.defaultdict(list); jac = []; pairs_by_cls = collections.defaultdict(list)
    lab = []
    for ai, i in enumerate(keep):
        dd_row = (ph != ph[i][None, :]).mean(axis=1)
        jj_row = (mh == mh[i][None, :]).mean(axis=1)
        for j in keep[ai + 1:]:
            A, B = by[ids[i]], by[ids[j]]
            if A["inst_id"] == B["inst_id"] or A["doc_type"] != B["doc_type"]:
                continue
            pair = frozenset((ids[i], ids[j]))
            cls = int(c.is_scan(A)) + int(c.is_scan(B))
            dd = dd_row[j] if has_ph[i] and has_ph[j] else np.nan
            jj = jj_row[j] if has_mh[i] and has_mh[j] else np.nan
            if pair in linked:
                lab.append((ids[i], ids[j], linked[pair], cls, dd, jj)); continue
            if sha[i] == sha[j]:
                continue
            if not np.isnan(dd):
                dist[cls].append(dd); pairs_by_cls[cls].append((dd, ids[i], ids[j]))
            if not np.isnan(jj):
                jac.append(jj)
    print(f"# dedup pair statistics: {c.corpus_root().name}, split={a.split}\n")
    print(f"documents in split: {len(keep)}; pHash {cfg['hash_size'] ** 2} bits; MinHash {cfg['n_perm']} perms, "
          f"{cfg['shingle_words']}-word shingles minus boilerplate\n")
    print("## pHash normalised Hamming distance, unrelated same-type pairs\n")
    print("| pair class | n pairs | min | p0.1 | p1 | p5 | median |\n|---|---|---|---|---|---|---|")
    for k in (0, 1, 2):
        v = np.array(dist[k])
        if len(v):
            print(f"| {cls_name[k]} | {len(v)} | {v.min():.3f} | {np.percentile(v, .1):.3f} | "
                  f"{np.percentile(v, 1):.3f} | {np.percentile(v, 5):.3f} | {np.median(v):.3f} |")
    jv = np.array(jac)
    print(f"\n## MinHash Jaccard, unrelated same-type pairs (n={len(jv)}): "
          + (f"p50={np.median(jv):.3f}, p99={np.percentile(jv, 99):.3f}, p99.9={np.percentile(jv, 99.9):.3f}, "
             f"max={jv.max():.3f}" if len(jv) else "none"))
    near = [x for x in lab if x[2] == "near"]
    print(f"\n## Labelled pairs in this split: {len(lab)} (near: {len(near)})\n")
    print("| pair | mode | class | hamming_frac | jaccard |\n|---|---|---|---|---|")
    for x in lab:
        print(f"| {x[0]} <-> {x[1]} | {x[2]} | {cls_name[x[3]]} | {x[4]:.3f} | "
              f"{'n/a (no text)' if np.isnan(x[5]) else f'{x[5]:.3f}'} |")
    print("\n## Visual threshold trade-off (pHash normalised Hamming <= T)\n")
    print("| T | labelled near pairs caught | unrelated pairs linked: dig-dig | mixed | scan-scan | distinct clean docs linked |")
    print("|---|---|---|---|---|---|")
    for T in (0.02, 0.03, 0.05, 0.07, 0.09, 0.10, 0.11):
        caught = sum(1 for x in near if not np.isnan(x[4]) and x[4] <= T)
        cnt = [int((np.array(dist[k]) <= T).sum()) if dist[k] else 0 for k in (0, 1, 2)]
        docs = set()
        for k in (0, 1, 2):
            for dd, x, y in pairs_by_cls[k]:
                if dd <= T:
                    docs |= {x, y}
        print(f"| {T} | {caught}/{len(near)} | {cnt[0]} | {cnt[1]} | {cnt[2]} | "
              f"{sum(1 for k in docs if c.is_clean(by[k]))} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
