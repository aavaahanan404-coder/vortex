import copy
import json

import pytest
import yaml

from contracts import Finding
from harness import corpus_io as c
from modules import extract as X
from modules import rules as R

RB = R.load_rulebook()
NORMS = R.load_norms()
needs_ocr = pytest.mark.skipif(not X.ocr_available(), reason="Tesseract not installed")


def base_facts(**over):
    """A compliant engineering application (480 seats): every rule passes or does not apply."""
    f = {"application": {"inst_id": "I1", "date": "2026-02-02", "type": "existing", "programme_type": "engineering"},
         "norms": NORMS["engineering"], "submitted_docs": sorted(RB["required_documents"]), "required_docs": RB["required_documents"],
         "faculty_ids": ["F1", "F2"], "faculty_count": 32,
         "applied": [{"course": "CSE", "level": "UG", "intake": 240}, {"course": "ECE", "level": "UG", "intake": 240}],
         "infra": {"total": 80000, "pcs": 480}, "fire": {"valid_to": "2027-02-27"}, "building": {"valid_to": "2030-01-19"},
         "land": {"tenure": "lease", "lease_years": 60, "area_acres": 14.3}, "fin": {"income": 100, "expenditure": 40, "surplus": 60},
         "institution_names": {"fire_cert": "Shivalik Institute of Engineering & Technology"},
         "society_names": {"society_reg": "Shivalik Educational Foundation"},
         "registry": {"institution": {"name": "Shivalik Institute of Engineering & Technology"}, "society": {"name": "Shivalik Educational Foundation"},
                      "fire": {"inst_id": "I1", "valid_to": "2027-02-27"}, "building": {"inst_id": "I1", "valid_to": "2030-01-19"},
                      "land": {"holder_inst_id": "I1", "area_acres": 14.3}, "affiliations": {"CSE": 240, "ECE": 240},
                      "faculty_employment": {"F1": "I1", "F2": "I1"}}}
    for k, v in over.items():
        f[k] = v
    return f


def res(facts, rb=RB):
    return {r.id: r for r in R.evaluate(facts, rb)}


def test_rulebook_shape_and_honesty():
    assert [r["id"] for r in RB["rules"]] == [f"R{i:03d}" for i in range(1, 17)]
    assert all(r["handbook_ref"] == "TODO" for r in RB["rules"])                  # not pretending to cite clauses
    assert RB["norms_validated"] is False
    for r in RB["rules"]:
        Finding(r["id"], "rules", r["kind"], r["tier"], r["p"], "m", "d")           # kind/tier/p obey the contract
        assert r["message"].strip().endswith(".")
    for r in RB["rules"]:
        if r["id"] in ("R001", "R002", "R003", "R006"):
            assert r["kind"] == "soft", "norm-based rules stay soft while the norms are illustrative"
    assert "ILLUSTRATIVE" in open(R.DEFAULT_NORMS, encoding="utf-8").read().upper()      # the norms file keeps its warning


def test_compliant_application_passes_everything():
    r = res(base_facts())
    assert {k: v.result for k, v in r.items() if v.result not in ("pass",)} == {}


@pytest.mark.parametrize("rid,changes,detail", [
    ("R001", {"faculty_count": 31}, "faculty 31 vs required 32"),
    ("R002", {"infra": {"total": 79999, "pcs": 480}}, "built-up 79999 vs required 80000"),
    ("R003", {"infra": {"total": 80000, "pcs": 479}}, "pcs 479 vs required 480"),
    ("R004", {"fire": {"valid_to": "2026-02-01"}, "registry": {**base_facts()["registry"], "fire": {"inst_id": "I1", "valid_to": "2026-02-01"}}}, "fire valid_to 2026-02-01 vs application 2026-02-02"),
    ("R005", {"building": {"valid_to": "2025-12-31"}, "registry": {**base_facts()["registry"], "building": {"inst_id": "I1", "valid_to": "2025-12-31"}}}, "building valid_to 2025-12-31 vs application 2026-02-02"),
    ("R006", {"land": {"tenure": "lease", "lease_years": 29, "area_acres": 14.3}}, "lease 29y"),
    ("R007", {"applied": [{"course": "CSE", "level": "UG", "intake": 241}, {"course": "ECE", "level": "UG", "intake": 100}]}, "CSE"),
    ("R010", {"submitted_docs": ["society_reg"]}, "land_doc"),
    ("R011", {"registry": {**base_facts()["registry"], "faculty_employment": {"F1": "I1", "F2": "I2"}}}, "F2"),
    ("R012", {"registry": {**base_facts()["registry"], "land": {"holder_inst_id": "I2", "area_acres": 14.3}}}, "parcel held by I2"),
    ("R014", {"fin": {"income": 100, "expenditure": 40, "surplus": 61}}, "surplus 61 vs income - expenditure 60"),
    ("R015", {"registry": {**base_facts()["registry"], "fire": {"inst_id": "I2", "valid_to": "2027-02-27"}}}, "certificate issued to another institution"),
    ("R016", {"fire": {"valid_to": "2028-02-27"}}, "fire.valid_to"),
])
def test_each_rule_fails_with_a_plain_detail(rid, changes, detail):
    r = res(base_facts(**changes))
    assert r[rid].result == "fail" and detail in r[rid].detail, r[rid]
    assert [x.id for x in r.values() if x.result == "fail"] == [rid], "only the rule under test should fail"


