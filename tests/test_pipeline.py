import json
import re

import cv2
import pymupdf
import pytest

from harness import corpus_io as c
from harness.make_photo import photograph
from modules import adapters as A
from modules import extract as X
from modules import perspective as P
from modules import pipeline as PL
from modules import review as R

FORBIDDEN = ("score", "category", "high_anomaly", "needs_human", "covered_text", "alt_values", "registry_mismatch", "exact_duplicate",
             "qr_date_mismatch", "qr_bad_signature", "font_anomaly", "editor_metadata", "R016", "R013", "ela_suspicious", "visual_near_duplicate")
needs_ocr = pytest.mark.skipif(not X.ocr_available(), reason="Tesseract not installed")


@pytest.fixture(scope="module")
def world():
    s = A.WorldSource(c.corpus_root() / "world" / "world.sqlite")
    yield s, c.issuer_keys()
    s.close()


def institution(pred, all_digital=True):
    by = {}
    for e in c.iter_docs():
        by.setdefault(e["inst_id"], []).append(e)
    for i in c.institutions():
        docs = by[i["inst_id"]]
        if (not all_digital or all(not c.is_scan(e) for e in docs)) and pred(i):
            app = {"inst_id": i["inst_id"], "name": i["name"], "application_date": i["application_date"],
                   "application_type": i["application_type"], "programme_type": i["type"]}
            return i, app, [{"doc_id": e["doc_id"], "doc_type": e["doc_type"], "path": e["path"]} for e in docs]
    pytest.skip("no such institution in this corpus")


def pipe(world, tmp_path, **kw):
    s, k = world
    return PL.Pipeline(s, k, work_dir=tmp_path, **kw)


def ids(res): return {f["id"] for f in res["findings"] if f["kind"] != "info"}


# ----------------------------------------------------------------------------- intake
def test_intake_decides_by_content_not_by_extension(tmp_path):
    cfg = PL.load_config()
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    (tmp_path / "lies.jpg").write_bytes(open(e["path"], "rb").read())                    # a PDF named .jpg
    assert PL.intake(tmp_path / "lies.jpg", cfg)[0] == "pdf"
    import numpy as np
    cv2.imwrite(str(tmp_path / "p.png"), np.full((50, 50, 3), 255, np.uint8))
    (tmp_path / "p.bin").write_bytes((tmp_path / "p.png").read_bytes())                  # a PNG with a meaningless extension
    assert PL.intake(tmp_path / "p.bin", cfg)[0] == "image"
    for name, data in (("txt.pdf", b"just text"), ("empty.pdf", b""), ("bad.png", b"\x89PNG garbage"), ("exe.pdf", b"MZ\x90\x00")):
        (tmp_path / name).write_bytes(data)
        kind, fs = PL.intake(tmp_path / name, cfg)
        assert kind is None and fs[0].id == "file_unreadable" and fs[0].kind == "soft"
    assert PL.intake(tmp_path / "missing.pdf", cfg)[1][0].id == "file_unreadable"
    big = {**cfg, "max_file_mb": 0}
    assert PL.intake(e["path"], big)[1][0].id == "file_too_large"


# ----------------------------------------------------------------------------- modes and views
def test_clean_application_is_auto_clear_with_an_empty_client_view(world, tmp_path):
    i, app, ups = institution(lambda i: not i["mutations"] and not i["documents_missing"] and not i.get("scenario_flag"))
    res = pipe(world, tmp_path).process_application(app, ups, now="2026-02-01")
    assert res["mode"] == "full" and res["score"]["category"] == "auto_clear_candidate" and ids(res) == set() and res["review"]["double_review"] is False
    cv = PL.client_view(res)
    assert cv["status"] == "received" and cv["application_issues"] == [] and {d["doc_type"] for d in cv["documents"]} == {u["doc_type"] for u in ups}
    assert all(d["status"] == "ok" and d["prefill"] for d in cv["documents"])


