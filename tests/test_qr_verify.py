import pymupdf
import pytest
from harness import corpus_io as c
from modules import qr_verify as q

KEYS = c.issuer_keys()
DIGITAL_QR = [e for e in c.iter_docs(clean_only=True) if "qr" in e["fields"] and not c.is_scan(e)]


def run(doc_id, **kw):
    e = c.get_entry(doc_id); f = e["fields"]
    iss = f["qr"]["value"].split("|")[1]
    pv = (f.get("approval_date") if iss == "aicte" else f.get("valid_to"))
    pv = pv["value"] if pv and not c.is_scan(e) else None
    return {x.id: x for x in q.analyze(e["path"], doc_id, KEYS, pv, doc_type=e["doc_type"], **kw)}


def test_valid_digital():
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    assert "qr_valid" in run(e["doc_id"])


@pytest.mark.parametrize("d", [e["doc_id"] for e in c.iter_docs(label="qr_bad_signature")])
def test_bad_signature(d):
    r = run(d); assert r["qr_bad_signature"].kind == "hard" and r["qr_bad_signature"].tier == 1


@pytest.mark.parametrize("d", [e["doc_id"] for lab in ("qr_date_mismatch", "spliced_expiry_forgery",
                                                        "incremental_edit_forgery")
                               for e in c.iter_docs(label=lab) if not c.is_scan(e)])
def test_date_mismatch(d):
    assert "qr_date_mismatch" in run(d)


def test_no_false_positive_on_clean_digital_qr_docs():
    bad = []
    for e in DIGITAL_QR:
        ids = set(run(e["doc_id"]))
        if ids != {"qr_valid"}:
            bad.append((e["doc_id"], ids))
    assert not bad


@pytest.mark.parametrize("e", list(c.iter_docs(label="certificate_reuse")), ids=lambda e: e["doc_id"])
def test_wrong_institution(e):
    # the certificate belongs to another institution; the QR itself is validly signed
    assert "qr_wrong_institution" in run(e["doc_id"], expected_inst_id=e["inst_id"])


def test_scan_decodes():
    e = c.first_doc(label="scan_clean", has_field="qr")
    ids = {x.id for x in q.analyze(e["path"], e["doc_id"], KEYS, doc_type=e["doc_type"])}
    assert "qr_date_unchecked" in ids


def test_absent_and_corrupt(tmp_path):
    e = c.first_doc("society_reg", clean_only=True, digital=True)       # no QR on this type
    assert q.analyze(e["path"], "x", KEYS, expected=True)[0].id == "qr_absent"
    assert q.analyze(e["path"], "x", KEYS, expected=False)[0].kind == "info"
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"not a pdf")
    assert q.analyze(bad, "x", KEYS)[0].kind in ("info", "soft")
    empty = tmp_path / "e.pdf"; empty.write_bytes(b"")
    assert q.analyze(empty, "x", KEYS)[0].kind in ("info", "soft")


def test_config_lists_required_types():
    cfg = q.load_config()
    assert set(cfg["qr_required_doc_types"]) == {"building_plan", "fire_cert", "eoa"}
    assert 0.4 <= cfg["p_qr_missing_on_digital"] <= 0.6


def test_qr_missing_on_digital(tmp_path):
    """Decision 3: a digital PDF of a QR-bearing type with the QR blanked out -> separate soft finding."""
    e = c.first_doc("fire_cert", clean_only=True, digital=True, has_field="qr")
    out = tmp_path / "noqr.pdf"
    with pymupdf.open(e["path"]) as d:
        x, y, w, h = e["fields"]["qr"]["bbox"]
        d[0].draw_rect(pymupdf.Rect(x - 2, y - 2, x + w + 2, y + h + 2), color=None, fill=(1, 1, 1))
        d.save(out)
    fs = q.analyze(out, "x", KEYS, doc_type="fire_cert")
    assert [f.id for f in fs] == ["qr_missing_on_digital"]
    assert fs[0].kind == "soft" and fs[0].tier == 3 and fs[0].p == pytest.approx(0.5, abs=0.1)
    # other doc types keep the old behaviour: qr_absent
    assert q.analyze(out, "x", KEYS, doc_type="society_reg")[0].id == "qr_absent"
    # no doc type given -> old behaviour too (the caller did not say a QR is required)
    assert q.analyze(out, "x", KEYS)[0].id == "qr_absent"
    # config switch: remove fire_cert from the list
    cfg = {**q.load_config(), "qr_required_doc_types": ["eoa"]}
    assert q.analyze(out, "x", KEYS, doc_type="fire_cert", cfg=cfg)[0].id == "qr_absent"


def test_qr_missing_not_raised_for_scan_without_qr(tmp_path):
    """An image-only page (scan) without a decodable QR stays qr_absent, applicant-fixable."""
    import cv2, numpy as np
    img = tmp_path / "s.png"; cv2.imwrite(str(img), np.full((1400, 1000), 255, np.uint8))
    pdf = tmp_path / "s.pdf"
    with pymupdf.open() as d:
        pg = d.new_page(width=595, height=842); pg.insert_image(pg.rect, filename=str(img)); d.save(pdf)
    f = q.analyze(pdf, "x", KEYS, doc_type="fire_cert")[0]
    assert f.id == "qr_absent" and f.kind == "soft"


def test_parse_date():
    from datetime import date
    for s in ("27-Feb-2028", "27 February 2028", "27/02/2028", "2028-02-27"):
        assert q.parse_date(s) == date(2028, 2, 27)
    assert q.parse_date("garbage") is None


def test_cli_extraction_uses_visible_date_on_spliced_docs():
    for lab in ("spliced_expiry_forgery", "incremental_edit_forgery"):
        for e in c.iter_docs(label=lab):
            pv = q.printed_date_from_text(e["path"], "default")
            assert pv == e["fields"]["valid_to"]["value"]          # the forged, visible date
            assert "qr_date_mismatch" in {f.id for f in q.analyze(e["path"], e["doc_id"], KEYS, pv)}