def test_boundaries_pass():
    f = base_facts(faculty_count=32, fire={"valid_to": "2026-02-02"})                       # exactly enough, valid on the day
    f["land"]["lease_years"] = 30
    r = res(f)
    assert r["R001"].result == r["R004"].result == r["R006"].result == "pass"


def test_not_applicable_and_not_evaluable_are_distinct_from_pass_and_fail():
    f = base_facts()
    f["land"] = {"tenure": "owned", "lease_years": None, "area_acres": 14.3}
    assert res(f)["R006"].result == "not_applicable"
    g = base_facts(faculty_count=None, faculty_ids=None)
    r = res(g)
    assert r["R001"].result == "not_evaluable" and "faculty_count" in r["R001"].missing
    assert r["R011"].result == "not_evaluable"
    g = base_facts(); g["registry"]["fire"] = None                                          # registry unavailable
    r = res(g)
    assert r["R013"].result == r["R015"].result == r["R016"].result == "not_evaluable"
    g = base_facts(); g["registry"]["fire"] = {}                                            # registry says "not found"
    r = res(g)
    assert r["R013"].result == "fail" and r["R015"].result == "pass"
    h = base_facts(applied=[]); assert res(h)["R001"].result == "not_evaluable"             # no intake = cannot compute the requirement


def test_name_rule_tolerates_abbreviations_but_not_another_institution():
    f = base_facts(institution_names={"fire_cert": "Shivalik Inst. of Engg. & Technology", "aff_letter": "SHIVALIK INSTITUTE OF ENGINEERING & TECHNOLOGY"})
    assert res(f)["R008"].result == "pass"
    f = base_facts(institution_names={"building_plan": "Vidya Sagar Institute of Engineering & Technology"})
    r = res(f)["R008"]
    assert r.result == "fail" and r.detail == "building_plan"
    f = base_facts(institution_names={"affidavit": "of Technology"})                         # truncated read: cannot judge, no accusation
    assert res(f)["R008"].result == "pass"
    f = base_facts(society_names={"land_doc": "Another Trust Foundation"})
    assert res(f)["R008"].result == "fail"


def test_similar_function_directly():
    sim = R.make_similar(RB["abbreviations"], RB["name_similarity"], set(RB["generic_name_words"]))
    assert sim("Hoysala Inst. of Technology", "Hoysala Institute of Technology")
    assert sim("Chola Polytechnic Coll.", "Chola Polytechnic College")
    assert not sim("Vidya Sagar Institute of Engineering", "Hoysala Institute of Engineering")
    assert sim("Institute of Technology", "Hoysala Institute of Technology") and not sim("", "x")


# ----------------------------------------------------------------------------- safety
@pytest.mark.parametrize("expr", ["__import__('os').system('echo hi')", "().__class__.__bases__", "open('/etc/passwd')", "9**9**9",
                                  "'a' * 10000000", "[x for x in range(10**9)]", "faculty_count.__class__", "exec('1')", "undefined_name + 1"])
def test_hostile_expressions_are_not_executed(expr):
    rb = copy.deepcopy(RB)
    rb["rules"] = [{"id": "RX", "name": "x", "tier": 3, "expr": expr}]
    r = R.evaluate(base_facts(), rb)[0]
    assert r.result == "not_evaluable" and r.error


def test_over_long_expressions_and_one_broken_rule_do_not_stop_the_others():
    rb = copy.deepcopy(RB)
    rb["rules"] = [{"id": "RX", "name": "x", "tier": 3, "expr": "1 == 1 and " + "1 == 1 and " * 100 + "True"}] + rb["rules"][:2]
    out = R.evaluate(base_facts(), rb)
    assert out[0].result == "not_evaluable" and "longer than" in out[0].error and [r.result for r in out[1:]] == ["pass", "pass"]


def test_functions_are_a_whitelist():
    assert set(R.FUNCTIONS) >= {"ceil", "len", "sum", "exists", "normalize"} and "open" not in R.FUNCTIONS and "__import__" not in R.FUNCTIONS
    rb = copy.deepcopy(RB)
    rb["rules"] = [{"id": "RX", "name": "x", "tier": 3, "expr": "eval('1')"}]
    assert R.evaluate(base_facts(), rb)[0].result == "not_evaluable"


