"""Field extraction from a document page: text layer (digital) or Tesseract OCR (scans), driven by templates.

Input : PDF/image path, doc_type, the template dict (config/templates.yaml, built by templates_build).
Output: extract() -> contracts.ExtractedDoc: fields {key: {value, bbox [x,y,w,h pt], page, conf, source, flags?, alt_values?}}
        source = "text_layer" | "ocr"; conf in 0..1. analyze() -> list[Finding]:
          low_ocr_confidence  soft T4 p~0.3  an OCR field is below low_conf or does not fit its value pattern
                              (routes to the ambiguity queue; needs_human in scoring.yaml)
          extract_ok / ocr_unavailable / extract_unreadable / extract_no_template (info)
How: words with boxes (draw order from the text layer, or OCR on the 2x upscaled scan) -> lines -> for each template
scalar the label is matched at the start of a line and the value is what lies right of it (positional fallback when the
label is not found, conf capped); table templates give one row per line with words assigned to columns by x.
Covered text: when two words are drawn on top of each other in one value (a white patch with new text over the original) the
LAST drawn is kept as the visible value, conf drops to conf_conflict, flags=["covered_text"] and the other text is in
alt_values - pdf_forensics reports the forgery itself, this only stops a downstream rule trusting the number.
Value hygiene: digit/letter confusions fixed inside numeric-shaped fields only; a value that does not fit its pattern keeps its
text, gets flags=["shape_mismatch"] and a lower conf; enum fields are repaired to the closest allowed value.
Does NOT: read QR/seal/signature, handle rotated or heavily skewed scans (see quality / perspective), tables whose layout
differs from the template, handwriting, non-English text (only the `eng` Tesseract data is installed).
CLI   : python -m modules.extract FILE --doc-type T [--templates t.yaml] [--force-ocr]
"""
from __future__ import annotations

import argparse
import difflib
import functools
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml

from contracts import ExtractedDoc, Finding

MODULE = "extract"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "extract.yaml"
DEFAULT_TEMPLATES = Path(__file__).resolve().parent.parent / "config" / "templates.yaml"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_WIN_TESS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@functools.lru_cache(maxsize=4)
def _load_templates(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_templates(path: Optional[str | Path] = None) -> dict:
    return _load_templates(str(path or DEFAULT_TEMPLATES))


# ----------------------------------------------------------------------------- OCR
def _tesseract(cfg: dict):
    """pytesseract module with the binary configured, or None if OCR cannot run."""
    try:
        import pytesseract
    except ImportError:
        return None
    cmd = cfg.get("tesseract_cmd") or shutil.which("tesseract") or next((p for p in _WIN_TESS if Path(p).exists()), None)
    if not cmd:
        return None
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return None
    return pytesseract


def ocr_available(cfg: Optional[dict] = None) -> bool:
    return _tesseract(cfg or load_config()) is not None


# ----------------------------------------------------------------------------- words
def _page_gray(doc, i: int, cfg: dict) -> tuple[np.ndarray, float]:
    """(gray image, pixels per point). A single full-page embedded image is used natively."""
    import pymupdf
    pg = doc[i]
    infos = pg.get_image_info(xrefs=True)
    if len(infos) == 1 and infos[0].get("xref"):
        r = pymupdf.Rect(infos[0]["bbox"]) & pg.rect
        if r.get_area() >= 0.9 * pg.rect.get_area():
            pm = pymupdf.Pixmap(doc, infos[0]["xref"])
            if pm.n >= 4:
                pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
            a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
            return (cv2.cvtColor(a, cv2.COLOR_RGB2GRAY) if pm.n == 3 else a[..., 0].copy()), pm.width / pg.rect.width
    z = cfg["ocr_render_dpi"] / 72.0
    pm = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width).copy(), z


def remove_rules(gray: np.ndarray, min_len_frac: float) -> np.ndarray:
    """Erase long horizontal / vertical ruling lines (table borders) that make Tesseract skip or merge cells."""
    ink = (gray < 0.6 * float(np.percentile(gray, 95))).astype(np.uint8)
    h, w = gray.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, int(w * min_len_frac)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, int(h * min_len_frac))))
    rules = cv2.dilate(cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk) | cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk),
                       np.ones((3, 3), np.uint8))
    out = gray.copy()
    out[rules > 0] = int(np.percentile(gray, 95))
    return out


