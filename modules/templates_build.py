"""Document templates: per document type, where each field sits and what its value looks like.

Input : build_templates(samples) - samples are (doc_type, fields, page_size_pt) from TRAIN-split clean
        digital documents (ground-truth field dicts: {key: {value, bbox [x,y,w,h], page, label}}).
        check_layout(extracted_fields, template, source) - an ExtractedDoc.fields dict.
Output: template dict {doc_type: {page_size, scalars: {key: {label, page, bbox, value_x, pattern, numeric,
        enum, optional, support}}, tables: {name: {columns: {col: x0}, page_y0: {page: y}, y_max, row_h, support}}}}.
        check_layout returns list[Finding]:
          layout_shifted       soft T3 p~0.45  a field sits further from its template position than the tolerance
          field_missing        soft T3 p~0.35  a required template field was not found
          fields_overlap       soft T3 p~0.5   two extracted values overlap (text pasted over text)
          field_out_of_bounds  soft T3 p~0.5   a value lies outside the page
          layout_ok (info)
Conventions: keys with an index (`courses[0].course`) become one table `courses` with columns; every other key is a
scalar. A scalar is `optional` when fewer than 90% of the training documents have it (e.g. land_doc seller / lessor).
`pattern` is a regex union of the distinct character-class shapes seen, only for values that contain digits (ids, dates,
numbers; None for names and other free text, whose letter shapes do not generalise); `numeric`
marks values made of digits and punctuation only (the OCR-confusion fixer in extract applies to those); `enum` lists
a small closed set of values.
Does NOT: learn from the held-out corpus (the builder refuses it), handle documents that change layout between
issuers of the same type (one template per type), or validate values (extract reports shape mismatches).
Builder: python -m harness.build_templates   -> config/templates.yaml
"""
from __future__ import annotations

import collections
import re
import statistics
from pathlib import Path
from typing import Iterable, Optional

import yaml

from contracts import Finding

