import cv2, numpy as np
import pytest
from harness import corpus_io as c
from modules import quality as q


def ids(path, doc_type=None): return {f.id for f in q.analyze(path, "x", doc_type=doc_type)}


@pytest.mark.parametrize("e", list(c.iter_docs(label="scan_degraded")), ids=lambda e: e["doc_id"])
def test_degraded_scans_fail(e):
    got = ids(e["path"], e["doc_type"])
    assert got & {"blurry", "tilted"}
    sev = e["quality"]["severity"]
    assert ("blurry" in got) == ("blur" in sev)
    assert ("tilted" in got) == ("skew" in sev)


@pytest.mark.parametrize("e", list(c.iter_docs(label="scan_clean")), ids=lambda e: e["doc_id"])
def test_clean_scans_pass(e):
    assert ids(e["path"], e["doc_type"]) == {"quality_ok"}


def test_scans_with_other_labels_pass_blur_gate():
    """copy-move and re-scanned near-duplicate scans are sharp and straight: the gate stays quiet."""
    for lab in ("copy_move_scan", "visual_near_duplicate"):
        for e in c.iter_docs(label=lab):
            assert ids(e["path"], e["doc_type"]) == {"quality_ok"}, e["doc_id"]


def test_digital_pages_pass_sample():
    n = 0
    for e in c.iter_docs(clean_only=True):
        if not c.is_scan(e):
            assert ids(e["path"], e["doc_type"]) == {"quality_ok"}, e["doc_id"]
            n += 1
            if n >= 60:
                break


GRADED = list(c.iter_graded())


@pytest.mark.skipif(not GRADED, reason="corpus has no graded-blur set")
@pytest.mark.parametrize("sigma,blurry", [(0.0, False), (0.5, False), (1.0, True), (1.5, True), (3.0, True)])
def test_graded_blur_levels(sigma, blurry):
    """Calibration sanity: clear blur levels are classified on both sides of the boundary
    (sigma 0.75 is the borderline level and is deliberately not asserted)."""
    rows = [g for g in GRADED if g["labels"][0]["blur_sigma"] == sigma][:8]
    assert rows
    for g in rows:
        assert ("blurry" in ids(g["path"], g["doc_type"])) == blurry, g["doc_id"]


def test_per_doc_type_threshold_lookup():
    cfg = q.load_config()
    assert q.vol_min_for(cfg, "no_such_type") == cfg["vol_min"]
    typ, val = next(iter(cfg["vol_min_by_doc_type"].items()))
    assert q.vol_min_for(cfg, typ) == val
    cfg2 = {**cfg, "vol_min_by_doc_type": {typ: 1e9}}              # absurd threshold: everything blurry
    e = c.first_doc(typ, clean_only=True, digital=True)
    assert "blurry" in {f.id for f in q.analyze(e["path"], "x", cfg2, doc_type=typ)}
    assert "blurry" not in {f.id for f in q.analyze(e["path"], "x", cfg2, doc_type="other")}


def test_edge_cases(tmp_path):
    (tmp_path / "empty.pdf").write_bytes(b"")
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 garbage")
    for n in ("empty.pdf", "bad.pdf", "missing.pdf"):
        fs = q.analyze(tmp_path / n, "x")
        assert fs[0].id == "quality_unreadable" and fs[0].kind == "info"


def test_blank_image_no_text(tmp_path):
    p = tmp_path / "blank.png"; cv2.imwrite(str(p), np.full((1400, 1000), 255, np.uint8))
    fs = q.analyze(p, "x")                   # blank page: sharpness is undefined, skew unknown
    assert all(f.tier == 4 and f.kind != "hard" for f in fs)


def test_synthetic_rotation_detected(tmp_path):
    e = c.first_doc("fire_cert", clean_only=True, digital=True)
    g = q.render_gray(e["path"], 0, 1000)
    m = cv2.getRotationMatrix2D((500, g.shape[0] / 2), 8, 1)
    r = cv2.warpAffine(g, m, (1000, g.shape[0]), borderValue=255)
    p = tmp_path / "rot.png"; cv2.imwrite(str(p), r)
    assert "tilted" in ids(p)