def ocr_words(gray: np.ndarray, ppp: float, cfg: dict, tess) -> list[dict]:
    if cfg.get("ocr_rule_frac"):
        gray = remove_rules(gray, cfg["ocr_rule_frac"])
    up = cfg["ocr_upscale"]
    g = cv2.resize(gray, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC) if up != 1 else gray
    d = tess.image_to_data(g, config=f"--psm {cfg['ocr_psm']}", output_type=tess.Output.DICT)
    s = 1.0 / (up * ppp)
    out = []
    for i, t in enumerate(d["text"]):
        if not t.strip() or float(d["conf"][i]) < 0 or re.fullmatch(r"[|_\\~`\[\]{}<>=^*.:;'\"]+", t.strip()):
            continue                                           # empty or border-junk token (leftover table borders); '-', '&', '/' are content
        x0, y0 = d["left"][i] * s, d["top"][i] * s
        out.append({"text": t.strip(), "bbox": (x0, y0, x0 + d["width"][i] * s, y0 + d["height"][i] * s),
                    "conf": float(d["conf"][i]) / 100.0, "seq": i})
    return out


def text_words(page) -> list[dict]:
    """Horizontal, non-faint words of the text layer with their draw order."""
    out = []
    for sp in page.get_texttrace():
        if sp.get("type") != 0 or (sp.get("opacity") or 1.0) < 0.5:
            continue
        if tuple(round(v, 2) for v in sp["dir"]) != (1.0, 0.0):
            continue
        cur, box = "", None
        for ch in list(sp["chars"]) + [(32, 0, None, None)]:
            if ch[0] and chr(ch[0]).strip():
                cur += chr(ch[0]); b = ch[3]
                box = tuple(b) if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
            elif cur:
                out.append({"text": cur, "bbox": box, "conf": 1.0, "seq": sp["seqno"]}); cur, box = "", None
    return out


def group_lines(words: list[dict], tol: float) -> list[list[dict]]:
    """Cluster words into lines by vertical centre (running mean of the line), each line sorted left to right."""
    lines: list[list[dict]] = []
    cys: list[float] = []
    for w in sorted(words, key=lambda w: (w["bbox"][1] + w["bbox"][3]) / 2):
        cy = (w["bbox"][1] + w["bbox"][3]) / 2
        if lines and abs(cy - cys[-1]) <= tol:
            lines[-1].append(w); cys[-1] = sum((u["bbox"][1] + u["bbox"][3]) / 2 for u in lines[-1]) / len(lines[-1])
        else:
            lines.append([w]); cys.append(cy)
    return [sorted([{**w, "_cy": c} for w in l], key=lambda w: w["bbox"][0]) for l, c in zip(lines, cys)]


# ----------------------------------------------------------------------------- helpers
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _union(ws: list[dict]) -> list[float]:
    x0 = min(w["bbox"][0] for w in ws); y0 = min(w["bbox"][1] for w in ws)
    x1 = max(w["bbox"][2] for w in ws); y1 = max(w["bbox"][3] for w in ws)
    return [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]


def _overlap_x(a, b) -> float:
    ix = min(a[2], b[2]) - max(a[0], b[0])
    return ix / max(1e-6, min(a[2] - a[0], b[2] - b[0]))


def _resolve_covered(ws: list[dict]) -> tuple[list[dict], list[str]]:
    """Words drawn on top of each other: keep the last drawn, return the others' texts."""
    keep, alts = [], []
    for w in sorted(ws, key=lambda w: -w["seq"]):
        clash = [k for k in keep if _overlap_x(k["bbox"], w["bbox"]) >= 0.5 and abs(k["bbox"][1] - w["bbox"][1]) < 3]
        if clash and w["seq"] != clash[0]["seq"]:
            alts.append(w["text"])
        else:
            keep.append(w)
    return sorted(keep, key=lambda w: w["bbox"][0]), alts


def _fix_digits(v: str, cfg: dict) -> str:
    fx = cfg["digit_fix"]
    body = re.sub(r"^(Rs\.?\s*)", "", v)
    pre = v[:len(v) - len(body)]
    cand = "".join(fx.get(ch, ch) for ch in body)
    return pre + cand if re.fullmatch(r"[\d.,/\-\s]+", cand) else v


