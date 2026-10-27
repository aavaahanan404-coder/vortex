"""Error Level Analysis: re-save as JPEG, absolute difference, amplify, heatmap + summary score.

Input : PDF (page 1) or image path; optional path to write the heatmap PNG.
Output: list[Finding]: ela_summary (info, always; evidence = statistics + heatmap path) and
        ela_suspicious (soft, TIER 4 - image heuristic, advisory, never hard) when the localisation
        score reaches config suspicious_score. bbox = union of hot blocks in PDF points (page, x,y,w,h).
Mechanics: error = |I - JPEG_q(I)| (max over channels), amplified (auto gain: p99.9 -> 255), colour-mapped.
Score: the page is cut into blocks; blocks with content (gray std >= content_std_min) get their mean
error; score = max robust z (median/MAD over content blocks); "hot" blocks are those above z_hot.
Does NOT detect: anything on text documents reliably (flat white page, error lives on glyph edges);
tampering that was saved at the same quality as the rest of the page; copy-move inside one
compression history; vector-level edits (use pdf_forensics). NOT evaluated on tamper data yet:
see reports/BATCH_B_REPORT.md for the clean-document score distribution only.
Fail-safe: unreadable input gives an info finding.
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

MODULE = "ela"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "ela.yaml"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_image(path: str | Path, cfg: dict) -> tuple[np.ndarray, float]:
    """(BGR image, pixels per PDF point). Scans use the native embedded image, digital pages are
    rasterised at render_dpi."""
    p = Path(path)
    if p.suffix.lower() in _IMG_EXT:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("unreadable image")
        return img, 1.0
    import pymupdf
    with pymupdf.open(str(p)) as d:
        if d.page_count == 0:
            raise ValueError("PDF has no pages")
        pg = d[0]
        infos = pg.get_image_info(xrefs=True)
        if len(infos) == 1:
            r = pymupdf.Rect(infos[0]["bbox"]) & pg.rect
            if r.get_area() >= cfg["native_image_min_cover"] * pg.rect.get_area() and infos[0].get("xref"):
                pm = pymupdf.Pixmap(d, infos[0]["xref"])
                if pm.n >= 4:
                    pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
                a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
                bgr = cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pm.n == 3 else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
                return bgr, pm.width / pg.rect.width
        z = cfg["render_dpi"] / 72.0
        pm = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csRGB, alpha=False)
        a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, 3)
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR), z


def ela_error(bgr: np.ndarray, quality: int) -> np.ndarray:
    """Raw error map |I - JPEG_quality(I)|, max over channels, float32 in 0..255."""
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG re-save failed")
    re = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return np.abs(bgr.astype(np.int16) - re.astype(np.int16)).max(axis=2).astype(np.float32)


def amplify(err: np.ndarray, gain="auto") -> np.ndarray:
    """Scale the error map to uint8. 'auto' stretches the p99.9 value to 255."""
    if gain == "auto":
        top = float(np.percentile(err, 99.9))
        g = 255.0 / top if top > 0 else 1.0
    else:
        g = float(gain)
    return np.clip(err * g, 0, 255).astype(np.uint8)


def heatmap(amp: np.ndarray, colormap: str = "JET") -> np.ndarray:
    return cv2.applyColorMap(amp, getattr(cv2, f"COLORMAP_{colormap}"))


def block_scores(err: np.ndarray, gray: np.ndarray, block: int, std_min: float) -> dict:
    """Robust z of each content block's mean error. Returns {z: 2D array (nan = no content), n_content}."""
    h, w = err.shape
    bh, bw = h // block, w // block
    e = err[:bh * block, :bw * block].reshape(bh, block, bw, block).mean(axis=(1, 3))
    g = gray[:bh * block, :bw * block].astype(np.float32).reshape(bh, block, bw, block)
    content = g.std(axis=(1, 3)) >= std_min
    z = np.full((bh, bw), np.nan, np.float32)
    if content.sum() >= 5:
        v = e[content]
        med = float(np.median(v)); mad = float(np.median(np.abs(v - med))) * 1.4826
        z[content] = (e[content] - med) / max(mad, 0.05)       # floor: a perfectly flat error has no outliers
    return {"z": z, "n_content": int(content.sum())}


def analyze(path: str | Path, doc_id: str = "", cfg: Optional[dict] = None,
            heatmap_path: Optional[str | Path] = None) -> list[Finding]:
    cfg = cfg or load_config()
    try:
        bgr, ppp = load_image(path, cfg)
        err = ela_error(bgr, cfg["jpeg_quality"])
        amp = amplify(err, cfg["amplify"])
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        bs = block_scores(err, gray, cfg["block"], cfg["content_std_min"])
        z = bs["z"]
        zmax = float(np.nanmax(z)) if np.isfinite(z).any() else 0.0
        hot = np.argwhere(np.nan_to_num(z, nan=-1) >= cfg["z_hot"])
        stats = {"mean_error": round(float(err.mean()), 4), "p99_error": round(float(np.percentile(err, 99)), 3),
                 "max_error": round(float(err.max()), 1), "score": round(zmax, 2), "n_hot_blocks": int(len(hot)),
                 "n_content_blocks": bs["n_content"], "jpeg_quality": cfg["jpeg_quality"],
                 "image_px": [int(bgr.shape[1]), int(bgr.shape[0])]}
        if heatmap_path:
            cv2.imwrite(str(heatmap_path), heatmap(amp, cfg["heatmap_colormap"]))
            stats["heatmap"] = str(heatmap_path)
    except Exception as exc:
        return [Finding("ela_unreadable", MODULE, "info", 4, 0.0,
                        "Could not run image-tampering analysis because the file could not be read.",
                        doc_id, None, {"error": f"{type(exc).__name__}: {exc}"})]
    out = [Finding("ela_summary", MODULE, "info", 4, 0.0, "Error-level analysis computed.", doc_id, None, stats)]
    if zmax >= cfg["suspicious_score"] and len(hot):
        b = cfg["block"]
        y0, x0 = hot.min(axis=0) * b
        y1, x1 = (hot.max(axis=0) + 1) * b
        bbox = (1, round(x0 / ppp, 2), round(y0 / ppp, 2), round((x1 - x0) / ppp, 2), round((y1 - y0) / ppp, 2))
        out.append(Finding("ela_suspicious", MODULE, "soft", 4, cfg["p_suspicious"],
                           "Part of the page has a different image-compression history from the rest, which "
                           "can indicate that it was pasted in. This is an advisory signal only.",
                           doc_id, bbox, stats))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.ela")
    ap.add_argument("file"); ap.add_argument("--heatmap", help="write the heatmap PNG here")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    fs = analyze(a.file, Path(a.file).stem, load_config(a.config), a.heatmap)
    json.dump([f.to_json() for f in fs], sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
