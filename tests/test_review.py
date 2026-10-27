import copy
import json
import random

import pytest

from modules import review as R

POOL = ["ana", "ben", "chi", "dev", "eli"]
CFG = R.load_config()


def case(score=0.9, category="high_anomaly"):
    return R.open_case("C1", score, category, CFG)


def assigned(seed=1, **kw):
    return R.assign(case(**kw), POOL, random.Random(seed), CFG)


def test_routing_threshold():
    assert R.needs_double_review(0.6, "needs_human", CFG) and R.needs_double_review(0.1, "high_anomaly", CFG)
    assert not R.needs_double_review(0.59, "needs_human", CFG)
    assert len(R.assign(case(0.2, "needs_human"), POOL, random.Random(0), CFG)["assigned"]) == 1


def test_two_distinct_random_reviewers_reproducible_per_seed():
    s = assigned(seed=3)
    assert len(s["assigned"]) == 2 and len(set(s["assigned"])) == 2 and s["phase"] == "blind" and set(s["assigned"]) <= set(POOL)
    assert assigned(seed=3)["assigned"] == s["assigned"]                       # rng injected: reproducible
    seen = {tuple(assigned(seed=k)["assigned"]) for k in range(30)}
    assert len(seen) > 5                                                        # genuinely random across seeds
    with pytest.raises(R.ReviewError):
        R.assign(case(), ["only_one"], random.Random(0), CFG)
    with pytest.raises(R.ReviewError):
        R.assign(s, POOL, random.Random(0), CFG)                                # cannot assign twice


def test_blind_until_both_submit_then_agreement_resolves():
    s = assigned(); a, b = s["assigned"]
    s1 = R.submit(s, a, "reject", "date was altered", POOL, random.Random(0), CFG)
    va, vb = R.view(s1, a, CFG), R.view(s1, b, CFG)
    assert va["your_decision"] == "reject" and va["score"] is None and va["category"] is None and va["reasons"] is None
    assert vb["your_decision"] is None and vb["submitted"] == 1 and "decisions" not in vb and "notes" not in vb and vb["score"] is None
    s2 = R.submit(s1, b, "reject", "agree", POOL, random.Random(0), CFG)
    assert s2["phase"] == "resolved" and s2["outcome"] == "reject"
    v = R.view(s2, a, CFG)
    assert v["score"] == 0.9 and v["decisions"] == {a: "reject", b: "reject"} and v["notes"][b] == "agree" and v["outcome"] == "reject"


def test_disagreement_goes_to_an_independent_third_who_stays_blind():
    s = assigned(); a, b = s["assigned"]
    s = R.submit(R.submit(s, a, "approve", "fine", POOL, random.Random(5), CFG), b, "reject", "forged", POOL, random.Random(5), CFG)
    assert s["phase"] == "awaiting_third" and len(s["assigned"]) == 3 and s["assigned"][2] not in (a, b) and s["outcome"] is None
    third = s["assigned"][2]
    assert R.view(s, a, CFG)["decisions"] == {a: "approve", b: "reject"}                 # the first two now see each other
    vt = R.view(s, third, CFG)
    assert vt["score"] is None and "decisions" not in vt and vt["your_decision"] is None   # the tie-breaker is still blind
    done = R.submit(s, third, "reject", "", POOL, random.Random(0), CFG)
    assert done["phase"] == "resolved" and done["outcome"] == "reject" and R.view(done, third, CFG)["decisions"][a] == "approve"


def test_three_different_decisions_go_to_a_committee():
    s = assigned(); a, b = s["assigned"]
    s = R.submit(R.submit(s, a, "approve", "", POOL, random.Random(1), CFG), b, "reject", "", POOL, random.Random(1), CFG)
    s = R.submit(s, s["assigned"][2], "request_info", "", POOL, random.Random(1), CFG)
    assert s["phase"] == "committee" and s["outcome"] is None                              # no machine tie-break, a person decides
    with pytest.raises(R.ReviewError):
        R.submit(s, a, "approve", "", POOL, random.Random(1), CFG)


def test_refusals():
    s = assigned(); a, b = s["assigned"]
    with pytest.raises(R.ReviewError):
        R.submit(s, "mallory", "approve", "", POOL, None, CFG)                              # not assigned
    with pytest.raises(R.ReviewError):
        R.submit(s, a, "auto_clear", "", POOL, None, CFG)                                   # not a human decision
    s1 = R.submit(s, a, "approve", "", POOL, None, CFG)
    with pytest.raises(R.ReviewError):
        R.submit(s1, a, "reject", "", POOL, None, CFG)                                      # no second vote, no changing a vote
    with pytest.raises(R.ReviewError):
        R.view(s1, "mallory", CFG)                                                          # outsiders see nothing
    with pytest.raises(R.ReviewError):
        R.submit(case(), a, "approve", "", POOL, None, CFG)                                 # unassigned case takes no decisions


def test_single_review_and_no_state_mutation():
    s = R.assign(case(0.2, "needs_human"), POOL, random.Random(0), CFG)
    snapshot = copy.deepcopy(s)
    out = R.submit(s, s["assigned"][0], "approve", "ok", POOL, None, CFG)
    assert s == snapshot and out["phase"] == "resolved" and out["outcome"] == "approve"     # functions return new state
    json.dumps(out)


def test_the_machine_never_decides():
    s = assigned()
    assert s["outcome"] is None and s["decisions"] == {} and set(CFG["decisions"]) == {"approve", "reject", "request_info"}