def _finish(raw: str, t: dict, conf: float, flags: list, cfg: dict, ocr: bool = True) -> tuple[str, float, list]:
    v = raw.strip()
    if t.get("numeric"):
        v = _fix_digits(v, cfg)
    if t.get("enum") and v not in t["enum"]:
        m = difflib.get_close_matches(v, t["enum"], n=1, cutoff=0.8) if ocr else []
        if m:
            v, conf = m[0], conf * cfg["conf_enum_repair"]; flags = flags + ["enum_repaired"]
        elif ocr:
            conf *= cfg["conf_shape_mismatch"]; flags = flags + ["shape_mismatch"]
        else:                                       # exact text layer: a value outside the training set is new, not wrong
            flags = flags + ["unseen_value"]
    elif t.get("pattern") and not re.fullmatch(t["pattern"], v):
        conf *= cfg["conf_shape_mismatch"]; flags = flags + ["shape_mismatch"]
    return v, conf, flags


def _right_bounds(tpl: dict, page_w: float) -> dict[str, float]:
    """x limit of each scalar's value: the next field on the same row."""
    sc = tpl["scalars"]
    out = {}
    for k, t in sc.items():
        nxt = [u["bbox"][0] for j, u in sc.items() if j != k and u["page"] == t["page"]
               and abs(u["bbox"][1] - t["bbox"][1]) < 3 and u["bbox"][0] > t["bbox"][0] + 1]
        out[k] = min(nxt) - 2 if nxt else page_w
    return out


# ----------------------------------------------------------------------------- scalars and tables
def _scalar(key: str, t: dict, lines_by_page: dict, tol: dict, rb: float, cfg: dict) -> Optional[dict]:
    pg = t["page"]
    lines = lines_by_page.get(pg, [])
    labs = [_norm(l) for l in (t.get("labels") or ([t["label"]] if t.get("label") else []))]
    lab = labs[0] if labs else None
    value_ws, located, flags = None, "label", []
    if lab:
        best = (0.0, None, 0)
        for li, line in enumerate(lines):
            for k in range(1, min(len(line), 8) + 1):
                if line[k - 1]["bbox"][2] > t["value_x"] + tol["x"]:
                    break
                head = "".join(_norm(w["text"]) for w in line[:k])
                r = max(difflib.SequenceMatcher(None, head, lb).ratio() for lb in labs)
                if r > best[0]:
                    best = (r, li, k)
        if best[1] is not None and best[0] >= cfg["label_ratio"]:
            line, k = lines[best[1]], best[2]
            value_ws = [w for w in line[k:] if w["bbox"][0] >= t["value_x"] - tol["x"] and w["bbox"][0] < rb]
    if not value_ws and lab and (t.get("optional") or t.get("y_variable")):
        return None                                            # an absent optional field must not be conjured from a position
    if not value_ws:                                           # positional fallback / unlabelled field
        x, y, w_, h_ = t["bbox"]
        value_ws = [w for line in lines for w in line
                    if abs((w["bbox"][1] + w["bbox"][3]) / 2 - (y + h_ / 2)) <= tol["y"]
                    and w["bbox"][0] >= x - tol["x"] and w["bbox"][0] < min(rb, x + max(w_ * 1.6, 40) + tol["x"])]
        located = "position"
    if not value_ws:
        return None
    conf = min(w["conf"] for w in value_ws) if value_ws[0].get("src") != "text" else 1.0
    alts: list[str] = []
    if any(w.get("src") == "text" for w in value_ws):
        value_ws, alts = _resolve_covered(value_ws)
        if alts:
            conf, flags = cfg["conf_conflict"], flags + ["covered_text"]
    if located == "position" and lab:
        conf = min(conf, cfg["conf_position_fallback"]); flags = flags + ["located_by_position"]
    v, conf, flags = _finish(" ".join(w["text"] for w in value_ws), t, conf, flags, cfg,
                             ocr=value_ws[0].get("src") != "text")
    out = {"value": v, "bbox": _union(value_ws), "page": pg, "conf": round(conf, 3), "source": value_ws[0].get("src_name")}
    if flags:
        out["flags"] = flags
    if alts:
        out["alt_values"] = alts
    return out


