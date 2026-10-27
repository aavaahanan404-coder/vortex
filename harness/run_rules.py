"""Score adapters and the rule engine against the corpus ground truth.

Usage: python -m harness.run_rules [--corpus C] [--split all|train|val|test] [--ignore-scenarios] [--limit N] [--verbose]
Per institution: fields come from the extract module (text layer / OCR) for every submitted document, each document is verified
with the adapters against the mock registries (world.sqlite), the registry view and the canonical facts are built, and all 16 rules
are evaluated. Scored against
  * manifest `expected_adapter` of every document (status, and reason where given), and
  * institutions.json `expected_rules` (result per rule; for failures also the detail text).
Scenario institutions (always_503 / flaky_then_ok / pending_then_ok / ambiguous_match) deliberately make a registry unavailable or
unsure: a rule that needs it is then not_evaluable, which differs from the ground truth ("pass") and is listed separately.
--ignore-scenarios runs the registries without those behaviours (the ground-truth world).
"""
from __future__ import annotations

import argparse
import collections
import re

from harness import corpus_io as c
from modules import adapters as A
from modules import extract as X
from modules import rules as R

_CACHE: dict = {}


def fields_of(e) -> dict:
    key = (str(c.corpus_root()), e["doc_id"])
    if key not in _CACHE:
        try:
            _CACHE[key] = X.extract(e["path"], e["doc_id"], e["doc_type"])[0].fields
        except Exception:
            _CACHE[key] = {}
    return _CACHE[key]


def run_institution(inst: dict, docs: list[dict], source, rb, norms, sleep=lambda s: None):
    fields = {e["doc_type"]: fields_of(e) for e in docs}
    results = {dt: A.verify_document(dt, f, inst["application_date"], inst["inst_id"], source, sleep=sleep) for dt, f in fields.items()}
    reg = A.registry_facts(results, source, inst["inst_id"], sleep=sleep)
    facts = R.build_facts({"inst_id": inst["inst_id"], "date": inst["application_date"], "type": inst["application_type"],
                           "programme_type": inst["type"]}, fields, reg, norms, rb["required_documents"])
    return fields, results, facts, R.evaluate(facts, rb)


def evaluate(split=None, ignore_scenarios=False, limit=None):
    rb, norms = R.load_rulebook(), R.load_norms()
    src = A.WorldSource(c.corpus_root() / "world" / "world.sqlite", scenarios=not ignore_scenarios)
    by_inst = collections.defaultdict(list)
    for e in c.iter_docs():
        by_inst[e["inst_id"]].append(e)
    ad_rows, rule_rows = [], []
    n = 0
    try:
        for inst in c.institutions():
            if split not in (None, "all") and c.split_of(inst["inst_id"]) != split:
                continue
            n += 1
            if limit and n > limit:
                break
            docs = by_inst[inst["inst_id"]]
            _, results, facts, res = run_institution(inst, docs, src, rb, norms)
            scenario = bool(inst.get("scenario_flag")) and not ignore_scenarios
            for e in docs:
                exp = e["expected_adapter"]; got = results[e["doc_type"]]
                ad_rows.append({"doc_id": e["doc_id"], "doc_type": e["doc_type"], "want": exp["status"], "got": got.status,
                                "want_reason": exp.get("reason", ""), "got_reason": got.reason, "attempts": got.attempts})
            for r in res:
                want = inst["expected_rules"][r.id]
                rule_rows.append({"inst": inst["inst_id"], "rule": r.id, "want": want["result"], "got": r.result,
                                  "want_detail": want.get("detail", ""), "got_detail": r.detail, "scenario": scenario,
                                  "missing": r.missing, "error": r.error})
    finally:
        src.close()
    return ad_rows, rule_rows


