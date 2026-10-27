import copy

import pymupdf
import pytest
import yaml

from modules import pipeline as PL
from modules import rule_import as RI
from modules import rules as R
from tests.test_rules import base_facts

RB = R.load_rulebook()
ND = yaml.safe_load(open(RI.CONFIG_DIR / "norms.yaml", encoding="utf-8"))


def pdf(tmp_path, norm_rows=(), rule_rows=(), meta=None, name="u.pdf"):
    p = tmp_path / name
    RI.write_rule_pdf(p, meta or {}, [list(r) for r in norm_rows], [list(r) for r in rule_rows])
    return p


def plan_of(p, current=None):
    return RI.build_plan(RI.read_pdf(p), RB, ND, current)


def by_key(plan):
    return {(c["target"], c.get("programme_type") or c.get("rule"), c["key"]): c for c in plan["changes"]}


def test_template_round_trip_changes_nothing_and_spans_pages(tmp_path):
    e_rb, _ = RI.effective(RB, ND)
    n, r = RI.template_rows(RB, ND)
    p = tmp_path / "t.pdf"
    RI.write_rule_pdf(p, {"norms_version": "x", "validated": False}, n, r)
    assert pymupdf.open(p).page_count >= 2                                   # the rules table is continued over pages (header repeated)
    parsed = RI.read_pdf(p)
    assert len(parsed["norm_rows"]) == 12 and len(parsed["rule_rows"]) == 64
    plan = RI.build_plan(parsed, RB, ND)
    assert plan["changes"] == [] and plan["rejected"] == [] and plan["unchanged"] == 12 + 64


def test_changes_go_up_and_down_with_direction_and_percent(tmp_path):
    p = pdf(tmp_path, [("Engineering", "Students per faculty", "12", "Clause 5.1"), ("MBA", "builtup_sqm_per_60_intake", "1,500", ""),
                       ("Management", "pcs_per_intake_seat", "0.75", "")],
            [("R001", "p", "80%", ""), ("r2", "kind", "hard", "Clause 5.2")], {"norms_version": "hb-2025", "validated": False})
    plan = plan_of(p)
    ch = by_key(plan)
    a = ch[("norm", "engineering", "students_per_faculty")]
    assert (a["old"], a["new"], a["direction"], a["pct"], a["ref"]) == (15, 12, "decrease", -20.0, "Clause 5.1")
    b = ch[("norm", "mba", "builtup_sqm_per_60_intake")]
    assert (b["old"], b["new"], b["direction"]) == (2000, 1500, "decrease") and b["pct"] == -25.0
    c = ch[("norm", "mba", "pcs_per_intake_seat")]
    assert (c["old"], c["new"], c["direction"]) == (0.5, 0.75, "increase") and c["pct"] == 50.0 and c["needs_ack"]      # 'Management' = mba; +50% needs a yes
    assert ch[("rule", "R001", "p")]["direction"] == "increase" and ch[("rule", "R001", "p")]["new"] == 0.8
    k = ch[("rule", "R002", "kind")]
    assert (k["old"], k["new"], k["direction"]) == ("soft", "hard", "changed")
    assert ch[("rule", "R002", "handbook_ref")]["new"] == "Clause 5.2"          # the reference column fills the clause reference
    assert plan["counts"]["increase"] == 2 and plan["counts"]["decrease"] == 2
    ov = plan["new_overrides"]
    assert ov["norms"]["engineering"] == {"students_per_faculty": 12} and ov["meta"]["norms_version"] == "hb-2025"
    assert RB["rules"][0]["p"] == 0.7 and ND["programme_types"]["engineering"]["students_per_faculty"] == 15      # the base is untouched