# ----------------------------------------------------------------------------- facts
def test_build_facts_parses_money_dates_and_drops_unreliable_ocr():
    assert R._money("Rs. 4,96,03,427") == 49603427 and R._money("(Rs. 5,00)") == -500 and R._money("-Rs. 1,000") == -1000 and R._money("") is None
    assert R._iso("27-Feb-2028") == "2028-02-27" and R._iso("13 February 2018") == "2018-02-13" and R._iso("junk") is None
    docs = {"fire_cert": {"cert_no": {"value": "C1", "source": "text_layer", "conf": 1.0},
                          "valid_to": {"value": "08-Jan-2027", "source": "text_layer", "conf": 0.5, "flags": ["covered_text"]},
                          "issued_on": {"value": "24-Jan-2024", "source": "ocr", "conf": 0.4}},
            "financial": {"fin.income.a": {"value": "Rs. 1,00", "source": "ocr", "conf": 0.9}, "fin.expenditure.a": {"value": "Rs. 40", "source": "ocr", "conf": 0.9},
                          "fin.surplus.a": {"value": "Rs. 60", "source": "ocr", "conf": 0.9}},
            "affidavit": {"applied[0].course": {"value": "CSE", "source": "ocr", "conf": 0.9}, "applied[0].intake": {"value": "240", "source": "ocr", "conf": 0.9},
                          "applied[1].course": {"value": "ECE", "source": "ocr", "conf": 0.2}, "applied[1].intake": {"value": "240", "source": "ocr", "conf": 0.9}}}
    f = R.build_facts({"inst_id": "I1", "date": "02/02/2026", "type": "existing", "programme_type": "engineering"}, docs, {}, NORMS)
    assert f["fire"]["valid_to"] == "2027-01-08"            # covered-text field (conf 0.5) is kept: it is the visible value, text layer is exact
    assert f["fire"]["issued_on"] is None                    # an unreliable OCR read is not a fact
    assert f["fin"] == {"income": 100, "expenditure": 40, "surplus": 60} and f["application"]["date"] == "2026-02-02"
    assert f["applied"] == [{"course": "CSE", "level": None, "intake": 240}]          # the half-read row is dropped whole
    json.dumps(f)                                             # plain JSON: snapshots are storable


# ----------------------------------------------------------------------------- findings, simulator, CLI
def test_findings_follow_the_rulebook():
    fs = {f.id: f for f in R.analyze(base_facts(faculty_count=31, fire={"valid_to": "2028-02-27"}), RB, "app1")}
    assert fs["R001"].kind == "soft" and fs["R001"].tier == 3 and fs["R001"].evidence["norms_validated"] is False
    assert fs["R016"].kind == "hard" and fs["R016"].tier == 2 and fs["R016"].p == 0.9 and "differs from the issuing registry" in fs["R016"].message
    g = base_facts(); g["registry"]["fire"] = None
    ne = [f for f in R.analyze(g, RB) if f.id == "rule_not_evaluable"]
    assert ne and ne[0].kind == "soft" and ne[0].evidence["rule_id"] == "R013"
    assert [f.id for f in R.analyze(base_facts(), RB)] == ["rules_ok"]


def test_rule_change_impact_simulator():
    new = copy.deepcopy(RB)
    for r in new["rules"]:
        if r["id"] == "R006":
            r["expr"] = "land.lease_years >= 90"                                   # candidate: much stricter lease minimum
    cases = {"a": base_facts(), "b": base_facts(land={"tenure": "lease", "lease_years": 30, "area_acres": 14.3}),
             "c": base_facts(land={"tenure": "owned", "lease_years": None, "area_acres": 14.3})}
    out = R.simulate(RB, new, cases)
    assert out["cases"] == 3 and out["by_rule"] == {"R006": {"pass->fail": 2}}
    assert {(x["case"], x["old"], x["new"]) for x in out["changed"]} == {("a", "pass", "fail"), ("b", "pass", "fail")}
    assert R.simulate(RB, RB, cases)["changed"] == []


def test_cli(tmp_path, capsys):
    p = tmp_path / "f.json"; p.write_text(json.dumps(base_facts(faculty_count=1)))
    assert R.main([str(p)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert next(x for x in out["results"] if x["id"] == "R001")["result"] == "fail" and any(f["id"] == "R001" for f in out["findings"])


# ----------------------------------------------------------------------------- corpus ground truth
@needs_ocr
def test_rules_never_accuse_wrongly_on_the_corpus():
    """Against institutions.json expected_rules: no false failure, no failure turned into a pass; the rest may only be 'declined'."""
    from harness import run_rules as RR
    _, rows = RR.evaluate()
    false_fail = [(r["inst"], r["rule"]) for r in rows if r["got"] == "fail" and r["want"] != "fail"]
    missed = [(r["inst"], r["rule"]) for r in rows if r["want"] == "fail" and r["got"] == "pass"]
    assert len(rows) == 1600 and not false_fail and not missed, (false_fail, missed)
    agree = sum(r["got"] == r["want"] for r in rows)
    assert agree / len(rows) >= 0.98
    details = [(r["want_detail"], r["got_detail"]) for r in rows if r["want"] == "fail" and r["got"] == "fail" and r["want_detail"]]
    assert details and all(a.lower() == b.lower() for a, b in details)
    # the only way to differ from the truth is to decline, and at scenario institutions that is exactly the registry outage
    assert all(r["got"] == "not_evaluable" for r in rows if r["got"] != r["want"])
