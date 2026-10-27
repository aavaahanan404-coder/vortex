"""Version comparison of a re-uploaded document (v1 -> v2): what changed, and which pages did not.

Input : two PDF/image paths (older, newer).
Output: compare(old, new) -> report dict; analyze(old, new, doc_id) -> list[Finding] (info only):
          diff_summary    evidence = {pages_old, pages_new, unchanged_pages, changed_pages, text_changes,
                          visual_regions}; text change = {page, op: replace|insert|delete, old, new, old_bbox,
                          new_bbox}; visual region = {page, bbox (x,y,w,h in the NEWER page's points)}
          diff_identical  every compared page has no text and no visual change (nothing to re-audit)
          diff_unreadable one of the files could not be read
        render_overlay(old, new, page, out_png) writes the reviewer's side-by-side image: ink only in the
        older version red, ink only in the newer green, common ink grey.
Why info: a re-upload legitimately changes things; the module's job is to stop reviewers re-auditing
unchanged pages and to point at the edits, not to judge them (use qr_verify / pdf_forensics for that).
Text diff is word-level on the text layer in reading order (faint watermark text ignored); the visual diff
is pixel-based on a render, so it also covers scans and pasted images.
Does NOT: align pages that were reordered (page n is compared with page n), compare more than max_pages,
detect hidden text under a white patch (that is pdf_forensics; a covered original stays in both text layers),
tell a legitimate correction from a forgery.
CLI   : python -m modules.diff OLD NEW [--overlay-dir DIR]
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml

from contracts import Finding

MODULE = "diff"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "diff.yaml"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ----------------------------------------------------------------------------- page access
class _Doc:
    """Uniform page access for PDFs and single images."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.img = None
        self.pdf = None
        if self.path.suffix.lower() in _IMG_EXT:
            self.img = cv2.imread(str(self.path), cv2.IMREAD_GRAYSCALE)
            if self.img is None:
                raise ValueError(f"unreadable image: {self.path.name}")
            self.n = 1
        else:
            import pymupdf
            self.pdf = pymupdf.open(str(self.path))
            if self.pdf.needs_pass:
                raise ValueError("encrypted PDF")
            self.n = self.pdf.page_count

    def close(self):
        if self.pdf is not None:
            self.pdf.close()

    def size_pt(self, i: int) -> tuple:
        if self.pdf is not None:
            r = self.pdf[i].rect
            return (r.width, r.height)
        return (float(self.img.shape[1]), float(self.img.shape[0]))

    def gray(self, i: int, dpi: int) -> np.ndarray:
        if self.pdf is None:
            return self.img
        import pymupdf
        z = dpi / 72.0
        pm = self.pdf[i].get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
        return np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width).copy()

    def words(self, i: int, min_opacity: float) -> list[tuple]:
        """[(text, (x0,y0,x1,y1))] in reading order from the text layer (empty for scans)."""
        if self.pdf is None:
            return []
        out = []
        for sp in self.pdf[i].get_texttrace():
            if sp.get("type") != 0 or (sp.get("opacity") or 1.0) < min_opacity:
                continue
            if tuple(round(v, 2) for v in sp["dir"]) != (1.0, 0.0):
                continue                                   # rotated text (watermarks) is not part of the content
            cur, box = "", None
            for ch in list(sp["chars"]) + [(32, 0, None, None)]:
                if ch[0] and chr(ch[0]).strip():
                    cur += chr(ch[0])
                    b = ch[3]
                    box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
                elif cur:
                    out.append((cur, tuple(box))); cur, box = "", None
        out.sort(key=lambda w: (round((w[1][1] + w[1][3]) / 2 / 4), w[1][0]))   # line (4 pt bands), then x
        return out


def _union(boxes: list[tuple]) -> Optional[tuple]:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _xywh(b: Optional[tuple]) -> Optional[list]:
    return None if b is None else [round(b[0], 2), round(b[1], 2), round(b[2] - b[0], 2), round(b[3] - b[1], 2)]


