import json
import pymupdf
import pytest
from pypdf import PdfWriter

from contracts import Finding
from harness import corpus_io as c
from modules import pdf_forensics as P

CFG = P.load_config()
FORGERIES = [e for lab in ("spliced_expiry_forgery", "incremental_edit_forgery")
             for e in c.iter_docs(label=lab)]
EDITORS = list(c.iter_docs(label="editor_metadata"))


def ids(fs): return {f.id for f in fs}


def iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx)); iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    i = ix * iy
    return i / (aw * ah + bw * bh - i)


def make_pdf(path, build, meta=None):
    doc = pymupdf.open()
    build(doc.new_page(width=595, height=842))
    if meta:
        doc.set_metadata(meta)
    doc.save(str(path)); doc.close()
    return path


def label_value(pg):
    pg.insert_text((50, 100), "Valid Until", fontname="hebo", fontsize=9)
    pg.insert_text((205, 100), "29-Aug-2025", fontname="helv", fontsize=9.5)


# ----------------------------------------------------------------------------- corpus
@pytest.mark.parametrize("e", FORGERIES, ids=lambda e: e["doc_id"])
def test_forgeries_report_hidden_and_visible_date(e):
    fs = P.analyze(e["path"], e["doc_id"], e["doc_type"])
    cov = [f for f in fs if f.id == "covered_text"]
    assert len(cov) == 1 and cov[0].kind == "soft" and cov[0].tier == 3 and cov[0].p >= 0.85
    v = e["fields"]["valid_to"]
    assert cov[0].evidence["hidden_text"] == v["hidden_original"]
    assert cov[0].evidence["visible_text"] == v["value"]
    assert iou(cov[0].bbox[1:], v["bbox"]) >= 0.5          # the bbox points at the date field
    assert "font_anomaly" in ids(fs)                          # Courier-Bold is not a fire_cert font
    if v.get("via_incremental_update"):
        inc = [f for f in fs if f.id == "incremental_update"][0]
        assert inc.evidence["n_revisions"] == 2 and v["value"] in inc.evidence["added_text"]
        assert iou(inc.bbox[1:], v["bbox"]) >= 0.4
    else:
        assert "incremental_update" not in ids(fs)


@pytest.mark.parametrize("e", EDITORS, ids=lambda e: e["doc_id"])
def test_editor_metadata_documents(e):
    fs = [f for f in P.analyze(e["path"], e["doc_id"], e["doc_type"]) if f.id == "editor_metadata"]
    assert len(fs) == 1 and fs[0].evidence["tool_match"] and fs[0].evidence["gap_days"] > 7


def test_clean_documents_raise_nothing():
    """Every clean document in the corpus: zero soft/hard findings (digital and scan)."""
    bad = []
    n = 0
    for e in c.iter_docs(clean_only=True):
        n += 1
        act = [f.id for f in P.analyze(e["path"], e["doc_id"], e["doc_type"]) if f.kind != "info"]
        if act:
            bad.append((e["doc_id"], act))
    assert n > 100 and not bad


def test_revisions_are_single_on_clean_documents():
    for e in list(c.iter_docs(clean_only=True))[:100]:
        with open(e["path"], "rb") as fh:
            assert len(P.revision_ends(fh.read())) == 1


def test_font_allowlist_built_from_train_only():
    from harness import build_font_allowlist as B
    with pytest.raises(SystemExit):
        B.main(["--corpus", "corpus_test_v2", "--out", "unused.yaml"])
    assert "Courier-Bold" not in {f for v in CFG["fonts_by_doc_type"].values() for f in v}


