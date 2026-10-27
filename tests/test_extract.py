import json
import re

import cv2
import numpy as np
import pymupdf
import pytest

from contracts import ExtractedDoc, Finding
from harness import corpus_io as c
from modules import extract as X

CFG = X.load_config()
TPL = X.load_templates()
SKIP = {"qr", "seal", "signature"}
needs_ocr = pytest.mark.skipif(not X.ocr_available(), reason="Tesseract not installed")


def norm(s): return re.sub(r"[^a-z0-9]", "", str(s).lower())


def truth(e): return {k: v for k, v in e["fields"].items() if k not in SKIP and v.get("value") not in (None, "")}


# ----------------------------------------------------------------------------- digital
@pytest.mark.parametrize("dt", sorted({e["doc_type"] for e in c.iter_docs(clean_only=True) if not c.is_scan(e)}))
def test_digital_documents_are_extracted_exactly(dt):
    docs = [e for e in c.iter_docs(dt, clean_only=True) if not c.is_scan(e)][:12]
    bad = []
    for e in docs:
        doc, info = X.extract(e["path"], e["doc_id"], dt, TPL)
        assert isinstance(doc, ExtractedDoc) and info["source"] == "text_layer"
        for k, v in truth(e).items():
            f = doc.fields.get(k)
            if f is None or norm(f["value"]) != norm(v["value"]) or f["conf"] != 1.0:
                bad.append((e["doc_id"], k, f and f["value"], v["value"]))
        assert not [k for k in doc.fields if k not in truth(e)], (e["doc_id"], "invented fields")
    assert not bad, bad[:5]


def test_field_shape_matches_the_contract():
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    doc, _ = X.extract(e["path"], e["doc_id"], "fire_cert", TPL)
    f = doc.fields["valid_to"]
    assert set(f) >= {"value", "bbox", "page", "conf", "source"} and f["source"] == "text_layer" and len(f["bbox"]) == 4
    assert abs(f["bbox"][0] - e["fields"]["valid_to"]["bbox"][0]) < 1 and abs(f["bbox"][1] - e["fields"]["valid_to"]["bbox"][1]) < 1
    ExtractedDoc.from_json(json.loads(json.dumps(doc.to_json())))


def test_multi_page_table_and_table_rows():
    e = next((e for e in c.iter_docs("faculty_list", clean_only=True) if e["pages"] == 2), None) or c.first_doc("faculty_list", clean_only=True)
    doc, _ = X.extract(e["path"], e["doc_id"], "faculty_list", TPL)
    rows = {int(m.group(1)) for k in doc.fields if (m := re.match(r"faculty\[(\d+)\]\.sl$", k))}
    want = {int(m.group(1)) for k in truth(e) if (m := re.match(r"faculty\[(\d+)\]\.sl$", k))}
    assert rows == want and len(rows) >= 5


def test_optional_field_absent_stays_absent():
    e = next(e for e in c.iter_docs("land_doc", clean_only=True) if not c.is_scan(e) and "lessor" not in e["fields"])
    doc, _ = X.extract(e["path"], e["doc_id"], "land_doc", TPL)
    assert "lessor" not in doc.fields and "lease_from" not in doc.fields and "seller" in doc.fields


@pytest.mark.parametrize("e", [e for lab in ("spliced_expiry_forgery", "incremental_edit_forgery") for e in c.iter_docs(label=lab)],
                         ids=lambda e: e["doc_id"])
def test_covered_text_keeps_the_visible_value_and_lowers_confidence(e):
    doc, _ = X.extract(e["path"], e["doc_id"], e["doc_type"], TPL)
    f, v = doc.fields["valid_to"], e["fields"]["valid_to"]
    assert norm(f["value"]) == norm(v["value"])                              # what a person sees
    assert "covered_text" in f["flags"] and f["conf"] == CFG["conf_conflict"]
    assert norm(v["hidden_original"]) in {norm(a) for a in f["alt_values"]}  # and the original is kept for the reviewer
    assert doc.fields["issued_on"]["conf"] == 1.0                            # other fields are not affected


# ----------------------------------------------------------------------------- OCR
@needs_ocr
def test_ocr_reads_scans_that_pass_the_quality_gate():
    ok = tot = hi_ok = hi_n = 0
    for e in c.iter_docs():
        if not c.is_scan(e) or (e.get("quality") or {}).get("expected_gate") == "fail":
            continue
        doc, info = X.extract(e["path"], e["doc_id"], e["doc_type"], TPL)
        assert info["source"] == "ocr"
        for k, v in truth(e).items():
            f = doc.fields.get(k)
            good = f is not None and norm(f["value"]) == norm(v["value"])
            ok += good; tot += 1
            if f and f["conf"] >= 0.9:
                hi_n += 1; hi_ok += good
    assert tot >= 100 and ok / tot >= 0.9, (ok, tot)
    assert hi_n > 0.5 * tot and hi_ok / hi_n >= 0.97, (hi_ok, hi_n)          # confidence means something


