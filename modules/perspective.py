"""Perspective correction for phone photos of documents: find the page, warp it flat.

Input : an image file (or a PDF whose page 1 is a single full-page image), optional doc_id / output path.
Output: detect_quad(image) -> (4x2 corners TL,TR,BR,BL in image px, info) or (None, info);
        warp(image, quad, width, aspect) -> flat page; correct(image) -> (flat image, info);
        analyze(path, doc_id, out_path=None) -> list[Finding] (all Tier 4, advisory; nothing here can be hard):
          perspective_corrected  info   the page was found and warped (evidence: corners, tilt, area share, out_path)
          perspective_not_needed info   already a flat full-page scan
          document_not_found     soft   no page-shaped region (applicant-fixable: photograph the whole page on a plain background)
          partial_document       soft   a corner of the page lies at/over the photo edge (applicant-fixable: page is cut off)
          perspective_unreadable info   the file could not be read
Method: downscale, blur, Canny + closing, external contours, largest convex 4-corner polygon covering >= min_area_ratio
(approxPolyDP at several tolerances), validated by rectangularity and corner angles; fallback: Otsu "bright paper" mask and the
minimum-area rectangle of its largest blob. The client portal uses the same logic with draggable handles (the corners are
returned so a person can correct them); this server copy re-checks uploads.
Does NOT: unroll curved/folded pages, remove shadows, handle several pages in one photo, or fix a page that is cut off.
CLI   : python -m modules.perspective FILE [--out corrected.png]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml

from contracts import Finding

MODULE = "perspective"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "perspective.yaml"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_image(path: str | Path) -> np.ndarray:
    p = Path(path)
    if p.suffix.lower() in _IMG_EXT:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("unreadable image")
        return img
    import pymupdf
    with pymupdf.open(str(p)) as d:
        if d.page_count == 0:
            raise ValueError("PDF has no pages")
        pg = d[0]
        infos = pg.get_image_info(xrefs=True)
        if len(infos) == 1 and infos[0].get("xref"):
            pm = pymupdf.Pixmap(d, infos[0]["xref"])
            if pm.n >= 4:
                pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
            a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
            return cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pm.n == 3 else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
        pm = pg.get_pixmap(dpi=150, alpha=False)
        a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pm.n == 3 else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)


def order_corners(pts: np.ndarray) -> np.ndarray:
    """4 points -> top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def _angles(q: np.ndarray) -> list[float]:
    out = []
    for i in range(4):
        a, b, c = q[(i - 1) % 4], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cosv = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return out


def _valid(quad: np.ndarray, contour_area: float, shape: tuple, cfg: dict) -> Optional[dict]:
    h, w = shape[:2]
    qa = abs(cv2.contourArea(quad.astype(np.float32)))
    if qa < cfg["min_area_ratio"] * w * h or not cv2.isContourConvex(quad.astype(np.float32).reshape(-1, 1, 2).astype(np.int32)):
        return None
    rect = contour_area / qa if qa > 0 else 0
    ang = _angles(quad)
    if rect < cfg["min_rectangularity"] or max(abs(a - 90) for a in ang) > cfg["max_corner_angle_dev"]:
        return None
    return {"area_ratio": round(qa / (w * h), 3), "rectangularity": round(rect, 3), "corner_angles": [round(a, 1) for a in ang]}