def test_bad_rows_are_rejected_one_by_one_with_page_and_reason(tmp_path):
    p = pdf(tmp_path, [("Engineering", "students_per_faculty", "15.5", ""), ("Engineering", "colour_of_walls", "3", ""), ("Medical", "students_per_faculty", "10", ""),
                       ("Engineering", "min_lease_years", "thirty", ""), ("Engineering", "pcs_per_intake_seat", "-1", ""), ("Engineering", "builtup_sqm_per_60_intake", "9000", "")],
            [("R999", "p", "0.5", ""), ("R001", "p", "1.5", ""), ("R001", "colour", "red", ""), ("R004", "kind", "maybe", ""), ("R003", "severity", "critical", "")])
    plan = plan_of(p)
    why = {r["row"]: r["reason"] for r in plan["rejected"]}
    assert len(plan["rejected"]) == 9 and all(r["page"] == 1 for r in plan["rejected"])
    assert "whole number" in why["Engineering | students_per_faculty | 15.5"]
    assert "unknown norm" in why["Engineering | colour_of_walls | 3"] and "unknown programme" in why["Medical | students_per_faculty | 10"]
    assert "plain number" in why["Engineering | min_lease_years | thirty"] and "greater than zero" in why["Engineering | pcs_per_intake_seat | -1"]
    assert any("unknown rule" in v for v in why.values()) and any("p must be" in v for v in why.values()) and any("unknown setting" in v for v in why.values())
    assert {(c["key"]) for c in plan["changes"]} == {"builtup_sqm_per_60_intake", "severity"}                    # the valid rows still count


def test_conflicting_duplicates_are_rejected_not_resolved_by_order(tmp_path):
    p = pdf(tmp_path, [("Engineering", "min_lease_years", "25", ""), ("Engineering", "min_lease_years", "20", ""), ("Engineering", "students_per_faculty", "16", ""), ("Engineering", "students_per_faculty", "16", "")])
    plan = plan_of(p)
    assert [r["reason"] for r in plan["rejected"]] == ["the same norm appears twice with different values"] * 2
    assert [c["key"] for c in plan["changes"]] == ["students_per_faculty"]                  # an identical repeat is counted once


def test_risky_changes_need_acknowledgement(tmp_path):
    p = pdf(tmp_path, [("Engineering", "min_lease_years", "10", "")], [("R010", "enabled", "no", ""), ("R004", "kind", "soft", ""), ("R001", "p", "0.2", "")])
    plan = plan_of(p)
    reasons = {n["label"].split(":")[0] + ":" + n["why"][:12] for n in plan["needs_ack"]}
    assert len(plan["needs_ack"]) == 4 and any("switches this check off" in n["why"] for n in plan["needs_ack"])
    assert any("hard check into a soft" in n["why"] for n in plan["needs_ack"]) and reasons


def test_effective_layers_overrides_and_drops_a_restored_value(tmp_path):
    plan = plan_of(pdf(tmp_path, [("Engineering", "students_per_faculty", "12", "")], [("R001", "p", "0.9", ""), ("R010", "enabled", "no", "")], {"validated": True, "norms_version": "v2"}))
    rb, norms = RI.effective(RB, ND, plan["new_overrides"])
    assert norms["engineering"]["students_per_faculty"] == 12 and norms["mba"]["students_per_faculty"] == 15
    assert {r["id"]: r for r in rb["rules"]}["R001"]["p"] == 0.9 and "R010" not in {r["id"] for r in rb["rules"]}
    assert rb["norms_validated"] is True and rb["norms_version"] == "v2" and len(RB["rules"]) == 16
    back = plan_of(pdf(tmp_path, [("Engineering", "students_per_faculty", "15", "")], [("R001", "p", "0.7", ""), ("R010", "enabled", "yes", "")], name="b.pdf"), plan["new_overrides"])
    assert back["new_overrides"]["norms"] == {} and back["new_overrides"]["rules"] == {}                         # equal to the shipped value: no override left
    assert {c["direction"] for c in back["changes"]} == {"increase", "decrease", "changed"}


def test_template_of_a_changed_state_shows_a_switched_off_rule_and_round_trips(tmp_path):
    plan = plan_of(pdf(tmp_path, [("Engineering", "students_per_faculty", "18", "Clause 5.1")], [("R010", "enabled", "no", ""), ("R001", "p", "0.8", "Clause 5.1")]))
    ov = plan["new_overrides"]
    n, r = RI.template_rows(RB, ND, ov)
    assert ["R010", "enabled", "no", ""] in r and ["engineering", "students_per_faculty", 18, "Clause 5.1"] in n and ["R001", "p", 0.8, ""] in r and ["R001", "kind", "soft", "Clause 5.1"] in r
    p = tmp_path / "t2.pdf"
    RI.write_rule_pdf(p, {}, n, r)
    again = RI.build_plan(RI.read_pdf(p), RB, ND, ov)
    assert again["changes"] == [] and again["rejected"] == []                         # the template of the state in force changes nothing
    on = RI.build_plan(RI.read_pdf(pdf(tmp_path, [], [("R010", "enabled", "yes", "")], name="on.pdf")), RB, ND, ov)
    assert [(c["rule"], c["old"], c["new"]) for c in on["changes"]] == [("R010", False, True)]