MODULE = "templates_build"
DEFAULT_TEMPLATES = Path(__file__).resolve().parent.parent / "config" / "templates.yaml"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "templates_cfg.yaml"
SKIP_KEYS = {"seal", "signature", "qr"}
_IDX = re.compile(r"^([A-Za-z_]+)\[(\d+)\]\.(.+)$")


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_templates(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_TEMPLATES, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ----------------------------------------------------------------------------- value shapes
def _runs(value: str) -> list[tuple[str, int]]:
    """Character-class runs: D digits, U upper, L lower, other characters literal (class = the char)."""
    out: list[tuple[str, int]] = []
    for ch in value:
        k = "D" if ch.isdigit() else "U" if ch.isupper() else "L" if ch.islower() else ch
        if out and out[-1][0] == k and k in "DUL":
            out[-1] = (k, out[-1][1] + 1)
        else:
            out.append((k, 1))
    return out


def shape_pattern(values: list[str], max_shapes: int) -> Optional[str]:
    """Regex covering the distinct run-class sequences of `values`; None when they are free text."""
    shapes: dict[tuple, list[list[int]]] = {}
    for v in values:
        r = _runs(v)
        key = tuple(k for k, _ in r)
        cur = shapes.setdefault(key, [[n, n] for _, n in r])
        for i, (_, n) in enumerate(r):
            cur[i][0], cur[i][1] = min(cur[i][0], n), max(cur[i][1], n)
    if len(shapes) > max_shapes or not all("D" in key for key in shapes):
        return None                       # free text, or purely alphabetic (names): letter shapes do not generalise
    alts = []
    for key, rng in shapes.items():
        parts = []
        for k, (lo, hi) in zip(key, rng):
            if k == "D":
                parts.append(rf"\d{{{lo},{hi}}}")
            elif k in "UL":                # letter runs: any length (a longer district code is still a code)
                parts.append("[A-Z]+" if k == "U" else "[a-z]+")
            else:
                parts.append(re.escape(k))
        alts.append("".join(parts))
    return "^(?:" + "|".join(sorted(alts)) + ")$"


def is_numeric(values: list[str]) -> bool:
    return bool(values) and all(re.fullmatch(r"[\d.,/\-\s]+|(?:Rs\.?\s*)?[\d,]+", v.strip()) for v in values)


# ----------------------------------------------------------------------------- builder
def _med(xs):
    return round(float(statistics.median(xs)), 2)


def build_templates(samples: Iterable[tuple], cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    by_type: dict[str, list] = collections.defaultdict(list)
    for dt, fields, size in samples:
        by_type[dt].append((fields, size))
    out = {}
    for dt, docs in sorted(by_type.items()):
        n = len(docs)
        scal: dict[str, list] = collections.defaultdict(list)
        tab: dict[str, dict] = collections.defaultdict(lambda: collections.defaultdict(list))
        for fields, _ in docs:
            for k, v in fields.items():
                if k in SKIP_KEYS or v.get("value") in (None, "") and not v.get("bbox"):
                    continue
                m = _IDX.match(k)
                if m:
                    tab[m.group(1)][m.group(3)].append((int(m.group(2)), v))
                else:
                    scal[k].append(v)
        scalars = {}
        for k, vs in scal.items():
            vals = [str(v["value"]) for v in vs if v.get("value") not in (None, "")]
            if not vals or len(vs) < cfg["min_support"]:
                continue
            box = [v["bbox"] for v in vs if v.get("bbox")]
            distinct = sorted(set(vals))
            scalars[k] = {
                "label": next(iter(collections.Counter(v.get("label") for v in vs if v.get("label")).most_common(1)), [None])[0],
                # a field can carry different labels by document variant (Purchaser / Lessee): keep every label seen
                "labels": [l for l, k in collections.Counter(v.get("label") for v in vs if v.get("label")).most_common()
                           if k >= cfg["min_label_support"]],
                "page": int(collections.Counter(v.get("page", 1) for v in vs).most_common(1)[0][0]),
                "bbox": [_med([b[0] for b in box]), _med([b[1] for b in box]), _med([b[2] for b in box]), _med([b[3] for b in box])],
                "value_x": _med([b[0] for b in box]),
                "pattern": shape_pattern(vals, cfg["max_shapes"]),
                "numeric": is_numeric(vals),
                "enum": distinct if (len(distinct) <= cfg["max_enum"] and len(vals) >= cfg["min_enum_docs"]) else None,
                "optional": len(vs) < cfg["optional_below"] * n,
                # a value placed after a table moves with the row count: its y is not a fixed position
                "y_variable": (max(b[1] for b in box) - min(b[1] for b in box)) > cfg["y_variable_pt"] if box else False,
                "support": len(vs)}
        tables = {}
        for name, cols in tab.items():
            col_x = {c: _med([v["bbox"][0] for _, v in items if v.get("bbox")]) for c, items in cols.items()}
            first_col = min(col_x, key=col_x.get)
            page_y: dict[int, list] = collections.defaultdict(list)
            last_y: dict[int, list] = collections.defaultdict(list)
            hs, pitches = [], []
            for fields, _ in docs:
                per_page: dict[int, list] = collections.defaultdict(list)
                for k, v in fields.items():
                    m = _IDX.match(k)
                    if m and m.group(1) == name and m.group(3) == first_col and v.get("bbox"):
                        per_page[v.get("page", 1)].append(v["bbox"])
                        hs.append(v["bbox"][3])
                for pg, bs in per_page.items():
                    page_y[pg].append(min(b[1] for b in bs)); last_y[pg].append(max(b[1] + b[3] for b in bs))
                    ys = sorted(b[1] for b in bs)
                    pitches += [b - a for a, b in zip(ys, ys[1:])]
            tables[name] = {"columns": col_x, "page_y0": {int(p): _med(v) for p, v in page_y.items()},
                            "y_max": {int(p): round(max(max(w) for w in last_y.values()), 2) for p in last_y},   # every page gets the full
                            # capacity: training may only contain short continuation pages
                            "row_h": _med(hs),
                            "row_pitch": _med(pitches) if pitches else None,
                            "support": len(next(iter(cols.values())))}
        out[dt] = {"page_size": [_med([s[0] for _, s in docs]), _med([s[1] for _, s in docs])],
                   "n_train_docs": n, "scalars": scalars, "tables": tables}
    return out


# ----------------------------------------------------------------------------- layout check
def _iou(a, b) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = a[2] * a[3] + b[2] * b[3] - i
    return i / u if u > 0 else 0.0


def check_layout(fields: dict, template: dict, doc_id: str = "", source: str = "text_layer",
                 cfg: Optional[dict] = None, page_size: Optional[tuple] = None) -> list[Finding]:
    """Compare extracted field positions with the template (Spatial Anomaly & Template Alignment)."""
    cfg = cfg or load_config()
    tol = cfg["tol_raster_pt"] if source == "ocr" else cfg["tol_digital_pt"]
    out: list[Finding] = []
    boxes = []
    for k, t in template.get("scalars", {}).items():
        f = fields.get(k)
        if f is None or f.get("bbox") is None or f.get("value") in (None, ""):
            if not t["optional"] and k not in cfg["never_required"]:
                out.append(Finding("field_missing", MODULE, "soft", 3, cfg["p_missing"],
                                   f"The field '{t['label'] or k}' was not found where this type of document normally has it.",
                                   doc_id, None, {"field": k}))
            continue
        b = f["bbox"]
        boxes.append((k, b))
        dx, dy = abs(b[0] - t["bbox"][0]), abs(b[1] - t["bbox"][1])
        if (dy > tol["y"] and not t.get("y_variable")) or (t["pattern"] is not None or t["enum"]) and dx > tol["x"]:
            out.append(Finding("layout_shifted", MODULE, "soft", 3, cfg["p_shifted"],
                               f"The value of '{t['label'] or k}' sits {max(dx, dy):.0f} pt away from its usual position.",
                               doc_id, (f.get("page", 1), *b), {"field": k, "dx": round(dx, 1), "dy": round(dy, 1),
                                                                 "tolerance": tol}))
        ps = page_size or template.get("page_size")
        if ps and (b[0] < -1 or b[1] < -1 or b[0] + b[2] > ps[0] + 1 or b[1] + b[3] > ps[1] + 1):
            out.append(Finding("field_out_of_bounds", MODULE, "soft", 3, cfg["p_oob"],
                               f"The value of '{t['label'] or k}' extends outside the page.", doc_id,
                               (f.get("page", 1), *b), {"field": k}))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _iou(boxes[i][1], boxes[j][1]) > cfg["overlap_iou"]:
                out.append(Finding("fields_overlap", MODULE, "soft", 3, cfg["p_overlap"],
                                   f"The values of '{boxes[i][0]}' and '{boxes[j][0]}' overlap each other.", doc_id,
                                   (fields[boxes[i][0]].get("page", 1), *boxes[i][1]), {"fields": [boxes[i][0], boxes[j][0]]}))
    if not out:
        out.append(Finding("layout_ok", MODULE, "info", 3, 0.0, "Field positions match the template.", doc_id))
    return out
