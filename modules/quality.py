"""Scan-quality gate: blur (variance of Laplacian) and skew (projection profile).

Input : path to a PDF or image file (page 1 by default, or all pages via config).
Output: list[Finding] (a gate finding per defect, or one info finding "quality_ok").
Detects: global blur and page tilt. Deliberately does NOT detect: low contrast,
shadows, cropping, missing pages, bad OCR, or anything about content/tampering.

Both measures are computed on a grayscale image normalised to a fixed width
(config.norm_width) so they do not depend on the file's resolution.
Findings are tier 4 / soft: the gate asks the applicant to re-scan; it never
rejects. Metrics are exposed via measure() for calibration.
The blur threshold is per document type when config has vol_min_by_doc_type (derived from the
train split of the graded-blur set, see reports/quality_calibration.md), else the global vol_min.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml

from contracts import Finding

MODULE = "quality"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "quality.yaml"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def render_gray(path: str | Path, page: int, norm_width: int) -> np.ndarray:
    """Render one page (or load an image) to grayscale at exactly norm_width px wide."""
    p = Path(path)
    if p.suffix.lower() in _IMG_EXT:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("unreadable image")
    else:
        import pymupdf
        with pymupdf.open(str(p)) as doc:
            if doc.page_count == 0:
                raise ValueError("PDF has no pages")
            pg = doc[page]
            zoom = norm_width / pg.rect.width
            pm = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                               colorspace=pymupdf.csGRAY, alpha=False)
            img = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width).copy()
    h, w = img.shape
    if w != norm_width:
        s = norm_width / w
        img = cv2.resize(img, (norm_width, max(1, round(h * s))),
                         interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    return img


def variance_of_laplacian(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def estimate_skew(gray: np.ndarray, max_deg: float = 15.0, step: float = 0.5,
                  work_width: int = 500) -> Optional[float]:
    """Skew in degrees (positive = content rotated counter-clockwise) or None if
    there is too little ink to tell. Projection-profile search: the angle whose
    horizontal ink profile has maximal variance is the straightening angle."""
    h, w = gray.shape
    s = work_width / w
    g = cv2.resize(gray, (work_width, max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if bw.sum() / 255 < 200:          # essentially blank page
        return None
    hh, ww = bw.shape
    c = (ww / 2, hh / 2)
    best, best_a = -1.0, 0.0
    for a in np.arange(-max_deg, max_deg + 1e-9, step):
        m = cv2.getRotationMatrix2D(c, float(a), 1.0)
        r = cv2.warpAffine(bw, m, (ww, hh), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = r.sum(axis=1, dtype=np.float64)
        score = float(np.sum(np.diff(prof) ** 2))
        if score > best:
            best, best_a = score, float(a)
    # rotating the image by +a (CCW) straightens content that was tilted CW by a
    return -best_a


def measure(path: str | Path, page: int = 0, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    g = render_gray(path, page, cfg["norm_width"])
    return {"vol": round(variance_of_laplacian(g), 1),
            "skew_deg": estimate_skew(g, cfg["skew_search_deg"], cfg["skew_step_deg"])}


def vol_min_for(cfg: dict, doc_type: Optional[str]) -> float:
    return float(cfg.get("vol_min_by_doc_type", {}).get(doc_type, cfg["vol_min"]))


def analyze(path: str | Path, doc_id: str = "", cfg: Optional[dict] = None,
            doc_type: Optional[str] = None) -> list[Finding]:
    """Run the gate. Never raises on bad input; returns an info/soft finding instead."""
    cfg = cfg or load_config()
    vol_min = vol_min_for(cfg, doc_type)
    try:
        n_pages = 1
        if Path(path).suffix.lower() not in _IMG_EXT:
            import pymupdf
            with pymupdf.open(str(path)) as d:
                n_pages = d.page_count
        pages = range(n_pages) if cfg.get("all_pages") else range(min(1, n_pages))
        if n_pages == 0:
            raise ValueError("PDF has no pages")
        out: list[Finding] = []
        worst = None
        for pg in pages:
            m = measure(path, pg, cfg)
            blurry = m["vol"] < vol_min
            sk = m["skew_deg"]
            tilted = sk is not None and abs(sk) > cfg["skew_max_deg"]
            if worst is None:
                worst = (m, pg)
            if blurry:
                out.append(Finding(
                    "blurry", MODULE, "soft", 4, cfg["p_blurry"],
                    f"Page {pg + 1} looks too blurry to read reliably. Please re-scan it "
                    f"in focus at a higher resolution.", doc_id, (pg + 1, 0, 0, 0, 0),
                    {"variance_of_laplacian": m["vol"], "threshold": vol_min}))
            if tilted:
                out.append(Finding(
                    "tilted", MODULE, "soft", 4, cfg["p_tilted"],
                    f"Page {pg + 1} is tilted by about {abs(sk):.0f} degrees. Please "
                    f"re-scan it straight.", doc_id, (pg + 1, 0, 0, 0, 0),
                    {"skew_deg": sk, "threshold_deg": cfg["skew_max_deg"]}))
        if not out:
            m, _ = worst
            out.append(Finding("quality_ok", MODULE, "info", 4, 0.0,
                               "Image quality is acceptable.", doc_id, None,
                               {"variance_of_laplacian": m["vol"], "skew_deg": m["skew_deg"]}))
        return out
    except Exception as exc:  # fail safe: never crash the pipeline
        return [Finding("quality_unreadable", MODULE, "info", 4, 0.0,
                        "Could not check image quality because the file could not be read.",
                        doc_id, None, {"error": f"{type(exc).__name__}: {exc}"})]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.quality")
    ap.add_argument("file")
    ap.add_argument("--config"); ap.add_argument("--doc-type")
    a = ap.parse_args(argv)
    fs = analyze(a.file, Path(a.file).stem, load_config(a.config), a.doc_type)
    json.dump([f.to_json() for f in fs], sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
