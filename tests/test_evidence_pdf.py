import json

import cv2
import numpy as np
import pymupdf
import pytest

from harness import corpus_io as c
from modules import dedup as dd
from modules import ela as E
from modules import evidence_pdf as V
from modules import pdf_forensics as P
from modules import qr_verify as Q
from modules import score as S

CFG = V.load_config()
FORGED = c.first_doc(label="spliced_expiry_forgery")


def findings_for(e):
    f = e["fields"]
    pv = f["valid_to"]["value"]
    fs = P.analyze(e["path"], e["doc_id"], e["doc_type"]) + Q.analyze(e["path"], e["doc_id"], c.issuer_keys(), pv, e["inst_id"])
    return [x.to_json() for x in fs]


def case_for(e, **kw):
    fs = findings_for(e)
    inst = next(i for i in c.institutions() if i["inst_id"] == e["inst_id"])
    return {"case_id": "CASE-1", "generated_at": "2026-10-27T10:00:00Z",
            "institution": {k: inst[k] for k in ("name", "inst_id", "state", "application_date")},
            "score": S.score_findings(fs), "documents": [{"doc_id": e["doc_id"], "doc_type": e["doc_type"], "path": e["path"]}],
            "findings": fs, **kw}


def text_of(pdf):
    with pymupdf.open(pdf) as d:
        return "\n".join(p.get_text() for p in d), d.page_count, sum(len(p.get_images()) for p in d)


def test_report_for_a_forged_certificate(tmp_path):
    case = case_for(FORGED)
    out = tmp_path / "ev.pdf"
    res = V.build_evidence_pdf(case, out)
    txt, pages, imgs = text_of(out)
    assert res["pages"] == pages >= 1 and res["n_findings"] >= 3 and res["n_images"] == imgs >= 2
    flat = " ".join(txt.split())
    for needle in ("covered_text", "qr_date_mismatch", FORGED["fields"]["valid_to"]["hidden_original"],
                   FORGED["fields"]["valid_to"]["value"], "HARD", "SOFT", "Tier 1", "STAFF ONLY", "does not approve or reject",
                   case["institution"]["name"], "Check F-QR", "Check F-HIDDEN", "handbook clause not yet mapped", "sha256"):
        assert needle in flat, needle
    assert flat.index("qr_date_mismatch") < flat.index("covered_text")        # hard before soft


def test_same_case_gives_byte_identical_pdf(tmp_path):
    case = case_for(FORGED)
    a = V.build_evidence_pdf(case, tmp_path / "a.pdf"); b = V.build_evidence_pdf(case, tmp_path / "b.pdf")
    assert a["sha256"] == b["sha256"]


def test_page_crop_has_the_flagged_box_in_red():
    f = next(x for x in findings_for(FORGED) if x["id"] == "covered_text")
    png = V.page_crop_png(FORGED["path"], tuple(f["bbox"]), CFG)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    red = ((img[..., 2] > 200) & (img[..., 1] < 60) & (img[..., 0] < 60)).sum()
    assert img.shape[0] > 40 and red > 100


def test_heatmap_and_duplicate_findings_are_embedded(tmp_path):
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    hm = tmp_path / "heat.png"
    ela = E.analyze(e["path"], e["doc_id"], heatmap_path=hm)[0].to_json()
    ela.update({"id": "ela_suspicious", "kind": "soft", "tier": 4, "p": 0.3, "bbox": [1, 100, 100, 80, 20],
                "message": "Part of the page has a different image-compression history."})
    dup = {"id": "exact_duplicate", "module": "dedup", "kind": "soft", "tier": 3, "p": 0.8, "doc_id": e["doc_id"], "bbox": None,
           "message": "Identical to another submission.", "evidence": {"matches": [{"doc_id": "X-1", "inst_id": "I9", "similarity": 1.0}]}}
    case = {"case_id": "C2", "generated_at": "t", "documents": [{"doc_id": e["doc_id"], "doc_type": "fire_cert", "path": e["path"]}],
            "findings": [ela, dup]}
    res = V.build_evidence_pdf(case, tmp_path / "e.pdf")
    txt, _, imgs = text_of(tmp_path / "e.pdf")
    assert imgs == 2 and res["n_images"] == 2 and "advisory signal" in txt and "X-1" in txt and "Check F-ELA" in txt


def test_rule_findings_cite_the_rulebook(tmp_path):
    f = {"id": "R004", "module": "rules", "kind": "hard", "tier": 2, "p": 0.9, "doc_id": "D1", "bbox": None,
         "message": "Fire certificate expired before the application date.", "evidence": {}}
    case = {"case_id": "C3", "generated_at": "t", "findings": [f],
            "rules": {"R004": {"name": "fire_certificate_valid", "handbook_ref": "Clause 4.2(b)"}}}
    V.build_evidence_pdf(case, tmp_path / "r.pdf")
    flat = " ".join(text_of(tmp_path / "r.pdf")[0].split())
    assert "Rule R004 (fire_certificate_valid): Clause 4.2(b)" in flat


def test_missing_source_and_hostile_text_do_not_break_the_report(tmp_path):
    f = {"id": "covered_text", "module": "pdf_forensics", "kind": "soft", "tier": 3, "p": 0.9, "doc_id": "D1",
         "bbox": [1, 10, 10, 50, 10], "message": "<b>bold</b> & <script>alert(1)</script> [x]",
         "evidence": {"hidden_text": "a" * 5000, "k": None}}
    case = {"case_id": "C4", "generated_at": "t", "documents": [{"doc_id": "D1", "path": str(tmp_path / "nope.pdf")}],
            "findings": [f]}
    V.build_evidence_pdf(case, tmp_path / "m.pdf")
    txt = " ".join(text_of(tmp_path / "m.pdf")[0].split())
    assert "page image not available" in txt and "<script>" in txt and "bold" in txt


def test_no_findings_report(tmp_path):
    res = V.build_evidence_pdf({"case_id": "C5", "generated_at": "t", "findings": [
        {"id": "quality_ok", "module": "quality", "kind": "info", "tier": 4, "p": 0.0, "doc_id": "d", "message": "ok", "bbox": None, "evidence": {}}]},
        tmp_path / "n.pdf")
    assert res["n_findings"] == 0 and "No finding needs attention" in " ".join(text_of(tmp_path / "n.pdf")[0].split())


def test_unknown_finding_id_is_flagged_not_fatal(tmp_path):
    f = {"id": "brand_new_check", "module": "x", "kind": "soft", "tier": 3, "p": 0.4, "doc_id": "D", "bbox": None,
         "message": "m", "evidence": {}}
    V.build_evidence_pdf({"case_id": "C6", "generated_at": "t", "findings": [f]}, tmp_path / "u.pdf")
    assert "No citation registered for 'brand_new_check'" in " ".join(text_of(tmp_path / "u.pdf")[0].split())


def test_cli(tmp_path, capsys):
    cj = tmp_path / "case.json"; cj.write_text(json.dumps(case_for(FORGED)))
    assert V.main([str(cj), str(tmp_path / "o.pdf")]) == 0
    assert json.loads(capsys.readouterr().out)["n_findings"] >= 3 and (tmp_path / "o.pdf").exists()
