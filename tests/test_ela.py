import json

import cv2
import numpy as np
import pymupdf
import pytest

from contracts import Finding
from harness import corpus_io as c
from modules import ela as E

CFG = E.load_config()


def textured(h=480, w=640, seed=3):
    rs = np.random.RandomState(seed)
    x = cv2.GaussianBlur((rs.rand(h, w, 3) * 255).astype(np.float32), (0, 0), 1.2)
    return np.clip(x * 0.8 + 30, 0, 255).astype(np.uint8)


def jpeg_roundtrip(img, q):
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], cv2.IMREAD_COLOR)


# ----------------------------------------------------------------------------- mechanics
def test_error_map_basic_properties():
    flat = np.full((64, 64, 3), 200, np.uint8)
    assert E.ela_error(flat, 90).max() == 0                         # a flat image survives JPEG exactly
    img = textured()
    err = E.ela_error(img, 90)
    assert err.shape == img.shape[:2] and err.dtype == np.float32 and 0 < err.mean() < 30
    assert E.ela_error(img, 50).mean() > E.ela_error(img, 95).mean()   # lower quality -> larger error


def test_resaving_at_the_same_quality_is_stable():
    """An image that already went through q=90 changes little when re-saved at q=90 (the ELA premise)."""
    once = jpeg_roundtrip(textured(), 90)
    assert E.ela_error(once, 90).mean() < 0.6 * E.ela_error(textured(), 90).mean()


def test_amplify_and_heatmap():
    err = np.zeros((10, 10), np.float32); err[3, 3] = 8
    amp = E.amplify(err, "auto")
    assert amp.dtype == np.uint8 and amp.max() == 255 and amp[0, 0] == 0
    assert E.amplify(err, 10).max() == 80 and E.amplify(np.zeros((4, 4), np.float32)).max() == 0
    hm = E.heatmap(amp)
    assert hm.shape == (10, 10, 3) and hm.dtype == np.uint8


def test_pasted_region_is_localised(tmp_path):
    """A region with a different compression history stands out: higher score, bbox on the patch."""
    base = jpeg_roundtrip(textured(), 60)                           # whole page: one (low) quality history
    rs = np.random.RandomState(9)
    patch = cv2.GaussianBlur((rs.rand(96, 160, 3) * 255).astype(np.float32), (0, 0), 1.2)
    base[200:296, 300:460] = np.clip(patch * 0.8 + 30, 0, 255).astype(np.uint8)   # never compressed
    p = tmp_path / "tampered.png"; cv2.imwrite(str(p), base)
    clean = tmp_path / "clean.png"; cv2.imwrite(str(clean), jpeg_roundtrip(textured(), 60))
    hm = tmp_path / "heat.png"
    cfg = {**CFG, "suspicious_score": 5.0}
    fs = E.analyze(p, "t", cfg, hm)
    sc_t = fs[0].evidence["score"]; sc_c = E.analyze(clean, "c", cfg)[0].evidence["score"]
    assert sc_t > sc_c + 3
    sus = [f for f in fs if f.id == "ela_suspicious"][0]
    page, x, y, w, h = sus.bbox
    assert page == 1 and 250 <= x <= 340 and 150 <= y <= 240 and 100 <= w <= 260 and 60 <= h <= 160
    assert sus.kind == "soft" and sus.tier == 4                      # image heuristic: advisory
    out = cv2.imread(str(hm)); assert out.shape == base.shape


def test_ela_suspicious_can_never_be_hard():
    with pytest.raises(ValueError):
        Finding("ela_suspicious", "ela", "hard", 4, 0.9, "x", "d")


def test_flat_page_is_quiet(tmp_path):
    p = tmp_path / "white.png"; cv2.imwrite(str(p), np.full((800, 600, 3), 255, np.uint8))
    fs = E.analyze(p, "w", {**CFG, "suspicious_score": 0.1})
    assert [f.id for f in fs] == ["ela_summary"] and fs[0].evidence["score"] == 0.0


# ----------------------------------------------------------------------------- pdf input
def test_digital_pdf_is_rasterised_at_render_dpi():
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    f = E.analyze(e["path"], e["doc_id"])[0]
    w, h = e["page_size_pt"]
    assert abs(f.evidence["image_px"][0] - w * CFG["render_dpi"] / 72) <= 2 and f.id == "ela_summary"


def test_scan_pdf_uses_native_image():
    e = c.first_doc(label="scan_clean")
    with pymupdf.open(e["path"]) as d:
        xref = d[0].get_images()[0][0]; pm = pymupdf.Pixmap(d, xref)
    f = E.analyze(e["path"], e["doc_id"])[0]
    assert f.evidence["image_px"] == [pm.width, pm.height]


def test_clean_documents_do_not_trigger_with_default_threshold():
    """Placeholder threshold sits above every clean-page score on this corpus (tier-4 advisory only)."""
    n = 0
    for e in c.iter_docs(clean_only=True):
        n += 1
        assert not [f for f in E.analyze(e["path"], e["doc_id"]) if f.kind != "info"], e["doc_id"]
        if n >= 80:
            break


# ----------------------------------------------------------------------------- robustness / cli
def test_bad_inputs(tmp_path):
    (tmp_path / "empty.pdf").write_bytes(b""); (tmp_path / "bad.png").write_bytes(b"nope")
    for n in ("empty.pdf", "bad.png", "missing.pdf"):
        fs = E.analyze(tmp_path / n, "x")
        assert len(fs) == 1 and fs[0].id == "ela_unreadable" and fs[0].kind == "info"


def test_cli(tmp_path, capsys):
    p = tmp_path / "i.png"; cv2.imwrite(str(p), textured())
    h = tmp_path / "h.png"
    assert E.main([str(p), "--heatmap", str(h)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["id"] == "ela_summary" and h.exists()