def detect_quad(img: np.ndarray, cfg: Optional[dict] = None) -> tuple[Optional[np.ndarray], dict]:
    """Corners (TL,TR,BR,BL in the original image's pixels) of the page, or None."""
    cfg = cfg or load_config()
    h, w = img.shape[:2]
    s = min(1.0, cfg["detect_max_side"] / max(h, w))
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (0, 0), cfg["blur_sigma"])
    cands: list[tuple[np.ndarray, float, str, np.ndarray]] = []
    # edges from brightness AND colour (Lab a/b): a dark page header on a dark table has no brightness edge but a colour one
    lab = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2LAB), (0, 0), cfg["blur_sigma"])
    e_all = cv2.Canny(gray, *cfg["canny"])
    for ch in (1, 2):
        e_all |= cv2.Canny(lab[..., ch], *cfg["canny_color"])
    edges = cv2.morphologyEx(e_all, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    masks = [("edges", edges)]
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    k = max(15, int(cfg["band_close_frac"] * max(gray.shape)))
    pad = k + 2
    banded = cv2.morphologyEx(cv2.copyMakeBorder(bright, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255),
                              cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))[pad:-pad, pad:-pad]
    masks += [("bright", cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))),
              ("bright-banded", banded),
              ("dark", cv2.morphologyEx(255 - bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8)))]
    for name, m in masks:
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL if name != "edges" else cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        for cnt in sorted(cs, key=cv2.contourArea, reverse=True)[:6]:
            per = cv2.arcLength(cnt, True)
            area = cv2.contourArea(cnt)
            if area < cfg["min_area_ratio"] * small.shape[0] * small.shape[1] * 0.6:
                continue
            hull = cv2.convexHull(cnt)
            for eps in cfg["approx_epsilon"]:
                ap = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
                if len(ap) == 4:
                    q = order_corners(ap.reshape(4, 2))
                    v = _valid(q, cv2.contourArea(hull), small.shape, cfg)
                    if v:
                        cands.append((q, v["area_ratio"] * v["rectangularity"], name, cnt))
                        break
            else:
                rect = cv2.minAreaRect(hull)
                q = order_corners(cv2.boxPoints(rect))
                v = _valid(q, cv2.contourArea(hull), small.shape, cfg)
                if v:
                    cands.append((q, v["area_ratio"] * v["rectangularity"] * 0.9, name + "-rect", cnt))
    if not cands:
        return None, {"reason": "no page-shaped region"}
    # a candidate that is the whole photo frame only wins when nothing page-shaped exists inside it (a flat full-page scan) and the
    # photo really looks like paper (mostly bright): noise or a plain wall is not a page
    def paper_like(q_):
        m = np.zeros(gray.shape, np.uint8)
        cv2.fillConvexPoly(m, np.round(q_).astype(np.int32), 255)
        inside = gray[m > 0]
        return inside.size > 0 and float(np.median(inside)) >= cfg["page_min_gray"]
    # the band-closed mask is only evidence for "the page fills the frame" (flat scan); as a page outline inside a photo it is too loose
    cands = [c_ for c_ in cands if paper_like(c_[0]) and not (c_[2].startswith("bright-banded") and
             abs(cv2.contourArea(c_[0].astype(np.float32))) < cfg["frame_area_ratio"] * small.shape[0] * small.shape[1])]
    if not cands:
        return None, {"reason": "no page-shaped region that looks like paper"}
    inner = [c_ for c_ in cands if abs(cv2.contourArea(c_[0].astype(np.float32))) < cfg["frame_area_ratio"] * small.shape[0] * small.shape[1]]
    if not inner and float((gray > cfg["paper_gray"]).mean()) < cfg["frame_min_bright_share"]:
        return None, {"reason": "no page-shaped region (the frame is not paper-like)"}
    q, score, how, hull = max(inner or cands, key=lambda c: c[1])
    v = _valid(q, cv2.contourArea(q.astype(np.float32)), small.shape, cfg) or {}
    q = refine_quad(q, hull.reshape(-1, 2).astype(np.float32), cfg)
    sup = side_support(q, lab, cfg["support_offset_px"], cfg["support_min_delta"])
    return (q / s).astype(np.float32), {"method": how, "score": round(float(score), 3), "side_support": [round(x, 2) for x in sup], **v}


def _intersect(l1, l2) -> Optional[np.ndarray]:
    (vx1, vy1, x1, y1), (vx2, vy2, x2, y2) = l1, l2
    d = vx1 * vy2 - vy1 * vx2
    if abs(d) < 1e-6:
        return None
    t = ((x2 - x1) * vy2 - (y2 - y1) * vx2) / d
    return np.array([x1 + t * vx1, y1 + t * vy1], np.float32)


def refine_quad(q: np.ndarray, pts: np.ndarray, cfg: dict) -> np.ndarray:
    """Sub-pixel corners: fit a straight line to the contour points along each side (robust Huber fit, the 12% nearest each
    corner left out) and intersect neighbouring lines. Falls back to the polygon corners when a fit is poor or moves a corner far."""
    diag = float(np.hypot(*(q[2] - q[0])))
    lines = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        ab = b - a
        L2 = float(ab @ ab)
        t = ((pts - a) @ ab) / max(L2, 1e-9)
        dist = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / max(np.sqrt(L2), 1e-9)
        sel = pts[(t > 0.12) & (t < 0.88) & (dist < 0.03 * diag)]
        if len(sel) < 8:
            return q
        lines.append(cv2.fitLine(sel.reshape(-1, 1, 2), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel())
    out = []
    for i in range(4):
        x = _intersect(lines[(i - 1) % 4], lines[i])
        if x is None or np.linalg.norm(x - q[i]) > cfg.get("refine_max_move", 0.04) * diag:
            return q
        out.append(x)
    return np.array(out, np.float32)


def side_support(q: np.ndarray, lab: np.ndarray, offset: int, min_delta: float) -> list[float]:
    """Share of each side of the quadrilateral across which the colour really changes: for points along the side, the Lab
    distance between a point `offset` px inside and one `offset` px outside must be >= min_delta. A side the detector had to
    interpolate (a dark header the same colour as the dark table) scores low; background texture cannot fake it because inside
    and outside are compared, not edge pixels counted."""
    h, w = lab.shape[:2]
    c = q.mean(axis=0)
    out = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = b - a
        n_ = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-9)
        if np.dot(n_, c - (a + b) / 2) < 0:
            n_ = -n_                                           # unit normal pointing into the page
        t = np.linspace(0.08, 0.92, max(8, int(np.linalg.norm(d) / 3)))
        pts = a[None, :] + t[:, None] * d[None, :]
        ins = np.clip(np.round(pts + offset * n_).astype(int), [0, 0], [w - 1, h - 1])
        outp = np.clip(np.round(pts - offset * n_).astype(int), [0, 0], [w - 1, h - 1])
        delta = np.linalg.norm(lab[ins[:, 1], ins[:, 0]].astype(np.float32) - lab[outp[:, 1], outp[:, 0]].astype(np.float32), axis=1)
        out.append(float((delta >= min_delta).mean()))
    return out