# ----------------------------------------------------------------------------- covered text
def test_covered_text_synthetic(tmp_path):
    def build(pg):
        label_value(pg)
        pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(1, 1, 1))
        pg.insert_text((205, 100), "08-Jan-2027", fontname="cobo", fontsize=9.5)
    fs = P.analyze(make_pdf(tmp_path / "f.pdf", build), "x")
    cov = [f for f in fs if f.id == "covered_text"][0]
    assert cov.evidence["hidden_text"] == "29-Aug-2025" and cov.evidence["visible_text"] == "08-Jan-2027"
    assert cov.bbox[0] == 1 and 200 < cov.bbox[1] < 210
    regions = P.covered_text_regions(tmp_path / "f.pdf")           # the helper for a later `extract`
    assert regions[0]["hidden_text"] == "29-Aug-2025" and regions[0]["visible_text"] == "08-Jan-2027"
    assert regions[0]["visible_font"].startswith("Courier")


def test_erasure_without_replacement_is_softer(tmp_path):
    def build(pg):
        label_value(pg)
        pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(1, 1, 1))
    f = [f for f in P.analyze(make_pdf(tmp_path / "e.pdf", build), "x") if f.id == "covered_text"][0]
    assert f.evidence["visible_text"] == "" and f.p == CFG["p_covered_text_no_replacement"] < CFG["p_covered_text"]


@pytest.mark.parametrize("case", ["box_before_text", "box_elsewhere", "page_background_after", "identical_redraw",
                                  "faint_watermark_under_box", "off_white_box", "white_stroke_only"])
def test_legitimate_white_shapes_stay_silent(tmp_path, case):
    """Things clean producers do: white boxes/backgrounds that do not hide text that was drawn earlier."""
    def build(pg):
        if case == "box_before_text":
            pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(1, 1, 1)); label_value(pg)
        elif case == "box_elsewhere":
            label_value(pg); pg.draw_rect(pymupdf.Rect(300, 300, 400, 330), color=None, fill=(1, 1, 1))
        elif case == "page_background_after":
            label_value(pg); pg.draw_rect(pg.rect, color=None, fill=(1, 1, 1))
        elif case == "identical_redraw":
            label_value(pg); pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(1, 1, 1))
            pg.insert_text((205, 100), "29-Aug-2025", fontname="helv", fontsize=9.5)
        elif case == "faint_watermark_under_box":
            pg.insert_text((205, 100), "SAMPLE", fontname="hebo", fontsize=9.5, fill_opacity=0.18)
            pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(1, 1, 1))
        elif case == "off_white_box":
            label_value(pg); pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(0.9, 0.9, 0.9))
        elif case == "white_stroke_only":
            label_value(pg); pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=(1, 1, 1), fill=None)
    fs = P.analyze(make_pdf(tmp_path / "l.pdf", build), "x")
    assert "covered_text" not in ids(fs), case


@pytest.mark.parametrize("dt", sorted({e["doc_type"] for e in c.iter_docs(clean_only=True) if not c.is_scan(e)}))
def test_cover_up_on_every_document_type(tmp_path, dt):
    """Generalisation beyond fire-certificate dates: cover any value-like span of a clean document
    of this type with a white box, type something else on top, save normally -> covered_text."""
    e = c.first_doc(dt, clean_only=True, digital=True)
    out = tmp_path / "forged.pdf"
    with pymupdf.open(e["path"]) as d:
        pg = d[0]
        spans = [s for s in P._spans(pg) if s["horizontal"] and s["opacity"] > 0.5 and any(ch.isdigit() for ch in s["text"])
                 and 20 < s["bbox"][2] - s["bbox"][0] < 200]
        s = spans[0]
        r = pymupdf.Rect(*s["bbox"]) + (-1, -1, 1, 1)
        pg.draw_rect(r, color=None, fill=(1, 1, 1))
        pg.insert_text((s["bbox"][0], s["bbox"][3] - 2), "9999", fontname="cobo", fontsize=9)
        d.save(str(out))
    fs = P.analyze(out, "x", dt)
    assert "covered_text" in ids(fs) and "incremental_update" not in ids(fs)
    assert s["text"].strip().startswith(next(f for f in fs if f.id == "covered_text").evidence["hidden_text"][:3])


