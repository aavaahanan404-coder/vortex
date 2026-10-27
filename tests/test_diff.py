import json
import re

import cv2
import numpy as np
import pymupdf
import pytest

from harness import corpus_io as c
from modules import diff as D
from modules import pdf_forensics as P

CFG = D.load_config()
INCR = [e for e in c.iter_docs(label="incremental_edit_forgery")]


def make(path, pages):
    """pages: list of callables drawing on a page."""
    doc = pymupdf.open()
    for build in pages:
        build(doc.new_page(width=595, height=842))
    doc.save(str(path)); doc.close()
    return path


def body(date="29-Aug-2025", extra=""):
    def f(pg):
        pg.insert_text((50, 100), "Valid Until", fontname="hebo", fontsize=9)
        pg.insert_text((205, 100), date, fontname="helv", fontsize=9.5)
        pg.insert_text((50, 160), "The premises comply with fire safety provisions " + extra, fontname="helv", fontsize=9)
    return f


def other_page(pg):
    pg.insert_text((50, 300), "Annexure: list of extinguishers installed", fontname="helv", fontsize=10)


def ids(fs): return [f.id for f in fs]


def test_identical_files_need_no_re_audit(tmp_path):
    a = make(tmp_path / "a.pdf", [body(), other_page])
    fs = D.analyze(a, a, "x")
    assert ids(fs) == ["diff_identical"] and fs[0].kind == "info"
    assert fs[0].evidence["unchanged_pages"] == [1, 2]


def test_changed_value_is_a_replace_with_boxes_and_unchanged_page_is_listed(tmp_path):
    a = make(tmp_path / "a.pdf", [body("29-Aug-2025"), other_page])
    b = make(tmp_path / "b.pdf", [body("08-Jan-2027"), other_page])
    f = D.analyze(a, b, "x")[0]
    assert f.id == "diff_summary" and f.kind == "info" and f.tier == 3
    ev = f.evidence
    assert ev["changed_pages"] == [1] and ev["unchanged_pages"] == [2]
    ch = ev["text_changes"]
    assert len(ch) == 1 and ch[0]["op"] == "replace" and ch[0]["old"] == "29-Aug-2025" and ch[0]["new"] == "08-Jan-2027"
    assert 200 < ch[0]["new_bbox"][0] < 210 and ch[0]["new_bbox"][2] > 40
    reg = ev["visual_regions"][0]
    assert reg["page"] == 1 and abs(reg["bbox"][0] - 205) < 12 and abs(reg["bbox"][1] - 90) < 12
    assert f.bbox[0] == 1 and "need no re-audit" in f.message


def test_insert_delete_and_page_count_changes(tmp_path):
    a = make(tmp_path / "a.pdf", [body(extra="alpha beta")])
    b = make(tmp_path / "b.pdf", [body(extra="beta gamma delta"), other_page])
    ev = D.analyze(a, b, "x")[0].evidence
    ops = {(t["op"], t["old"], t["new"]) for t in ev["text_changes"]}
    assert ops == {("delete", "alpha", ""), ("insert", "", "gamma delta")}
    assert ev["pages_added"] == [2] and ev["pages_old"] == 1 and ev["pages_new"] == 2 and ev["compared_pages"] == 1
    back = D.analyze(b, a, "x")[0].evidence
    assert back["pages_removed"] == [2]


def test_watermark_text_is_ignored_in_text_diff(tmp_path):
    def wm(text):
        def f(pg):
            body()(pg)
            pg.insert_text((100, 500), text, fontname="hebo", fontsize=40, fill_opacity=0.18, rotate=0)
        return f
    a = make(tmp_path / "a.pdf", [wm("DRAFT ONE")]); b = make(tmp_path / "b.pdf", [wm("DRAFT TWO")])
    ev = D.analyze(a, b, "x")[0].evidence                       # faint text: no TEXT change, but the pixels differ
    assert ev["n_text_changes"] == 0                       # faint text is not content
    assert ev["n_visual_regions"] >= 1                     # ... but the pixels did change, so the page is not "unchanged"
    assert ev["changed_pages"] == [1]