def tilt_deg(quad: np.ndarray) -> float:
    """Angle of the top edge against the horizontal, degrees (0 = level)."""
    d = quad[1] - quad[0]
    return math.degrees(math.atan2(d[1], d[0]))


def warp(img: np.ndarray, quad: np.ndarray, width: int = 1240, aspect: Optional[float] = None) -> np.ndarray:
    """Flat rectangle from the four corners. aspect = height / width of the output (None: measured from the edges)."""
    q = order_corners(quad)
    wm = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    hm = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    asp = aspect or (hm / max(wm, 1e-6))
    out_w, out_h = int(width), int(round(width * asp))
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], np.float32)
    return cv2.warpPerspective(img, cv2.getPerspectiveTransform(q, dst), (out_w, out_h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def correct(img: np.ndarray, cfg: Optional[dict] = None) -> tuple[Optional[np.ndarray], dict]:
    """(flat page, info). info.status: corrected | not_needed | not_found | partial."""
    cfg = cfg or load_config()
    h, w = img.shape[:2]
    q, info = detect_quad(img, cfg)
    if q is None:
        return None, {**info, "status": "not_found"}
    info["corners"] = [[round(float(x), 1), round(float(y), 1)] for x, y in q]
    info["tilt_deg"] = round(tilt_deg(q), 2)
    m = cfg["partial_margin_px"]
    # an upright, undistorted rectangle that fills the image is a flat full-page scan: leave it alone
    ang_dev = max(abs(a - 90) for a in _angles(q))
    side_tilt = max(abs(math.degrees(math.atan2(q[3][0] - q[0][0], q[3][1] - q[0][1]))), abs(math.degrees(math.atan2(q[2][0] - q[1][0], q[2][1] - q[1][1]))))
    if (info.get("area_ratio", 0) >= cfg["not_needed_min_area"] and abs(info["tilt_deg"]) <= cfg["not_needed_max_tilt_deg"]
            and side_tilt <= cfg["not_needed_max_tilt_deg"] and ang_dev <= cfg["not_needed_max_angle_dev"]):
        return img, {**info, "status": "not_needed"}
    if any(x <= m or y <= m or x >= w - 1 - m or y >= h - 1 - m for x, y in q):
        return warp(img, q, cfg["output_width"], cfg["target_aspect"]), {**info, "status": "partial"}
    if min(info.get("side_support", [1.0])) < cfg["min_side_support"]:
        return warp(img, q, cfg["output_width"], cfg["target_aspect"]), {**info, "status": "partial", "edge_unclear": True}
    wq = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    hq = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    info["measured_aspect"] = round(float(hq / max(wq, 1e-6)), 3)
    if cfg.get("target_aspect") and abs(info["measured_aspect"] / cfg["target_aspect"] - 1) > cfg["aspect_tol"]:
        # the proportions are not those of the page: an edge was probably missed (e.g. a dark header on a dark table)
        return warp(img, q, cfg["output_width"], cfg["target_aspect"]), {**info, "status": "partial", "aspect_off": True}
    return warp(img, q, cfg["output_width"], cfg["target_aspect"]), {**info, "status": "corrected"}


def analyze(path: str | Path, doc_id: str = "", cfg: Optional[dict] = None, out_path: Optional[str | Path] = None) -> list[Finding]:
    cfg = cfg or load_config()
    try:
        img = load_image(path)
        flat, info = correct(img, cfg)
    except Exception as exc:
        return [Finding("perspective_unreadable", MODULE, "info", 4, 0.0, "Could not check the page shape because the file could not be read.",
                        doc_id, None, {"error": f"{type(exc).__name__}: {exc}"})]
    st = info["status"]
    if st == "not_found":
        return [Finding("document_not_found", MODULE, "soft", 4, 0.5,
                        "We could not find the edges of the page. Please photograph the whole page flat, on a plain background, with good light.",
                        doc_id, None, info)]
    ev = {k: v for k, v in info.items() if k != "status"}
    if st == "not_needed":
        return [Finding("perspective_not_needed", MODULE, "info", 4, 0.0, "The page is already flat and upright.", doc_id, None, ev)]
    if out_path is not None and flat is not None:
        cv2.imwrite(str(out_path), flat); ev["out_path"] = str(out_path)
    if st == "partial":
        return [Finding("partial_document", MODULE, "soft", 4, 0.5,
                        "We could not see all four corners of the page clearly (part of it may be cut off, or an edge blends into the background). Please retake the photo with the whole page in view on a plain, contrasting background.",
                        doc_id, None, ev)]
    return [Finding("perspective_corrected", MODULE, "info", 4, 0.0,
                    "The page was straightened.", doc_id, None, ev)]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.perspective")
    ap.add_argument("file"); ap.add_argument("--out"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    fs = analyze(a.file, Path(a.file).stem, load_config(a.config), a.out)
    json.dump([f.to_json() for f in fs], sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