# ----------------------------------------------------------------------------- incremental updates
def _base_pdf(tmp_path, name="base.pdf"):
    return make_pdf(tmp_path / name, label_value, {"creator": "SynthIssuer/iText 5.5.13",
                                                     "creationDate": "D:20251005103000+05'30'",
                                                     "modDate": "D:20251005103000+05'30'"})


def test_incremental_cover_up_built_with_pymupdf(tmp_path):
    p = _base_pdf(tmp_path)
    assert "incremental_update" not in ids(P.analyze(p, "x"))
    with pymupdf.open(str(p)) as d:                       # incremental save: rev 2 appended in place
        pg = d[0]
        pg.draw_rect(pymupdf.Rect(203, 91, 270, 104), color=None, fill=(1, 1, 1))
        pg.insert_text((205, 100), "08-Jan-2027", fontname="cobo", fontsize=9.5)
        d.saveIncr()
    data = p.read_bytes()
    assert len(P.revision_ends(data)) == 2 and b"/Prev" in data
    fs = {f.id: f for f in P.analyze(p, "x")}
    assert {"incremental_update", "covered_text"} <= set(fs)
    inc = fs["incremental_update"]
    assert inc.p == CFG["p_incremental_update"] and "08-Jan-2027" in inc.evidence["added_text"]
    assert 195 < inc.bbox[1] < 215                         # points at the date field
    assert fs["covered_text"].evidence["hidden_text"] == "29-Aug-2025"


def test_incremental_without_visible_change_is_weaker(tmp_path):
    p = _base_pdf(tmp_path)
    w = PdfWriter(fileobj=str(p), incremental=True)         # pypdf incremental update: metadata only
    w.add_metadata({"/Title": "re-saved"})
    w.write(str(p))
    assert len(P.revision_ends(p.read_bytes())) == 2
    inc = [f for f in P.analyze(p, "x") if f.id == "incremental_update"][0]
    assert inc.p == CFG["p_incremental_no_visible_change"] and inc.bbox is None
    assert inc.evidence["changed"] is False


def test_linearized_header_is_not_a_revision():
    one = b"%PDF-1.4\n<< /Linearized 1 >>\nstartxref\n0\n%%EOF\n...body...\nstartxref\n123\n%%EOF\n"
    assert len(P.revision_ends(one)) == 1
    plain = b"%PDF-1.4\nbody\nstartxref\n10\n%%EOF\nmore\nstartxref\n99\n%%EOF\n"
    assert len(P.revision_ends(plain)) == 2
    assert P.revision_ends(b"%PDF-1.4 %%EOF in a string only") == []


def test_signed_content_changed(tmp_path):
    """Content differs from the revision that a /ByteRange covers (synthetic marker, no real PKI)."""
    p = _base_pdf(tmp_path)
    first = p.read_bytes()
    with pymupdf.open(str(p)) as d:
        d[0].insert_text((205, 140), "added after signing", fontname="helv", fontsize=9)
        d.saveIncr()
    data = p.read_bytes() + b"\n%% /ByteRange [0 %d %d 0]\n" % (len(first), len(first))
    (tmp_path / "s.pdf").write_bytes(data)
    f = [f for f in P.analyze(tmp_path / "s.pdf", "x") if f.id == "signed_content_changed"]
    assert f and f[0].p == CFG["p_signed_content_changed"] and "added after signing" in f[0].evidence["added_text"]


# ----------------------------------------------------------------------------- metadata, fonts
def test_parse_pdf_date():
    d = P.parse_pdf_date("D:20251005103000+05'30'")
    assert (d.year, d.month, d.day, d.hour) == (2025, 10, 5, 10)
    assert P.parse_pdf_date("D:2025").month == 1
    assert P.parse_pdf_date("") is None and P.parse_pdf_date("garbage") is None and P.parse_pdf_date(None) is None