@pytest.mark.parametrize("e", INCR, ids=lambda e: e["doc_id"])
def test_real_incremental_forgery_revision1_vs_final(e, tmp_path):
    """Corpus pair: the original (revision 1) against the forged final file."""
    data = open(e["path"], "rb").read()
    first = tmp_path / "rev1.pdf"; first.write_bytes(data[:P.revision_ends(data)[0]])
    f = D.analyze(first, e["path"], e["doc_id"])[0]
    v = e["fields"]["valid_to"]
    assert f.id == "diff_summary" and f.evidence["changed_pages"] == [1]
    assert any(t["op"] == "insert" and t["new"] == v["value"] for t in f.evidence["text_changes"])
    reg = f.evidence["visual_regions"][0]["bbox"]
    assert abs(reg[0] - v["bbox"][0]) < 15 and abs(reg[1] - v["bbox"][1]) < 15           # points at the date field


def test_scan_pairs_use_the_visual_diff(tmp_path):
    e = c.first_doc(label="scan_clean")
    with pymupdf.open(e["path"]) as d:
        pm = d[0].get_pixmap(dpi=100, colorspace=pymupdf.csGRAY, alpha=False)
    a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width).copy()
    b = a.copy(); b[300:340, 200:330] = 255
    pa, pb = tmp_path / "a.png", tmp_path / "b.png"; cv2.imwrite(str(pa), a); cv2.imwrite(str(pb), b)
    ev = D.analyze(pa, pb, "x")[0].evidence
    assert ev["n_text_changes"] == 0 and ev["n_visual_regions"] >= 1 and ev["changed_pages"] == [1]
    assert D.analyze(pa, pa, "x")[0].id == "diff_identical"


def test_overlay_colours(tmp_path):
    a = make(tmp_path / "a.pdf", [body("29-Aug-2025")]); b = make(tmp_path / "b.pdf", [body("08-Jan-2027")])
    out = D.render_overlay(a, b, 1, tmp_path / "o.png")
    img = cv2.imread(str(out))
    red = ((img[..., 2] > 180) & (img[..., 1] < 60)).sum(); green = ((img[..., 1] > 140) & (img[..., 2] < 60)).sum()
    assert red > 20 and green > 20 and img.shape[2] == 3


def test_corpus_clean_document_against_itself_and_a_different_one():
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    assert ids(D.analyze(e["path"], e["path"], e["doc_id"])) == ["diff_identical"]
    other = next(x for x in c.iter_docs(clean_only=True) if x["doc_type"] == "fire_cert" and not c.is_scan(x)
                 and x["doc_id"] != e["doc_id"])
    f = D.analyze(e["path"], other["path"], "x")[0]
    assert f.id == "diff_summary" and f.evidence["n_text_changes"] > 0


def test_bad_inputs(tmp_path):
    good = make(tmp_path / "g.pdf", [body()])
    (tmp_path / "bad.pdf").write_bytes(b"nope"); (tmp_path / "e.pdf").write_bytes(b"")
    for p in (tmp_path / "bad.pdf", tmp_path / "e.pdf", tmp_path / "missing.pdf"):
        for pair in ((p, good), (good, p)):
            fs = D.analyze(*pair, "x")
            assert ids(fs) == ["diff_unreadable"] and fs[0].kind == "info"


def test_cli(tmp_path, capsys):
    a = make(tmp_path / "a.pdf", [body("29-Aug-2025")]); b = make(tmp_path / "b.pdf", [body("08-Jan-2027")])
    assert D.main([str(a), str(b), "--overlay-dir", str(tmp_path / "ov")]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "diff_summary" and (tmp_path / "ov" / "page1_diff.png").exists()
