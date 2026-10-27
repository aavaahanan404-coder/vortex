"""End-to-end: every institution of the corpus through the full pipeline, scored against the seeded defects.

Usage: python -m harness.run_pipeline [--corpus C] [--split S] [--limit N] [--no-scenarios]
Institutions are processed in application-date order with ONE shared dedup index, so a duplicate is flagged when it ARRIVES (the
earlier submission is the donor). Per institution the staff category and score come from modules.pipeline; the client view is checked
for leaks. Ground truth: institutions.json `mutations` (scan_clean is a control, not a defect). Reported with n; n < 10 is weak.
  clean institution  = no mutation except scan_clean
  escalated          = category high_anomaly or needs_human (a person looks)
A seeded defect counts as caught if its institution is escalated; the table also shows the dominant finding ids for that mutation.
"""
from __future__ import annotations

import argparse
import collections
import json

from harness import corpus_io as c
from modules import adapters as A
from modules import pipeline as PL

CONTROL = {"scan_clean"}
FORBIDDEN_IN_CLIENT_VIEW = ("score", "category", "high_anomaly", "needs_human", "covered_text", "alt_values", "registry_mismatch",
                            "exact_duplicate", "qr_date_mismatch", "qr_bad_signature", "font_anomaly", "editor_metadata", "R016", "R013")


def run(split=None, limit=None, scenarios=True, work_dir=None):
    src = A.WorldSource(c.corpus_root() / "world" / "world.sqlite", scenarios=scenarios)
    keys = c.issuer_keys()
    pl = PL.Pipeline(src, keys, work_dir=work_dir)
    by = collections.defaultdict(list)
    for e in c.iter_docs():
        by[e["inst_id"]].append(e)
    insts = sorted(c.institutions(), key=lambda i: (i["application_date"], i["inst_id"]))
    rows, leaks = [], []
    try:
        for n, i in enumerate(insts):
            if limit and n >= limit:
                break
            ups = [{"doc_id": e["doc_id"], "doc_type": e["doc_type"], "path": e["path"]} for e in by[i["inst_id"]]]
            app = {"inst_id": i["inst_id"], "name": i["name"], "application_date": i["application_date"],
                   "application_type": i["application_type"], "programme_type": i["type"]}
            res = pl.process_application(app, ups)
            if split not in (None, "all") and c.split_of(i["inst_id"]) != split:
                continue
            cv = json.dumps(PL.client_view(res))
            leaked = [w for w in FORBIDDEN_IN_CLIENT_VIEW if w in cv]
            if leaked:
                leaks.append((i["inst_id"], leaked))
            rows.append({"inst": i["inst_id"], "mutations": [m for m in i["mutations"] if m not in CONTROL], "score": res["score"]["score"],
                         "category": res["score"]["category"], "ids": sorted({f["id"] for f in res["findings"] if f["kind"] in ("hard", "soft")}),
                         "double": res["review"]["double_review"], "scenario": bool(i.get("scenario_flag")),
                         "errors": [f["id"] for f in res["findings"] if f["id"] == "step_failed"]})
    finally:
        src.close()
    return rows, leaks


def render(rows, leaks) -> str:
    out = []
    clean = [r for r in rows if not r["mutations"]]
    dirty = [r for r in rows if r["mutations"]]
    cat = collections.Counter(r["category"] for r in clean)
    out.append(f"### clean institutions (n={len(clean)}): category of each\n")
    out.append("| category | n |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in sorted(cat.items())))
    ok = cat["auto_clear_candidate"] / max(1, len(clean))
    esc_clean = [r for r in clean if r["category"] in ("high_anomaly", "needs_human")]
    out.append(f"\nauto-cleared {cat['auto_clear_candidate']}/{len(clean)} ({ok:.0%}); escalated to a person {len(esc_clean)}/{len(clean)}; "
               f"of those at scenario institutions (registry outage by design) {sum(1 for r in esc_clean if r['scenario'])}.")
    if esc_clean:
        out.append("Escalated clean institutions, and why: " + "; ".join(f"{r['inst']} {r['category']} {r['ids']}" for r in esc_clean[:8]))
    out.append(f"\n### seeded defects (n={len(dirty)} institutions with at least one mutation)\n")
    by_m = collections.defaultdict(list)
    for r in dirty:
        for m in r["mutations"]:
            by_m[m].append(r)
    out.append("| mutation | n | escalated | high_anomaly | median score | top findings |\n|---|---|---|---|---|---|")
    for m, rs in sorted(by_m.items()):
        esc = sum(1 for r in rs if r["category"] in ("high_anomaly", "needs_human"))
        hi = sum(1 for r in rs if r["category"] == "high_anomaly")
        top = collections.Counter(i for r in rs for i in r["ids"]).most_common(3)
        sc = sorted(r["score"] for r in rs)[len(rs) // 2]
        out.append(f"| {m} | {len(rs)}{' (weak)' if len(rs) < 10 else ''} | {esc}/{len(rs)} | {hi} | {sc:.2f} | {', '.join(f'{k} x{v}' for k, v in top)} |")
    esc_all = sum(1 for r in dirty if r["category"] in ("high_anomaly", "needs_human"))
    out.append(f"\nInstitutions with a seeded defect that reach a person: **{esc_all}/{len(dirty)}**; auto-cleared (missed): "
               f"{[r['inst'] + str(r['mutations']) for r in dirty if r['category'] == 'auto_clear_candidate']}.")
    out.append(f"\nDouble review would be routed for {sum(1 for r in rows if r['double'])}/{len(rows)} institutions.")
    out.append(f"Client-view leaks: **{len(leaks)}** {leaks[:3]}. Steps that failed (step_failed): {sum(len(r['errors']) for r in rows)}.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--split", default="all"); ap.add_argument("--limit", type=int)
    ap.add_argument("--no-scenarios", action="store_true")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    import tempfile
    rows, leaks = run(a.split, a.limit, not a.no_scenarios, tempfile.mkdtemp())
    print(f"## run_pipeline on {c.corpus_root().name} (split={a.split}, {len(rows)} institutions)\n")
    print(render(rows, leaks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