def test_unstructured_and_non_pdf_files_get_a_plain_refusal(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 80), "Approval Process Handbook. Institutions shall maintain one faculty member for every fifteen students.")
    p = tmp_path / "prose.pdf"
    doc.save(str(p))
    with pytest.raises(RI.RuleImportError, match="unstructured"):
        RI.read_pdf(p)
    with pytest.raises(RI.RuleImportError, match="PDF"):
        RI.read_pdf(b"not a pdf at all")


def test_store_stage_apply_history_stale_plan_and_restore(tmp_path):
    st = RI.RuleStore(tmp_path / "rules")
    up1 = pdf(tmp_path, [("Engineering", "students_per_faculty", "12", "")], [("R010", "enabled", "no", "")], name="a.pdf")
    up2 = pdf(tmp_path, [("MBA", "min_lease_years", "28", "")], name="b.pdf")
    s1 = st.stage({**plan_of(up1), "source": {"name": "a.pdf"}}, up1.read_bytes())
    s2 = st.stage(plan_of(up2), up2.read_bytes())
    assert st.version() == 0 and st.history() == []
    with pytest.raises(RI.RuleImportError, match="explicit confirmation"):
        st.apply(s1, "adm", False)
    assert st.apply(s1, "adm", True, "2026-03-01T00:00:00+00:00")["version"] == 1
    with pytest.raises(RI.RuleImportError, match="changed after this preview"):
        st.apply(s2, "adm", False)                                                    # staged against version 0
    h = st.history()
    assert h[0]["version"] == 1 and h[0]["by"] == "adm" and h[0]["source_name"] == "a.pdf" and len(h[0]["sha256"]) == 64
    rb, norms = RI.effective(RB, ND, st.current())
    assert norms["engineering"]["students_per_faculty"] == 12 and "R010" not in {r["id"] for r in rb["rules"]}
    s3 = st.stage(plan_of(up2, st.current()), up2.read_bytes())
    assert st.apply(s3, "adm", False)["version"] == 2
    assert st.revert(1, "adm")["version"] == 3 and st.current()["version"] == 3
    assert RI.effective(RB, ND, st.current())[1]["mba"]["min_lease_years"] == 30                  # version 2's change is gone, version 1's is kept
    assert st.revert(0, "adm")["version"] == 4
    rb0, n0 = RI.effective(RB, ND, st.current())
    assert n0 == ND["programme_types"] and rb0["rules"] == RB["rules"]                              # version 0 = the shipped configuration
    assert [x["kind"] for x in st.history()] == ["apply", "apply", "revert", "revert"]


def test_impact_of_a_norm_change_on_stored_cases():
    facts = base_facts()
    old = (RB, ND["programme_types"])
    findings = [f.to_json() for f in R.analyze(facts, RB, "I1")]
    results = {"I1": {"facts": facts, "findings": findings, "app": {"inst_id": "I1"}}, "I2": {"facts": None, "findings": [], "app": {"inst_id": "I2"}}}
    norms2 = copy.deepcopy(ND["programme_types"]); norms2["engineering"]["students_per_faculty"] = 12            # tighter: 40 faculty needed, 32 listed
    imp = PL.rule_change_impact(results, old, (RB, norms2))
    assert imp["cases_checked"] == 1 and imp["rule_changes"] == {"R001": {"pass -> fail": 1}} and imp["score_changes"] == 1
    same = PL.rule_change_impact(results, old, old)
    assert same["rule_changes"] == {} and same["score_changes"] == 0 and same["category_changes"] == []
    loose = copy.deepcopy(ND["programme_types"]); loose["engineering"]["students_per_faculty"] = 30
    assert PL.rule_change_impact(results, old, (RB, loose))["rule_changes"] == {}                                # 16 needed: still passes


def test_cli_template_and_preview(tmp_path, capsys):
    out = tmp_path / "t.pdf"
    assert RI.main(["--template", str(out)]) == 0 and out.exists()
    assert RI.main([str(out)]) == 0
    assert '"changes": []' in capsys.readouterr().out