@pytest.mark.parametrize("creator,mod,expect", [
    ("SynthIssuer/iText 5.5.13", "D:20251005103000", None),                    # clean: modified == created
    ("ilovepdf.com", "D:20251005103000", ("editor", CFG["p_editor_name"])),     # name only
    ("SynthIssuer/iText 5.5.13", "D:20260219160500", ("gap", CFG["p_gap_only"])),   # gap only
    ("Adobe Photoshop 25.2 (Windows)", "D:20260219160500", ("both", CFG["p_editor_and_gap"])),
    ("Sejda PDF Editor", "D:20251006103000", ("editor", CFG["p_editor_name"])),    # 1 day: below the gap rule
    ("SynthIssuer/Apache FOP 2.6", "D:20251010103000", None),                   # 5 days: below the gap rule
])
def test_editor_metadata_rules(tmp_path, creator, mod, expect):
    p = make_pdf(tmp_path / "m.pdf", label_value, {"creator": creator, "producer": creator,
                                                    "creationDate": "D:20251005103000", "modDate": mod})
    got = [f for f in P.analyze(p, "x") if f.id == "editor_metadata"]
    if expect is None:
        assert not got
    else:
        assert len(got) == 1 and got[0].p == expect[1] and got[0].kind == "soft" and got[0].tier == 3


def test_font_anomaly_uses_doc_type_allowlist(tmp_path):
    def build(pg):
        label_value(pg); pg.insert_text((205, 120), "typed in", fontname="cobo", fontsize=9)
    p = make_pdf(tmp_path / "f.pdf", build)
    f = [f for f in P.analyze(p, "x", "fire_cert") if f.id == "font_anomaly"]
    assert len(f) == 1 and "Courier-Bold" in f[0].evidence["fonts"] and f[0].bbox[0] == 1
    assert "font_anomaly" not in ids(P.analyze(p, "x", "no_such_type"))
    assert "font_anomaly" not in ids(P.analyze(p, "x"))                      # no doc type: no allow-list
    cfg = {**CFG, "fonts_by_doc_type": {"fire_cert": ["Helvetica", "Helvetica-Bold", "Courier-Bold"]}}
    assert "font_anomaly" not in ids(P.analyze(p, "x", "fire_cert", cfg))


def test_norm_font_strips_subset_prefix():
    assert P.norm_font("ABCDEF+Helvetica-Bold") == "Helvetica-Bold" and P.norm_font("Times-Roman") == "Times-Roman"


# ----------------------------------------------------------------------------- contract / robustness
def test_findings_obey_contract(tmp_path):
    e = FORGERIES[0]
    for f in P.analyze(e["path"], e["doc_id"], e["doc_type"]):
        assert isinstance(f, Finding) and f.module == "pdf_forensics" and f.kind in ("soft", "info") and f.tier == 3
        Finding.from_json(json.loads(json.dumps(f.to_json())))


def test_bad_inputs_fail_safe(tmp_path):
    (tmp_path / "empty.pdf").write_bytes(b"")
    (tmp_path / "garbage.pdf").write_bytes(b"%PDF-1.4 garbage")
    (tmp_path / "text.pdf").write_bytes(b"hello")
    good = _base_pdf(tmp_path)
    (tmp_path / "trunc.pdf").write_bytes(good.read_bytes()[:200])
    for n in ("empty.pdf", "garbage.pdf", "text.pdf", "trunc.pdf", "missing.pdf"):
        fs = P.analyze(tmp_path / n, "x")
        assert len(fs) == 1 and fs[0].kind == "info" and fs[0].id in ("pdf_unreadable", "pdf_not_applicable"), n
    assert ids(P.analyze(good, "x")) == {"pdf_structure_ok"}


def test_scan_has_no_text_checks():
    e = c.first_doc(label="scan_clean")
    assert ids(P.analyze(e["path"], e["doc_id"], e["doc_type"])) == {"pdf_structure_ok"}


def test_cli(capsys):
    e = FORGERIES[0]
    assert P.main([e["path"], "--doc-type", e["doc_type"]]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(f["id"] == "covered_text" for f in out)
