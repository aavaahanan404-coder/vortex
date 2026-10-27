"""Findings -> {score, category, reasons}.

Input : list[Finding] (or their JSON dicts), config/scoring.yaml.
Output: {"score": float, "category": str, "reasons": [{finding_id, message, ...}]}
score = max(max hard p, noisy-OR over soft p*weight). Info findings never raise the score.
Category high_anomaly uses the score excluding applicant-fixable soft findings (two re-scan
requests are not an anomaly); the reported score includes them. Categories (precedence): high_anomaly > needs_human > applicant_fixable > auto_clear_candidate;
a mid-band score with a non-fixable cause is routed to needs_human.
Every reason traces to a finding id. Deliberately does NOT decide accept/reject; Tier-4 findings
are soft by contract and can raise the score only via the noisy-OR.
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path
from typing import Iterable, Optional, Union

import yaml

from contracts import Finding

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"


def load_config(path=None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def score_findings(findings: Iterable[Union[Finding, dict]], cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    fs = [f if isinstance(f, Finding) else Finding.from_json(f) for f in findings]
    w = cfg["weights"]; dw = cfg["default_weight"]
    hard = [f for f in fs if f.kind == "hard"]
    soft = [f for f in fs if f.kind == "soft"]
    hard_p = max((f.p for f in hard), default=0.0)
    keep = 1.0
    contrib = {}
    for f in soft:
        c = min(1.0, f.p * w.get(f.id, dw)); contrib[id(f)] = c; keep *= (1 - c)
    soft_p = 1 - keep
    score = round(max(hard_p, soft_p), 4)
    reasons = []
    for f in sorted(fs, key=lambda f: (f.kind != "hard", f.kind != "soft", -f.p)):
        reasons.append({"finding_id": f.id, "module": f.module, "kind": f.kind, "tier": f.tier,
                        "p": f.p, "message": f.message, "bbox": list(f.bbox) if f.bbox else None,
                        "contribution": round(f.p if f.kind == "hard" else contrib.get(id(f), 0.0), 4)})
    active = [f for f in fs if f.kind in ("hard", "soft")]
    fix = set(cfg["applicant_fixable_ids"])
    keep2 = 1.0
    for f in soft:
        if f.id not in fix:
            keep2 *= 1 - contrib[id(f)]
    anomaly = max(hard_p, 1 - keep2)   # applicant-fixable findings (re-scan requests) never make an anomaly
    if anomaly >= cfg["high_anomaly_threshold"]:
        cat = "high_anomaly"
    elif any(f.id in cfg["needs_human_ids"] for f in fs):
        cat = "needs_human"
    elif active and all(f.kind == "soft" and f.id in cfg["applicant_fixable_ids"] for f in active):
        cat = "applicant_fixable"
    elif not active and score < cfg["auto_clear_max_score"]:
        cat = "auto_clear_candidate"
    else:
        cat = "needs_human"
    return {"score": score, "category": cat, "reasons": reasons}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.score")
    ap.add_argument("findings_json"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    out = score_findings(json.load(open(a.findings_json)), load_config(a.config))
    json.dump(out, sys.stdout, indent=2); print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
