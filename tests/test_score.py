from contracts import Finding
from modules.score import score_findings


def F(id, kind, p, tier=1): return Finding(id, "t", kind, tier, p, "m", "D")


def test_empty_is_auto_clear():
    r = score_findings([]); assert r["category"] == "auto_clear_candidate" and r["score"] == 0


def test_info_only_clear():
    assert score_findings([F("qr_valid", "info", 0.0)])["category"] == "auto_clear_candidate"


def test_hard_dominates():
    r = score_findings([F("qr_bad_signature", "hard", 0.99), F("blurry", "soft", 0.6, 4)])
    assert r["score"] == 0.99 and r["category"] == "high_anomaly"
    assert r["reasons"][0]["finding_id"] == "qr_bad_signature"


def test_noisy_or():
    r = score_findings([F("a", "soft", 0.5, 3), F("b", "soft", 0.5, 3)])
    assert abs(r["score"] - 0.75) < 1e-9


def test_applicant_fixable():
    r = score_findings([F("blurry", "soft", 0.6, 4), F("tilted", "soft", 0.6, 4)])
    assert r["category"] == "applicant_fixable"


def test_needs_human():
    assert score_findings([F("source_unavailable", "info", 0.0, 2)])["category"] == "needs_human"
    assert score_findings([F("odd", "soft", 0.4, 3)])["category"] == "needs_human"


def test_reasons_trace_ids():
    fs = [F("x", "soft", 0.3, 3)]
    assert {r["finding_id"] for r in score_findings(fs)["reasons"]} == {"x"}
