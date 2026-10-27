import json

import pytest

from contracts import Finding
from harness import corpus_io as c
from modules import adapters as A
from modules import extract as X
from modules import score as S

CFG = A.load_config()
APP = "2026-02-02"
needs_ocr = pytest.mark.skipif(not X.ocr_available(), reason="Tesseract not installed")


class Fake:
    """Registry stub: table -> rows keyed by the lookup key; optional scripted failures."""

    def __init__(self, data=None, script=None):
        self.data, self.script, self.calls = data or {}, script or {}, []

    def lookup(self, source, key, inst_id=None):
        self.calls.append((source, key, inst_id))
        ev = self.script.get((source, key))
        if ev:
            e = ev.pop(0) if isinstance(ev, list) else ev
            if isinstance(e, Exception):
                raise e
        return [dict(r) for r in self.data.get((source, key), [])]


def fire_row(**kw):
    return {"cert_no": "C1", "inst_id": "I1", "valid_to": "2027-02-27", **kw}


def run(doc_type, fields, src, inst="I1", app=APP, sleeps=None):
    return A.verify_document(doc_type, fields, app, inst, src, CFG, sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


# ----------------------------------------------------------------------------- retry / backoff
def test_exponential_backoff_and_attempt_counts():
    src = Fake({("fire", "C1"): [fire_row()]}, {("fire", "C1"): [A.SourceUnavailable("503"), A.SourceUnavailable("503")]})
    sl = []
    r = run("fire_cert", {"cert_no": "C1", "valid_to": "27-Feb-2027"}, src, sleeps=sl)
    assert r.status == "verified" and r.attempts == 3 and sl == [0.5, 1.0] and r.waited_s == 1.5


def test_all_attempts_fail_is_source_unavailable_and_delays_are_capped():
    src = Fake({}, {("fire", "C1"): A.SourceUnavailable("503")})
    sl = []
    r = run("fire_cert", {"cert_no": "C1"}, src, sleeps=sl)
    assert r.status == "source_unavailable" and sl == [0.5, 1.0, 2.0] and len(src.calls) == CFG["retry"]["max_attempts"]
    capped = A.call_with_retry  # cap check on a long schedule
    sl2 = []
    with pytest.raises(A.SourceUnavailable):
        capped(Fake({}, {("x", "k"): A.SourceUnavailable("503")}), "x", "k", {"max_attempts": 8, "base_delay_s": 1, "factor": 4, "max_delay_s": 10}, sl2.append)
    assert sl2 == [1, 4, 10, 10, 10, 10, 10]


def test_pending_is_not_retried_and_ambiguity_goes_to_a_person():
    src = Fake({}, {("building", "B1"): A.SourcePending("wait")})
    sl = []
    r = run("building_plan", {"cert_no": "B1"}, src, sleeps=sl)
    assert r.status == "verification_pending" and sl == [] and len(src.calls) == 1 and r.retry_after_s == CFG["pending_retry_after_s"]
    two = {("society", "R1"): [{"name": "X", "status": "active", "society_id": "1"}, {"name": "X", "status": "active", "society_id": "2"}]}
    r = run("society_reg", {"reg_no": "R1", "society_name": "X"}, Fake(two))
    assert r.status == "manual_review_required" and "2 society records" in r.reason


# ----------------------------------------------------------------------------- per document type
@pytest.mark.parametrize("fields,row,expect", [
    ({"cert_no": "C1", "valid_to": "27-Feb-2027"}, fire_row(), ("verified", "")),
    ({"cert_no": "C1", "valid_to": "27-Feb-2027"}, fire_row(inst_id="I2"), ("mismatch_detected", "certificate issued to another institution")),
    ({"cert_no": "C1", "valid_to": "08-Jan-2028"}, fire_row(), ("mismatch_detected", "validity differs from registry")),
    ({"cert_no": "C1", "valid_to": "25-Jan-2025"}, fire_row(valid_to="2025-01-25"), ("expired", "valid_to before application date")),
    # precedence: a forged later date over an expired original is a mismatch, not "expired"
    ({"cert_no": "C1", "valid_to": "08-Jan-2028"}, fire_row(valid_to="2025-08-29"), ("mismatch_detected", "validity differs from registry")),
    ({"cert_no": "C1", "valid_to": "2 February 2026"}, fire_row(valid_to="2026-02-02"), ("verified", "")),     # valid on the day itself
])
def test_fire_certificate_outcomes(fields, row, expect):
    r = run("fire_cert", fields, Fake({("fire", "C1"): [row]}))
    assert (r.status, r.reason) == expect


def test_certificate_not_in_registry_and_unreadable_identifier():
    assert run("fire_cert", {"cert_no": "C9"}, Fake()).reason == "certificate not in registry"
    r = run("fire_cert", {"valid_to": "x"}, Fake())
    assert r.status == "manual_review_required" and "cert_no" in r.reason


def test_ocr_read_identifier_that_is_not_found_is_not_an_accusation():
    f = {"cert_no": {"value": "SYN-FSC/RJ/2024/4B8797", "conf": 0.8, "source": "ocr"}}
    r = run("fire_cert", f, Fake())
    assert r.status == "manual_review_required" and "OCR" in r.reason
    f2 = {"cert_no": {"value": "SYN-FSC/RJ/2024/488797", "conf": 1.0, "source": "text_layer"}}
    assert run("fire_cert", f2, Fake()).status == "mismatch_detected"             # an exact text layer is trusted


def test_land_checks():
    row = {"holder_inst_id": "I1", "area_acres": 14.3}
    ok = run("land_doc", {"survey_no": "S", "area_acres": "14.30 Acres"}, Fake({("land", "S"): [row]}))
    assert ok.status == "verified"
    assert run("land_doc", {"survey_no": "S", "area_acres": "14.40 Acres"}, Fake({("land", "S"): [row]})).reason == "area differs"
    assert run("land_doc", {"survey_no": "S", "area_acres": "14.30"}, Fake({("land", "S"): [{**row, "holder_inst_id": "I2"}]})).reason == "parcel held by another institution"


def test_financial_checks_amounts_with_indian_grouping():
    class Src(Fake):
        class con:
            @staticmethod
            def execute(q, a):
                return [{"total_income": 49603427, "total_expenditure": 40316196}]
    f = {"firm_no": "F1", "fin.income.a": "Rs. 4,96,03,427", "fin.expenditure.a": "Rs. 4,03,16,196"}
    src = Src({("ca", "F1"): [{"status": "active"}]})
    assert run("financial", f, src).status == "verified"
    assert run("financial", {**f, "fin.income.a": "Rs. 4,96,03,428"}, src).reason.startswith("income or expenditure differs")
    assert A._money("Rs. 4,96,03,427") == 49603427 and A._money("") is None


def test_faculty_registry_check_counts_people_employed_elsewhere():
    emp = {("faculty", "F1"): [{"inst_id": "I1", "to_date": None}], ("faculty", "F2"): [{"inst_id": "I2", "to_date": None}],
           ("faculty", "F3"): [{"inst_id": "I1", "to_date": "2020-01-01"}, {"inst_id": "I9", "to_date": None}]}
    f = {f"faculty[{i}].faculty_id": v for i, v in enumerate(["F1", "F2", "F3"])}
    r = run("faculty_list", f, Fake(emp))
    assert r.status == "mismatch_detected" and r.reason == "2 faculty not employed here per registry"
    assert r.checks["not_employed_here"] == ["F2", "F3"] and r.record["employment"]["F3"] == "I9"


def test_documents_without_an_issuer_and_adapter_crashes():
    for dt in ("affidavit", "infra_statement"):
        r = run(dt, {}, Fake())
        assert r.status == "not_applicable" and "Tier 3" in r.reason
    class Boom(Fake):
        def lookup(self, *a, **k):
            raise RuntimeError("boom")
    r = run("fire_cert", {"cert_no": "C1"}, Boom())
    assert r.status == "manual_review_required" and "RuntimeError" in r.reason


def test_every_result_is_one_of_the_six_states():
    assert set(A.STATES) == {"verified", "mismatch_detected", "expired", "verification_pending", "source_unavailable", "manual_review_required"}


# ----------------------------------------------------------------------------- mock authority and findings
@pytest.fixture
def world():
    s = A.WorldSource(c.corpus_root() / "world" / "world.sqlite")
    yield s
    s.close()


def test_scenarios_behave_and_are_scoped_to_their_institution(world):
    flags = {(k[0], k[1]): v for k, v in world.flags.items()}
    by = {v[0]: (k, v[1]) for k, v in flags.items()}
    if "always_503" in by:
        (src, key), inst = by["always_503"]
        with pytest.raises(A.SourceUnavailable):
            world.lookup(src, key, inst)
        assert world.lookup(src, key, "SYN-I9999") is not None            # other institutions are unaffected
    if "flaky_then_ok" in by:
        (src, key), inst = by["flaky_then_ok"]
        for _ in range(CFG["mock"]["flaky_failures"]):
            with pytest.raises(A.SourceUnavailable):
                world.lookup(src, key, inst)
        assert world.lookup(src, key, inst)
    if "pending_then_ok" in by:
        (src, key), inst = by["pending_then_ok"]
        with pytest.raises(A.SourcePending):
            world.lookup(src, key, inst)
        assert world.lookup(src, key, inst)
    if "ambiguous_match" in by:
        (src, key), inst = by["ambiguous_match"]
        assert len(world.lookup(src, key, inst)) == 2 and len(world.lookup(src, key, "SYN-I9999")) == 1


def test_world_database_is_read_only(world):
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        world.con.execute("delete from fire_certs")


def test_findings_and_their_routing():
    def an(status_fields, src, dt="fire_cert"):
        return A.analyze(dt, status_fields, APP, "I1", src, "d1", CFG, sleep=lambda s: None)
    mm = an({"cert_no": "C1", "valid_to": "01-Jan-2030"}, Fake({("fire", "C1"): [fire_row()]}))[0]
    assert (mm.id, mm.kind, mm.tier) == ("registry_mismatch", "hard", 2) and mm.evidence["reason"] == "validity differs from registry"
    ex = an({"cert_no": "C1", "valid_to": "25-Jan-2025"}, Fake({("fire", "C1"): [fire_row(valid_to="2025-01-25")]}))[0]
    assert (ex.id, ex.kind) == ("registry_expired", "soft")
    un = an({"cert_no": "C1"}, Fake({}, {("fire", "C1"): A.SourceUnavailable("x")}))[0]
    assert (un.id, un.kind, un.tier) == ("source_unavailable", "soft", 2)
    assert S.score_findings([un])["category"] == "needs_human"                       # routed to a person, not auto-cleared
    ok = an({"cert_no": "C1", "valid_to": "27-Feb-2027"}, Fake({("fire", "C1"): [fire_row()]}))[0]
    assert ok.id == "registry_verified" and ok.kind == "info"
    Finding.from_json(json.loads(json.dumps(mm.to_json())))


# ----------------------------------------------------------------------------- corpus
def test_every_digital_document_gets_the_expected_status():
    src = A.WorldSource(c.corpus_root() / "world" / "world.sqlite")
    inst = {i["inst_id"]: i for i in c.institutions()}
    bad, n = [], 0
    try:
        for e in c.iter_docs():
            if c.is_scan(e):
                continue
            f = X.extract(e["path"], e["doc_id"], e["doc_type"])[0].fields
            r = A.verify_document(e["doc_type"], f, inst[e["inst_id"]]["application_date"], e["inst_id"], src, CFG, sleep=lambda s: None)
            n += 1
            exp = dict(e["expected_adapter"])
            # corpus quirk: a scenario flag (authority down) can sit on a certificate that the manifest also labels as a reuse;
            # the outage wins, because the authority cannot be asked at all
            spec = CFG["sources"].get(e["doc_type"]) or {}
            flag = src.flags.get((spec.get("source"), str((f.get(spec.get("key")) or {}).get("value"))))
            if flag and flag[0] == "always_503" and flag[1] == e["inst_id"]:
                exp = {"status": "source_unavailable", "reason": ""}
            if r.status != exp["status"] or (exp.get("reason") and not exp["reason"].startswith("scenario_flag") and exp["status"] not in ("verified", "not_applicable")
                                              and r.reason.lower() != exp["reason"].lower()):
                bad.append((e["doc_id"], exp["status"], r.status, r.reason))
    finally:
        src.close()
    assert n > 800 and not bad, bad[:5]


@needs_ocr
def test_scans_never_produce_an_accusation_from_a_misread():
    src = A.WorldSource(c.corpus_root() / "world" / "world.sqlite")
    inst = {i["inst_id"]: i for i in c.institutions()}
    wrong = []
    try:
        for e in c.iter_docs():
            if not c.is_scan(e):
                continue
            f = X.extract(e["path"], e["doc_id"], e["doc_type"])[0].fields
            r = A.verify_document(e["doc_type"], f, inst[e["inst_id"]]["application_date"], e["inst_id"], src, CFG, sleep=lambda s: None)
            if e["expected_adapter"]["status"] in ("verified", "not_applicable") and r.status in ("mismatch_detected", "expired"):
                wrong.append((e["doc_id"], r.status, r.reason))
    finally:
        src.close()
    assert not wrong, wrong


def test_cli(tmp_path, capsys):
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    f = X.extract(e["path"], e["doc_id"], "fire_cert")[0].fields
    p = tmp_path / "f.json"; p.write_text(json.dumps(f))
    app = next(i for i in c.institutions() if i["inst_id"] == e["inst_id"])["application_date"]
    assert A.main([str(p), "--doc-type", "fire_cert", "--inst", e["inst_id"], "--date", app, "--world", str(c.corpus_root() / "world" / "world.sqlite")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] in A.STATES