@needs_ocr
def test_ocr_reads_a_bordered_table_on_a_scan():
    e = next(e for e in c.iter_docs() if c.is_scan(e) and e["doc_type"] in ("eoa", "affidavit")
             and (e.get("quality") or {}).get("expected_gate") != "fail")
    doc, _ = X.extract(e["path"], e["doc_id"], e["doc_type"], TPL)
    cells = {k: v for k, v in truth(e).items() if "[" in k}
    right = sum(1 for k, v in cells.items() if k in doc.fields and norm(doc.fields[k]["value"]) == norm(v["value"]))
    assert cells and right / len(cells) >= 0.85


@needs_ocr
def test_degraded_scan_is_sent_to_the_ambiguity_queue():
    e = next(e for e in c.iter_docs(label="scan_degraded") if "blur" in e["quality"]["severity"])
    fs = X.analyze(e["path"], e["doc_id"], e["doc_type"], TPL)
    low = [f for f in fs if f.id == "low_ocr_confidence"]
    assert low and low[0].kind == "soft" and low[0].tier == 4 and low[0].p == CFG["p_low_ocr"] and low[0].bbox
    doc, _ = X.extract(e["path"], e["doc_id"], e["doc_type"], TPL)
    wrong_but_confident = [k for k, v in truth(e).items() if k in doc.fields and doc.fields[k]["conf"] >= 0.9
                           and norm(doc.fields[k]["value"]) != norm(v["value"])]
    assert not wrong_but_confident


def test_ocr_unavailable_is_reported_not_faked(monkeypatch):
    monkeypatch.setattr(X, "_tesseract", lambda cfg: None)
    e = c.first_doc(label="scan_clean")
    fs = X.analyze(e["path"], e["doc_id"], e["doc_type"], TPL)
    assert [f.id for f in fs] == ["ocr_unavailable"] and fs[0].kind == "info"
    assert X.extract(e["path"], e["doc_id"], e["doc_type"], TPL)[0].fields == {}


# ----------------------------------------------------------------------------- units
def test_group_lines_and_junk_filter():
    ws = [{"text": "a", "bbox": (10, 100, 20, 110), "conf": 1, "seq": 0}, {"text": "b", "bbox": (60, 103, 70, 113), "conf": 1, "seq": 1},
          {"text": "c", "bbox": (10, 130, 20, 140), "conf": 1, "seq": 2}]
    lines = X.group_lines(ws, 5.0)
    assert [[w["text"] for w in l] for l in lines] == [["a", "b"], ["c"]]
    assert len(X.group_lines(ws, 1.0)) == 3


def test_digit_confusions_only_inside_numeric_fields():
    assert X._fix_digits("1O5", CFG) == "105" and X._fix_digits("2O/O3/2O25", CFG) == "20/03/2025"
    assert X._fix_digits("Rs. 4,96,O3,427", CFG) == "Rs. 4,96,03,427"
    assert X._fix_digits("SYN-OLD", CFG) == "SYN-OLD"                        # not numeric: untouched
    t = {"numeric": False, "enum": None, "pattern": None}
    assert X._finish("SYN-1O5", t, 0.9, [], CFG)[0] == "SYN-1O5"


def test_enum_repair_and_shape_mismatch():
    t = {"numeric": False, "enum": ["Active", "Lapsed"], "pattern": None}
    assert X._finish("Actlve", t, 1.0, [], CFG) == ("Active", pytest.approx(CFG["conf_enum_repair"]), ["enum_repaired"])
    v, conf, fl = X._finish("Zzzz", t, 1.0, [], CFG)
    assert fl == ["shape_mismatch"] and conf == CFG["conf_shape_mismatch"]
    t2 = {"numeric": True, "enum": None, "pattern": r"^(?:\d{2}/\d{2}/\d{4})$"}
    assert X._finish("21/O2/2002", t2, 0.9, [], CFG)[0] == "21/02/2002"
    assert X._finish("21-02-2002", t2, 0.9, [], CFG)[2] == ["shape_mismatch"]


def test_remove_rules_keeps_text_and_erases_lines():
    g = np.full((400, 600), 255, np.uint8)
    cv2.putText(g, "Hello 123", (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
    cv2.line(g, (0, 120), (599, 120), 0, 2); cv2.line(g, (300, 0), (300, 399), 0, 2)
    out = X.remove_rules(g, 0.06)
    assert out[120, 100] > 200 and out[50, 300] > 200                           # rules gone
    assert (out[170:215, 40:200] < 100).sum() > 100                              # text kept


def test_unknown_type_unreadable_and_cli(tmp_path, capsys):
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    assert [f.id for f in X.analyze(e["path"], "x", "no_such_type", TPL)] == ["extract_no_template"]
    (tmp_path / "bad.pdf").write_bytes(b"nope")
    for p in (tmp_path / "bad.pdf", tmp_path / "missing.pdf"):
        fs = X.analyze(p, "x", "fire_cert", TPL)
        assert [f.id for f in fs] == ["extract_unreadable"] and fs[0].kind == "info"
    assert [f.id for f in X.analyze(e["path"], "x", "fire_cert", TPL)] == ["extract_ok"]
    assert X.main([e["path"], "--doc-type", "fire_cert"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["extracted"]["fields"]["cert_no"]["value"] == e["fields"]["cert_no"]["value"] and out["info"]["source"] == "text_layer"