def _table(name: str, tb: dict, tpl: dict, lines_by_page: dict, raster: bool, cfg: dict) -> dict:
    """Rows of a table template. Rows are consecutive lines on the row grid; the first line that is neither the next
    row nor the wrapped continuation of the previous one (or that starts like a scalar label, e.g. a total below the
    table) ends the table on that page."""
    cols = sorted(tb["columns"].items(), key=lambda kv: kv[1])
    tol = cfg["tol_raster"] if raster else cfg["tol_digital"]
    ctol = cfg["col_tol_raster"] if raster else cfg["col_tol_digital"]
    pitch = tb.get("row_pitch") or 2 * tb["row_h"]
    stops = [_norm(l) for t in tpl["scalars"].values() for l in (t.get("labels") or [t["label"]] if t.get("label") else [])]
    first = cols[0][0]
    merged: list[tuple[int, dict, float]] = []
    for pg in sorted(int(p) for p in tb["page_y0"]):
        y0, y1 = tb["page_y0"][pg] - tol["y"], tb["y_max"][pg] + tol["y"] + pitch
        last_cy = None
        for line in lines_by_page.get(pg, []):
            cy = line[0]["_cy"]
            if cy < y0:
                continue
            if cy > y1:
                break
            head = "".join(_norm(w["text"]) for w in line[:4])
            if any(len(sp) >= 6 and difflib.SequenceMatcher(None, head[:len(sp)], sp).ratio() >= cfg["label_ratio"] for sp in stops):
                break
            cells: dict[str, list] = {c: [] for c, _ in cols}
            for w in line:
                cand = [c for c, x in cols if x <= w["bbox"][0] + ctol]
                if cand:
                    cells[cand[-1]].append(w)
            if not cells[first]:
                continue
            only_first = not any(cells[c] for c, _ in cols[1:])
            if only_first and merged and merged[-1][0] == pg and cy - merged[-1][2] <= 1.35 * tb["row_h"] + tol["y"]:
                merged[-1][1][first] = merged[-1][1][first] + cells[first]       # wrapped first-column text
                merged[-1] = (pg, merged[-1][1], cy)
                continue
            if last_cy is not None and abs(cy - (last_cy + pitch)) > tol["y"] * 1.5 + 0.1 * pitch:
                break                                                            # not the next row on the grid
            merged.append((pg, cells, cy)); last_cy = cy
    out = {}
    for i, (pg, cells, _) in enumerate(merged):
        for c, ws in cells.items():
            if not ws:
                continue
            ws = sorted(ws, key=lambda w: (round(w["_cy"] / 3), w["bbox"][0]))
            out[f"{name}[{i}].{c}"] = {"value": " ".join(w["text"] for w in ws), "bbox": _union(ws), "page": pg,
                                       "conf": round(min(w["conf"] for w in ws), 3), "source": ws[0].get("src_name")}
    return out