def norm_detail(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def render(ad_rows, rule_rows, verbose=False) -> str:
    out = []
    tot = len(ad_rows)
    ok = [r for r in ad_rows if r["got"] == r["want"]]
    out.append(f"### adapters: status agrees with the manifest for {len(ok)}/{tot} documents\n")
    out.append("| document type | expected status | n | agree |\n|---|---|---|---|")
    g = collections.defaultdict(lambda: [0, 0])
    for r in ad_rows:
        t = g[(r["doc_type"], r["want"])]; t[1] += 1; t[0] += r["got"] == r["want"]
    for (dt, st), (a, n) in sorted(g.items()):
        out.append(f"| {dt} | {st} | {n}{' (weak)' if n < 10 else ''} | {a} |")
    reason_rows = [r for r in ad_rows if r["want_reason"] and r["want"] not in ("verified", "not_applicable") and r["got"] == r["want"]]
    rok = sum(1 for r in reason_rows if norm_detail(r["got_reason"]) == norm_detail(r["want_reason"]) or r["want_reason"].startswith("scenario_flag"))
    out.append(f"\nReason text matches for {rok}/{len(reason_rows)} non-verified documents with a stated reason.")
    out.append(f"Retries: {sum(1 for r in ad_rows if r['attempts'] > 1)} documents needed more than one attempt (flaky / unavailable scenarios).")
    bad = [r for r in ad_rows if r["got"] != r["want"]]
    cmA = collections.Counter((r["want"], r["got"]) for r in bad)
    out.append("Adapter disagreements (want -> got): " + ("; ".join(f"{w}->{g} x{n}" for (w, g), n in sorted(cmA.items())) or "none"))
    if bad:
        out.append("\nDisagreements: " + "; ".join(f"{r['doc_id']} want {r['want']} got {r['got']} ({r['got_reason']})" for r in bad[:10]))
    out.append("\n### rules: result agrees with the ground truth\n")
    out.append("| rule | pass | fail | n/a | not_evaluable | agree | disagree (non-scenario) | disagree (scenario institutions) |\n|---|---|---|---|---|---|---|---|")
    by = collections.defaultdict(list)
    for r in rule_rows:
        by[r["rule"]].append(r)
    tot_ok = tot_n = 0
    for rid in sorted(by):
        rs = by[rid]
        c_ = collections.Counter(x["want"] for x in rs)
        agree = [x for x in rs if x["got"] == x["want"]]
        dis = [x for x in rs if x["got"] != x["want"]]
        out.append(f"| {rid} | {c_['pass']} | {c_['fail']} | {c_['not_applicable']} | {c_['not_evaluable']} | {len(agree)}/{len(rs)} | "
                   f"{sum(1 for x in dis if not x['scenario'])} | {sum(1 for x in dis if x['scenario'])} |")
        tot_ok += len(agree); tot_n += len(rs)
    out.append(f"\nAll rules: {tot_ok}/{tot_n} agree.")
    fails = [r for r in rule_rows if r["want"] == "fail" and r["got"] == "fail"]
    dm = sum(1 for r in fails if r["want_detail"] and norm_detail(r["got_detail"]) == norm_detail(r["want_detail"]))
    out.append(f"Failure detail text matches for {dm}/{sum(1 for r in fails if r['want_detail'])} failed rules with a stated detail.")
    dis = [r for r in rule_rows if r["got"] != r["want"]]
    cm = collections.Counter((r["want"], r["got"], "scenario" if r["scenario"] else "ocr/other") for r in dis)
    false_fail = sum(n for (w, g, _), n in cm.items() if g == "fail" and w != "fail")
    missed = sum(n for (w, g, _), n in cm.items() if w == "fail" and g == "pass")
    ne = sum(n for (w, g, _), n in cm.items() if g == "not_evaluable")
    out.append(f"\nDisagreements by kind: **false failures {false_fail}**, **failures turned to pass {missed}**, "
               f"declined as not_evaluable {ne} (of which at scenario institutions {sum(n for (w, g, k), n in cm.items() if g == 'not_evaluable' and k == 'scenario')}).")
    out.append("(want -> got, cause): " + "; ".join(f"{w}->{g} [{k}] x{n}" for (w, g, k), n in sorted(cm.items())))
    if dis and verbose:
        out.append("\nDisagreements:\n" + "\n".join(f"- {r['inst']} {r['rule']}: want {r['want']} got {r['got']} {r['missing'] or r['error'] or ''} {'[scenario]' if r['scenario'] else ''} {r['got_detail']}" for r in dis))
    elif dis:
        out.append("\nDisagreements (first 12): " + "; ".join(f"{r['inst']} {r['rule']} want {r['want']} got {r['got']}{' [scenario]' if r['scenario'] else ''}" for r in dis[:12]))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--split", default="all"); ap.add_argument("--ignore-scenarios", action="store_true")
    ap.add_argument("--limit", type=int); ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    ad, ru = evaluate(a.split, a.ignore_scenarios, a.limit)
    print(f"## run_rules on {c.corpus_root().name} (split={a.split}, scenarios {'off' if a.ignore_scenarios else 'on'}, {len({r['inst'] for r in ru})} institutions)\n")
    print(render(ad, ru, a.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