# ----------------------------------------------------------------------------- text and visual diffs
def text_changes(old_words: list[tuple], new_words: list[tuple], page: int) -> list[dict]:
    sm = difflib.SequenceMatcher(None, [w[0] for w in old_words], [w[0] for w in new_words], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        out.append({"page": page, "op": {"replace": "replace", "delete": "delete", "insert": "insert"}[op],
                    "old": " ".join(w[0] for w in old_words[i1:i2]), "new": " ".join(w[0] for w in new_words[j1:j2]),
                    "old_bbox": _xywh(_union([w[1] for w in old_words[i1:i2]])),
                    "new_bbox": _xywh(_union([w[1] for w in new_words[j1:j2]]))})
    return out


def _aligned(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return a, b


def visual_regions(old_g: np.ndarray, new_g: np.ndarray, size_pt: tuple, cfg: dict) -> list[list]:
    """Regions (x,y,w,h in points of the newer page) where the two renders differ."""
    a, b = _aligned(old_g, new_g)
    diff = (cv2.absdiff(a, b) > cfg["visual_threshold"]).astype(np.uint8) * 255
    k = cfg["visual_close_px"]
    diff = cv2.morphologyEx(diff, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _, st, _ = cv2.connectedComponentsWithStats(diff)
    sx, sy = size_pt[0] / a.shape[1], size_pt[1] / a.shape[0]
    regs = [[round(st[i][0] * sx, 2), round(st[i][1] * sy, 2), round(st[i][2] * sx, 2), round(st[i][3] * sy, 2)]
            for i in range(1, n) if st[i][4] >= cfg["visual_min_area_px"]]
    return sorted(regs, key=lambda r: (r[1], r[0]))


def compare(old_path: str | Path, new_path: str | Path, cfg: Optional[dict] = None) -> dict:
    """Full comparison report (raises on unreadable input; analyze() wraps it)."""
    cfg = cfg or load_config()
    o, n = _Doc(old_path), _Doc(new_path)
    try:
        k = min(o.n, n.n, cfg["max_pages"])
        unchanged, changed, tchanges, regions = [], [], [], []
        for i in range(k):
            tc = text_changes(o.words(i, cfg["text_min_opacity"]), n.words(i, cfg["text_min_opacity"]), i + 1)
            vr = visual_regions(o.gray(i, cfg["render_dpi"]), n.gray(i, cfg["render_dpi"]), n.size_pt(i), cfg)
            if tc or vr:
                changed.append(i + 1)
                tchanges += tc
                regions += [{"page": i + 1, "bbox": r} for r in vr]
            else:
                unchanged.append(i + 1)
        lim = cfg["max_changes_listed"]
        return {"pages_old": o.n, "pages_new": n.n, "compared_pages": k, "unchanged_pages": unchanged,
                "changed_pages": changed, "pages_added": list(range(o.n + 1, n.n + 1)) if n.n > o.n else [],
                "pages_removed": list(range(n.n + 1, o.n + 1)) if o.n > n.n else [],
                "n_text_changes": len(tchanges), "text_changes": tchanges[:lim],
                "n_visual_regions": len(regions), "visual_regions": regions[:lim]}
    finally:
        o.close(); n.close()


def analyze(old_path: str | Path, new_path: str | Path, doc_id: str = "", cfg: Optional[dict] = None) -> list[Finding]:
    cfg = cfg or load_config()
    try:
        rep = compare(old_path, new_path, cfg)
    except Exception as exc:
        return [Finding("diff_unreadable", MODULE, "info", 3, 0.0,
                        "Could not compare the two versions because a file could not be read.", doc_id, None,
                        {"error": f"{type(exc).__name__}: {exc}"})]
    if not rep["changed_pages"] and not rep["pages_added"] and not rep["pages_removed"]:
        return [Finding("diff_identical", MODULE, "info", 3, 0.0,
                        "The new version shows no change from the previous one; there is nothing new to audit.",
                        doc_id, None, rep)]
    first = rep["visual_regions"][0] if rep["visual_regions"] else None
    bbox = (first["page"], *first["bbox"]) if first else None
    msg = (f"{len(rep['changed_pages'])} of {rep['compared_pages']} compared page(s) changed "
           f"({rep['n_text_changes']} text change(s)); pages {rep['unchanged_pages'] or 'none'} are unchanged and need no re-audit.")
    return [Finding("diff_summary", MODULE, "info", 3, 0.0, msg, doc_id, bbox, rep)]


def render_overlay(old_path: str | Path, new_path: str | Path, page: int, out_png: str | Path,
                   cfg: Optional[dict] = None) -> Path:
    """Reviewer image for 1-based `page`: red = only in the older version, green = only in the newer."""
    cfg = cfg or load_config()
    o, n = _Doc(old_path), _Doc(new_path)
    try:
        a, b = _aligned(o.gray(page - 1, cfg["render_dpi"]), n.gray(page - 1, cfg["render_dpi"]))
    finally:
        o.close(); n.close()
    ink_a, ink_b = a < 160, b < 160
    img = np.full(a.shape + (3,), 255, np.uint8)
    img[ink_a & ink_b] = (150, 150, 150)
    img[ink_a & ~ink_b] = (0, 0, 220)           # BGR red: removed
    img[~ink_a & ink_b] = (0, 170, 0)           # BGR green: added
    cv2.imwrite(str(out_png), img)
    return Path(out_png)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.diff")
    ap.add_argument("old"); ap.add_argument("new"); ap.add_argument("--overlay-dir"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    fs = analyze(a.old, a.new, Path(a.new).stem, cfg)
    if a.overlay_dir and fs[0].id == "diff_summary":
        Path(a.overlay_dir).mkdir(parents=True, exist_ok=True)
        for p in fs[0].evidence["changed_pages"]:
            render_overlay(a.old, a.new, p, Path(a.overlay_dir) / f"page{p}_diff.png", cfg)
    json.dump([f.to_json() for f in fs], sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
