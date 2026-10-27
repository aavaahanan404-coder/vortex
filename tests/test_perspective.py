import json

import cv2
import numpy as np
import pymupdf
import pytest

from contracts import Finding
from harness import corpus_io as c
from harness.make_photo import photograph
from modules import perspective as P

CFG = P.load_config()


def page_image(w=910, h=1287, seed=0, header=(120, 70, 30)):
    """A synthetic A4 'scan': white page, dark header band, text-like lines."""
    img = np.full((h, w, 3), 250, np.uint8)
    cv2.rectangle(img, (0, 0), (w, 110), header, -1)
    rs = np.random.RandomState(seed)
    for i in range(18):
        y = 200 + i * 55
        cv2.putText(img, f"Field {i}: SYN-{rs.randint(10000, 99999)} value", (60, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    return img


def ncc(a, b):
    a = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (300, 424)).astype(np.float32); a -= a.mean()
    b = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (300, 424)).astype(np.float32); b -= b.mean()
    return float((a * b).sum() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def ids(fs): return [f.id for f in fs]


def test_order_corners_and_tilt():
    q = P.order_corners(np.array([[100, 400], [10, 10], [90, 20], [20, 380]]))
    assert q.tolist() == [[10, 10], [90, 20], [100, 400], [20, 380]]
    assert abs(P.tilt_deg(P.order_corners(np.array([[0, 0], [100, 0], [100, 50], [0, 50]])))) < 1e-6


def test_warp_gives_the_requested_flat_rectangle():
    img = np.zeros((600, 800, 3), np.uint8)
    cv2.rectangle(img, (200, 100), (600, 500), (255, 255, 255), -1)
    flat = P.warp(img, np.array([[200, 100], [600, 100], [600, 500], [200, 500]], np.float32), 400, 1.5)
    assert flat.shape[:2] == (600, 400) and flat.mean() > 245                       # whole output is page, no background


@pytest.mark.parametrize("seed", range(6))
def test_page_is_found_on_dark_wood_and_light_backgrounds(seed):
    page = page_image(seed=seed)
    photo, true = photograph(page, seed=seed)
    q, info = P.detect_quad(photo)
    assert q is not None
    diag = float(np.hypot(*(true[2] - true[0])))
    assert float(np.mean(np.linalg.norm(q - P.order_corners(true), axis=1))) < 0.012 * diag, info
    flat, st = P.correct(photo)
    assert st["status"] == "corrected" and ncc(flat, page) > 0.8


def test_dark_header_on_dark_table_needs_the_colour_edges():
    """Regression: with grey-level edges only, the top edge of a page whose header is as dark as the table was missed."""
    page = page_image()
    photo, true = photograph(page, seed=1, kind="wood")
    q, _ = P.detect_quad(photo, {**CFG, "canny_color": [255, 255]})                  # colour edges effectively off
    q2, _ = P.detect_quad(photo)
    t = P.order_corners(true)
    assert np.linalg.norm(q2 - t, axis=1).max() < 25
    assert q is None or np.linalg.norm(q - t, axis=1).max() > 60


@pytest.mark.xfail(strict=True, reason="KNOWN LIMIT: when the page's dark header is exactly the colour of a dark table the detector takes the "
                   "header/body boundary as the top edge (it has real contrast). The corrected page is then cut short; neither the proportions "
                   "(foreshortening alone moves them by up to 11%) nor edge contrast reveals it. Needs a template- or camera-aware check.")
def test_invisible_top_edge_is_detected_or_refused():
    page = page_image(header=(60, 60, 60))
    photo, true = photograph(page, seed=0)                                                # dark table (45, 50, 55)
    flat, st = P.correct(photo)
    assert st["status"] != "corrected" or ncc(flat, page) > 0.8


def test_flat_full_page_scan_is_left_alone():
    page = page_image()
    flat, st = P.correct(page)
    assert st["status"] == "not_needed" and flat is page


def test_cut_off_page_is_reported_not_silently_warped():
    page = page_image()
    photo, _ = photograph(page, seed=2, tilt=4.0, jitter=0.02)
    crop = photo[:, 500:]                                                         # the left part of the page is outside the photo
    flat, st = P.correct(crop)
    assert st["status"] in ("partial", "not_found")


def test_no_page_in_the_photo():
    rs = np.random.RandomState(1)
    noise = np.clip(rs.normal(120, 40, (900, 700, 3)), 0, 255).astype(np.uint8)
    flat, st = P.correct(noise)
    assert flat is None and st["status"] == "not_found"
    plain = np.full((900, 700, 3), 130, np.uint8)
    assert P.correct(plain)[1]["status"] == "not_found"


def test_findings_contract_and_routing(tmp_path):
    page = page_image()
    photo, _ = photograph(page, seed=0)
    src = tmp_path / "photo.jpg"; cv2.imwrite(str(src), photo)
    out = tmp_path / "flat.png"
    fs = P.analyze(src, "d1", CFG, out)
    assert ids(fs) == ["perspective_corrected"] and fs[0].kind == "info" and fs[0].tier == 4 and out.exists()
    assert cv2.imread(str(out)).shape[1] == CFG["output_width"] and len(fs[0].evidence["corners"]) == 4
    assert ids(P.analyze(_write(tmp_path, "flat.png", page), "d2", CFG)) == ["perspective_not_needed"]
    noise = np.clip(np.random.RandomState(1).normal(120, 40, (900, 700, 3)), 0, 255).astype(np.uint8)
    nf = P.analyze(_write(tmp_path, "noise.png", noise), "d3", CFG)[0]
    assert nf.id == "document_not_found" and nf.kind == "soft" and nf.tier == 4                  # image heuristic: never hard
    assert "photograph the whole page" in nf.message
    for f in fs + [nf]:
        Finding.from_json(json.loads(json.dumps(f.to_json())))


def _write(tmp_path, name, img):
    p = tmp_path / name; cv2.imwrite(str(p), img); return p


def test_bad_inputs_and_pdf_with_an_embedded_scan(tmp_path):
    (tmp_path / "bad.png").write_bytes(b"nope"); (tmp_path / "e.pdf").write_bytes(b"")
    for n in ("bad.png", "e.pdf", "missing.jpg"):
        fs = P.analyze(tmp_path / n, "x", CFG)
        assert ids(fs) == ["perspective_unreadable"] and fs[0].kind == "info"
    e = c.first_doc(label="scan_clean")
    assert ids(P.analyze(e["path"], e["doc_id"], CFG)) == ["perspective_not_needed"]        # a real corpus scan is already flat


def test_corpus_scans_photographed_are_corrected_and_still_readable():
    """End to end on real corpus pages: photo -> corners -> flat page whose fields OCR reads (skipped without Tesseract)."""
    from modules import extract as X
    scans = [e for e in c.iter_docs(clean_only=True) if c.is_scan(e)][:6]
    for i, e in enumerate(scans):
        page = P.load_image(e["path"])
        photo, true = photograph(page, seed=i)
        flat, st = P.correct(photo)
        assert st["status"] == "corrected"
        diag = float(np.hypot(*(true[2] - true[0])))
        assert float(np.mean(np.linalg.norm(np.array(st["corners"]) - P.order_corners(true), axis=1))) < 0.01 * diag
        assert ncc(flat, page) > 0.8
    if X.ocr_available():
        import re, tempfile
        from pathlib import Path
        e = scans[0]; page = P.load_image(e["path"]); photo, _ = photograph(page, seed=0)
        flat, _ = P.correct(photo)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.png"; cv2.imwrite(str(p), flat)
            doc, _ = X.extract(p, e["doc_id"], e["doc_type"])
        norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
        truth = {k: v for k, v in e["fields"].items() if k not in ("qr", "seal", "signature") and v.get("value")}
        ok = sum(1 for k, v in truth.items() if k in doc.fields and norm(doc.fields[k]["value"]) == norm(v["value"]))
        assert ok / len(truth) >= 0.8


def test_cli(tmp_path, capsys):
    photo, _ = photograph(page_image(), seed=0)
    src = _write(tmp_path, "p.jpg", photo)
    assert P.main([str(src), "--out", str(tmp_path / "o.png")]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "perspective_corrected" and (tmp_path / "o.png").exists()