# ----------------------------------------------------------------------------- entry points
def extract(path: str | Path, doc_id: str = "", doc_type: Optional[str] = None, templates: Optional[dict] = None,
            cfg: Optional[dict] = None, force_ocr: bool = False) -> tuple[ExtractedDoc, dict]:
    """Returns (ExtractedDoc, info). info = {source, n_pages, ocr_available, error}. Raises on unreadable input."""
    cfg = cfg or load_config()
    templates = templates if templates is not None else load_templates()
    p = Path(path)
    info = {"source": None, "ocr_available": None, "template": doc_type in templates}
    tpl = templates.get(doc_type)
    words_by_page: dict[int, list] = {}
    if p.suffix.lower() in _IMG_EXT:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("unreadable image")
        # an image has no physical size: take the template's page width as the page width (a full-page image is assumed;
        # photos must go through perspective correction first)
        ppp = img.shape[1] / tpl["page_size"][0] if tpl else 1.0
        pages = [(img, ppp)]
        digital = False
    else:
        import pymupdf
        with pymupdf.open(str(p)) as doc:
            if doc.page_count == 0 or doc.needs_pass:
                raise ValueError("empty or encrypted PDF")
            n = min(doc.page_count, cfg["max_pages"])
            tw = {i: text_words(doc[i]) for i in range(n)}
            digital = any(tw.values()) and not force_ocr
            pages = []
            if digital:
                for i in range(n):
                    words_by_page[i + 1] = [{**w, "src": "text", "src_name": "text_layer"} for w in tw[i]]
            else:
                pages = [_page_gray(doc, i, cfg) for i in range(n)]
    tol = cfg["tol_digital"] if digital else cfg["tol_raster"]
    if not digital:
        tess = _tesseract(cfg)
        info["ocr_available"] = tess is not None
        if tess is None:
            info["source"] = "ocr"
            return ExtractedDoc(doc_id, doc_type or "", {}), info
        for i, (g, ppp) in enumerate(pages):
            words_by_page[i + 1] = [{**w, "src": "ocr", "src_name": "ocr"} for w in ocr_words(g, ppp, cfg, tess)]
    info["source"] = "text_layer" if digital else "ocr"
    ltol = cfg["line_tol_pt"] if digital else cfg["line_tol_raster_pt"]
    lines_by_page = {pg: group_lines(ws, ltol) for pg, ws in words_by_page.items()}
    fields: dict = {}
    if tpl:
        rb = _right_bounds(tpl, tpl["page_size"][0])
        for k, t in tpl["scalars"].items():
            if k in cfg["skip_keys"]:
                continue
            f = _scalar(k, t, lines_by_page, tol, rb[k], cfg)
            if f is not None:
                fields[k] = f
        for name, tb in tpl["tables"].items():
            fields.update(_table(name, tb, tpl, lines_by_page, not digital, cfg))
    return ExtractedDoc(doc_id, doc_type or "", fields), info


def analyze(path: str | Path, doc_id: str = "", doc_type: Optional[str] = None, templates: Optional[dict] = None,
            cfg: Optional[dict] = None, force_ocr: bool = False, precomputed: Optional[tuple] = None) -> list[Finding]:
    """Findings of an extraction. precomputed = the (ExtractedDoc, info) pair of an extract() call already made (no second OCR)."""
    cfg = cfg or load_config()
    try:
        doc, info = precomputed if precomputed is not None else extract(path, doc_id, doc_type, templates, cfg, force_ocr)
    except Exception as exc:
        return [Finding("extract_unreadable", MODULE, "info", 4, 0.0,
                        "Could not read the document to extract its fields.", doc_id, None,
                        {"error": f"{type(exc).__name__}: {exc}"})]
    if info["source"] == "ocr" and info["ocr_available"] is False:
        return [Finding("ocr_unavailable", MODULE, "info", 4, 0.0,
                        "This document is a scan and no OCR engine is installed, so its fields were not read.", doc_id)]
    if not info["template"]:
        return [Finding("extract_no_template", MODULE, "info", 4, 0.0,
                        f"No template for document type '{doc_type}'; no fields were extracted.", doc_id)]
    out: list[Finding] = []
    if info["source"] == "ocr":
        weak = {k: {"conf": f["conf"], "value": f["value"], "flags": f.get("flags", [])} for k, f in doc.fields.items()
                if f["conf"] < cfg["low_conf"]}
        if weak:
            worst = min(weak, key=lambda k: weak[k]["conf"])
            out.append(Finding("low_ocr_confidence", MODULE, "soft", 4, cfg["p_low_ocr"],
                               f"{len(weak)} field(s) could not be read reliably (least certain: '{worst}'); "
                               "a person should check them against the page.", doc_id,
                               (doc.fields[worst]["page"], *doc.fields[worst]["bbox"]), {"fields": weak}))
    if not out:
        out.append(Finding("extract_ok", MODULE, "info", 4, 0.0, f"{len(doc.fields)} field(s) extracted from the {info['source']}.",
                           doc_id, None, {"n_fields": len(doc.fields), "source": info["source"]}))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.extract")
    ap.add_argument("file"); ap.add_argument("--doc-type", required=True); ap.add_argument("--templates")
    ap.add_argument("--config"); ap.add_argument("--force-ocr", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    tpl = load_templates(a.templates)
    doc, info = extract(a.file, Path(a.file).stem, a.doc_type, tpl, cfg, a.force_ocr)
    fs = analyze(a.file, Path(a.file).stem, a.doc_type, tpl, cfg, a.force_ocr)
    json.dump({"extracted": doc.to_json(), "info": info, "findings": [f.to_json() for f in fs]}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