def test_forgery_is_escalated_and_the_client_view_reveals_nothing(world, tmp_path):
    i, app, ups = institution(lambda i: "spliced_expiry_forgery" in i["mutations"] and not i["documents_missing"])
    res = pipe(world, tmp_path).process_application(app, ups)
    assert res["score"]["category"] == "high_anomaly" and res["score"]["score"] > 0.9 and res["review"]["double_review"]
    assert {"covered_text", "qr_date_mismatch", "R016", "registry_mismatch", "editor_metadata", "font_anomaly"} <= ids(res)
    cv = json.dumps(PL.client_view(res))
    assert not [w for w in FORBIDDEN if w in cv]
    fire = next(d for d in res["documents"] if d["doc_type"] == "fire_cert")
    assert fire["fields"]["valid_to"]["flags"] == ["covered_text"] and fire["fields"]["valid_to"]["alt_values"]       # staff keep the evidence ...
    pre = next(d for d in PL.client_view(res)["documents"] if d["doc_type"] == "fire_cert")["prefill"]["valid_to"]
    assert pre == {"value": fire["fields"]["valid_to"]["value"], "confident": True}                                    # ... the applicant sees no hint of it


def test_dry_run_runs_no_forensic_detector_and_touches_no_shared_state(world, tmp_path):
    i, app, ups = institution(lambda i: "spliced_expiry_forgery" in i["mutations"] and not i["documents_missing"])
    pl = pipe(world, tmp_path)
    res = pl.process_application(app, ups, dry_run=True)
    assert res["mode"] == "dry_run" and len(pl.index.docs) == 0                                                     # dedup index untouched
    got = ids(res)
    assert not got & {"covered_text", "qr_date_mismatch", "registry_mismatch", "editor_metadata", "font_anomaly", "exact_duplicate", "R016", "R013"}
    assert all(d["adapter"] is None for d in res["documents"])                                                      # no registry call
    cv = PL.client_view(res)
    assert cv["mode"] == "dry_run" and cv["status"] == "ready_to_submit"                                            # a forgery is not the applicant's business here


def test_dry_run_tells_the_applicant_about_a_missing_document_and_an_expired_certificate(world, tmp_path):
    i, app, ups = institution(lambda i: i["documents_missing"], all_digital=False)
    cv = PL.client_view(pipe(world, tmp_path).process_application(app, ups, dry_run=True))
    assert cv["status"] == "action_needed" and [x["code"] for x in cv["application_issues"]] == ["R010"]
    assert "mandatory document" in cv["application_issues"][0]["message"]
    j, app2, ups2 = institution(lambda i: "expired_fire_cert" in i["mutations"] and not i["documents_missing"])
    cv2 = PL.client_view(pipe(world, tmp_path).process_application(app2, ups2, dry_run=True))
    assert "R004" in [x["code"] for x in cv2["application_issues"]] and "current certificate" in json.dumps(cv2)


def test_registry_outage_is_a_person_s_job_not_a_failure(world, tmp_path):
    i, app, ups = institution(lambda i: i.get("scenario_flag") and i["scenario_flag"][2] == "always_503", all_digital=False)
    res = pipe(world, tmp_path).process_application(app, ups)
    assert {"source_unavailable", "rule_not_evaluable"} <= ids(res) and res["score"]["category"] in ("needs_human", "high_anomaly")
    assert not [f for f in res["findings"] if f["id"] == "step_failed"]
    assert not [w for w in FORBIDDEN if w in json.dumps(PL.client_view(res))]


def test_duplicate_is_flagged_when_it_arrives(world, tmp_path):
    e = next(e for e in c.iter_docs(label="exact_duplicate"))
    donor = c.get_entry(e["derived_from"]["doc_id"])
    mk = lambda x: ({"inst_id": x["inst_id"], "application_date": "2026-02-01", "application_type": "existing", "programme_type": "engineering"},
                    [{"doc_id": x["doc_id"], "doc_type": x["doc_type"], "path": x["path"]}])
    pl = pipe(world, tmp_path)
    a1, u1 = mk(donor); a2, u2 = mk(e)
    first = pl.process_application(a1, u1)
    second = pl.process_application(a2, u2)
    assert "exact_duplicate" not in ids(first) and "exact_duplicate" in ids(second)
    hit = next(f for f in second["findings"] if f["id"] == "exact_duplicate")
    assert donor["doc_id"] in json.dumps(hit["evidence"])


def test_a_failing_step_does_not_stop_the_pipeline(world, tmp_path, monkeypatch):
    i, app, ups = institution(lambda i: not i["mutations"] and not i["documents_missing"] and not i.get("scenario_flag"))
    monkeypatch.setattr(PL.pdf_forensics, "analyze", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res = pipe(world, tmp_path).process_application(app, ups)
    failed = [f for f in res["findings"] if f["id"] == "step_failed"]
    assert failed and failed[0]["evidence"]["step"] == "pdf_forensics" and "boom" in failed[0]["evidence"]["error"] and failed[0]["kind"] == "info"
    assert res["score"]["category"] == "auto_clear_candidate" and res["rules"]                                      # the rest still ran


def test_unreadable_upload_is_reported_to_the_applicant(world, tmp_path):
    i, app, ups = institution(lambda i: not i["mutations"] and not i["documents_missing"])
    bad = tmp_path / "garbage.pdf"; bad.write_bytes(b"not a pdf at all")
    ups = [dict(u, path=str(bad)) if u["doc_type"] == "fire_cert" else u for u in ups]
    cv = PL.client_view(pipe(world, tmp_path).process_application(app, ups, dry_run=True))
    fire = next(d for d in cv["documents"] if d["doc_type"] == "fire_cert")
    assert fire["status"] == "needs_attention" and fire["issues"][0]["code"] == "file_unreadable" and cv["status"] == "action_needed"


@needs_ocr
def test_wrong_document_type_is_proposed(world, tmp_path):
    i, app, ups = institution(lambda i: not i["mutations"] and not i["documents_missing"])
    fire, land = (next(u for u in ups if u["doc_type"] == t) for t in ("fire_cert", "land_doc"))
    swapped = [dict(u, path=land["path"]) if u["doc_type"] == "fire_cert" else u for u in ups]                      # the deed uploaded as the fire certificate
    res = pipe(world, tmp_path).process_application(app, swapped, dry_run=True)
    w = [f for f in res["findings"] if f["id"] == "wrong_document_type"]
    assert w and w[0]["evidence"]["declared"] == "fire_cert" and w[0]["evidence"]["detected"] == "land_doc"
    cv = PL.client_view(res)
    assert "land deed" not in json.dumps(cv) and "looks like a land doc" in json.dumps(cv)


@needs_ocr
def test_photo_of_a_page_is_straightened_before_reading(world, tmp_path):
    i, app, ups = institution(lambda i: not i["mutations"], all_digital=False)
    e = next(e for e in c.iter_docs(clean_only=True) if c.is_scan(e) and e["doc_type"] in ("fire_cert", "building_plan"))
    photo, _ = photograph(P.load_image(e["path"]), seed=2)
    p = tmp_path / "photo.jpg"; cv2.imwrite(str(p), photo)
    pl = pipe(world, tmp_path)
    d = pl.process_document({"inst_id": e["inst_id"], "application_date": "2026-02-01"}, {"doc_id": "d1", "doc_type": e["doc_type"], "path": str(p)}, dry_run=True)
    assert d["kind"] == "image" and any(f.id == "perspective_corrected" for f in d["findings"])
    ok = sum(1 for k, v in e["fields"].items() if k in d["fields"] and re.sub(r"[^a-z0-9]", "", d["fields"][k]["value"].lower()) == re.sub(r"[^a-z0-9]", "", str(v["value"]).lower()))
    assert ok >= 5


def test_audit_and_evidence_pdf_and_cli(world, tmp_path, capsys):
    import sqlite3
    from modules.audit import AuditLog
    audit = AuditLog(sqlite3.connect(":memory:"))
    i, app, ups = institution(lambda i: "incremental_edit_forgery" in i["mutations"] and not i["documents_missing"])
    pl = pipe(world, tmp_path, audit=audit)
    res = pl.process_application(app, ups, now="2026-02-01")
    pl.process_application(app, ups, dry_run=True)
    n = audit.conn.execute("select count(*) from audit_log").fetchone()[0]
    assert n == 1 and audit.verify_chain() == (True, None)                                                          # the dry run wrote nothing
    out = tmp_path / "ev.pdf"
    r = pl.build_evidence(res, out)
    txt = "\n".join(p.get_text() for p in pymupdf.open(str(out)))
    assert r["n_findings"] >= 4 and "incremental_update" in txt and "covered_text" in txt
    spec = tmp_path / "app.json"; spec.write_text(json.dumps({"app": app, "uploads": ups}))
    assert PL.main([str(spec), "--world", str(c.corpus_root() / "world" / "world.sqlite"), "--view", "client", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry_run"
