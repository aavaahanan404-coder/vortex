"""PDF structure forensics: covered text, appended revisions, editor metadata, odd fonts.

Input : path to a PDF (digital or scanned), optional doc_type (enables the font allow-list).
Output: list[Finding]. ids (all soft, tier 3; structural evidence is deterministic but a legitimate
        producer can do the same thing, so none is "hard"):
          covered_text      p~0.9   text drawn earlier, then a near-white filled shape drawn over it
                                    (and usually new text on top). evidence: hidden/visible strings.
          incremental_update p~0.5  more than one revision (%%EOF); p drops when no page content
                                    changed. bbox/evidence = what the later revisions changed.
          signed_content_changed p~0.85  page content differs from the revision a /ByteRange covers.
          editor_metadata   p 0.35..0.7  /Creator or /Producer names an editing tool and/or
                                    ModDate is long after CreationDate (names + gap, not absolute dates).
          font_anomaly      p~0.6   a font outside the per-document-type allow-list
                                    (config/fonts_allowlist.yaml, built from train-split clean docs).
          pdf_structure_ok (info), pdf_unreadable (info), pdf_not_applicable (info).
Tier-4 rule: nothing here is image-based; image heuristics (ELA, pHash) live in other modules and may
never be hard.
Helper: covered_text_regions(path) -> list of dicts with the visible AND the hidden string plus the
        bbox, so a later `extract` can lower that field's confidence.
Does NOT detect: text covered by a non-white fill or an image patch; edits made by re-rendering the
whole page (no structural trace); a forger who rebuilds the PDF from scratch with a plausible
creator; fonts that are allowed for the type but swapped between fields; tampering inside embedded
images; signature validity (only /ByteRange coverage is checked, no PKI). Signatures stored inside
compressed object streams are not seen by the raw /ByteRange scan.
Fail-safe: never raises; unreadable input yields an info finding.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from contracts import Finding

MODULE = "pdf_forensics"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "forensics.yaml"
_EOF_RE = re.compile(rb"startxref\s+\d+\s*%%EOF")
_PREV_RE = re.compile(rb"/Prev\s+\d+")
_BYTERANGE_RE = re.compile(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]")
_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")


def load_config(path: Optional[str | Path] = None) -> dict:
    p = Path(path or DEFAULT_CONFIG)
    with open(p, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg.setdefault("fonts_by_doc_type", {})
    af = cfg.get("fonts_allowlist_file")
    if af and (p.parent / af).exists():
        with open(p.parent / af, encoding="utf-8") as fh:
            cfg["fonts_by_doc_type"] = (yaml.safe_load(fh) or {}).get("fonts_by_doc_type", {})
    return cfg


def norm_font(name: str) -> str:
    """Base font name without the ABCDEF+ subset prefix."""
    return _SUBSET_RE.sub("", name or "")


def parse_pdf_date(s: Optional[str]) -> Optional[datetime]:
    """'D:20251005103000+05'30'' -> naive datetime (time zone ignored; we only need day gaps)."""
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", (s or "").strip())
    if not m or not m.group(1):
        return None
    try:
        y, mo, d, h, mi, se = (int(g) if g else (1 if i in (1, 2) else 0) for i, g in enumerate(m.groups()))
        return datetime(y, mo, d, h, mi, se)
    except ValueError:
        return None


# ----------------------------------------------------------------------------- revisions
def revision_ends(data: bytes) -> list[int]:
    """Byte offsets just after each revision's %%EOF. A linearized file's leading first-page
    xref is not a revision of its own and is discounted."""
    ends = [m.end() for m in _EOF_RE.finditer(data)]
    if len(ends) >= 2 and b"/Linearized" in data[:1024]:
        ends = ends[1:]
    return ends


def _union(rects: list[tuple]) -> Optional[tuple]:
    if not rects:
        return None
    x0 = min(r[0] for r in rects); y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects); y1 = max(r[3] for r in rects)
    return (x0, y0, x1, y1)


def _spans(page) -> list[dict]:
    """Text spans in draw order: {seqno, font, text, bbox, chars:[(ch, bbox)], opacity, size}."""
    out = []
    for sp in page.get_texttrace():
        if sp.get("type") != 0:
            continue
        chars = []
        for ch in sp["chars"]:
            if ch[0] and ch[0] > 0:
                chars.append((chr(ch[0]), tuple(ch[3])))
        out.append({"seqno": sp["seqno"], "font": sp["font"], "text": "".join(c for c, _ in chars),
                    "bbox": tuple(sp["bbox"]), "chars": chars, "opacity": sp.get("opacity") or 1.0,
                    "size": sp.get("size"), "horizontal": tuple(round(v, 2) for v in sp["dir"]) == (1.0, 0.0)})
    return out


def _page_signature(page) -> dict:
    sig = {"text": set(), "fills": set(), "images": set()}
    for s in _spans(page):
        sig["text"].add((s["text"], tuple(round(v, 1) for v in s["bbox"])))
    for d in page.get_drawings():
        if d.get("fill") is not None:
            sig["fills"].add((tuple(round(c, 2) for c in d["fill"]), tuple(round(v, 1) for v in d["rect"])))
    try:
        for im in page.get_image_info(hashes=True):
            sig["images"].add((tuple(round(v, 1) for v in im["bbox"]), im.get("digest")))
    except Exception:
        pass
    return sig


def revision_diff(older, newer, max_pages: int = 5) -> dict:
    """What `newer` shows that `older` (an earlier revision of the same file) did not.
    Returns {changed: bool, added_text, removed_text, added_fills, added_images, regions:[(page,x0,y0,x1,y1)]}."""
    res = {"changed": False, "added_text": [], "removed_text": [], "added_fills": 0, "added_images": 0,
           "regions": []}
    for pno in range(min(newer.page_count, older.page_count, max_pages)):
        a, b = _page_signature(older[pno]), _page_signature(newer[pno])
        add_t, rem_t = b["text"] - a["text"], a["text"] - b["text"]
        add_f, add_i = b["fills"] - a["fills"], b["images"] - a["images"]
        if not (add_t or rem_t or add_f or add_i):
            continue
        res["changed"] = True
        res["added_text"] += [t for t, _ in sorted(add_t, key=lambda x: x[1])]
        res["removed_text"] += [t for t, _ in sorted(rem_t, key=lambda x: x[1])]
        res["added_fills"] += len(add_f); res["added_images"] += len(add_i)
        rects = [bb for _, bb in add_t] + [bb for _, bb in rem_t] + [r for _, r in add_f] + [r for r, _ in add_i]
        u = _union(rects)
        if u:
            res["regions"].append((pno + 1, *u))
    if newer.page_count != older.page_count:
        res["changed"] = True
        res["page_count_change"] = [older.page_count, newer.page_count]
    return res


def _open_bytes(data: bytes):
    import pymupdf
    return pymupdf.open(stream=data, filetype="pdf")


def _incremental_findings(data: bytes, doc, doc_id: str, cfg: dict) -> list[Finding]:
    ends = revision_ends(data)
    out: list[Finding] = []
    sig = _BYTERANGE_RE.search(data)
    if sig:                                   # signed revision: compare it with the final content
        end = int(sig.group(3)) + int(sig.group(4))
        if 0 < end < len(data.rstrip()):
            try:
                prefix = _open_bytes(data[:end])
                dif = revision_diff(prefix, doc, cfg["max_diff_pages"])
                if dif["changed"]:
                    out.append(Finding(
                        "signed_content_changed", MODULE, "soft", 3, cfg["p_signed_content_changed"],
                        "The page content differs from the version covered by the document's digital "
                        "signature, so the content was changed after signing.", doc_id,
                        (*dif["regions"][0][:1], *_xywh(dif["regions"][0][1:])) if dif["regions"] else None,
                        {"signed_range_end": end, "file_size": len(data),
                         "added_text": dif["added_text"][:10], "removed_text": dif["removed_text"][:10]}))
            except Exception:
                pass
    if len(ends) < 2:
        return out
    ev = {"n_revisions": len(ends), "n_prev": len(_PREV_RE.findall(data)), "revision_ends": ends}
    bbox = None
    p = cfg["p_incremental_no_visible_change"]
    msg = ("The file was saved in more than one revision, but the page content looks unchanged "
           "(for example a form fill or annotation).")
    try:
        first = _open_bytes(data[:ends[0]])
        dif = revision_diff(first, doc, cfg["max_diff_pages"])
        ev.update({"changed": dif["changed"], "added_text": dif["added_text"][:10],
                   "removed_text": dif["removed_text"][:10], "added_white_or_filled_shapes": dif["added_fills"],
                   "added_images": dif["added_images"], "revision1_creator": (first.metadata or {}).get("creator")})
        if dif["changed"]:
            p = cfg["p_incremental_update"]
            where = dif["regions"][0] if dif["regions"] else None
            bbox = (where[0], *_xywh(where[1:])) if where else None
            msg = ("The file was edited after it was first issued: a later revision was appended and it "
                   "changes what the page shows" + (f" (adds '{', '.join(dif['added_text'][:3])}')." if dif["added_text"] else "."))
    except Exception as exc:
        ev["diff_error"] = f"{type(exc).__name__}: {exc}"
        p = cfg["p_incremental_update"]
        msg = "The file was saved in more than one revision; the earlier revision could not be compared."
    out.append(Finding("incremental_update", MODULE, "soft", 3, p, msg, doc_id, bbox, ev))
    return out


def _xywh(r: tuple) -> tuple:
    return (round(r[0], 2), round(r[1], 2), round(r[2] - r[0], 2), round(r[3] - r[1], 2))


# ----------------------------------------------------------------------------- covered text
def _is_white(fill, cfg) -> bool:
    return fill is not None and len(fill) >= 1 and all(c >= cfg["white_min"] for c in fill)


def _inside_frac(box: tuple, rect: tuple) -> float:
    ix = max(0.0, min(box[2], rect[2]) - max(box[0], rect[0]))
    iy = max(0.0, min(box[3], rect[3]) - max(box[1], rect[1]))
    a = max(1e-9, (box[2] - box[0]) * (box[3] - box[1]))
    return ix * iy / a


def covered_text_regions(path_or_doc, cfg: Optional[dict] = None) -> list[dict]:
    """Every place where text was covered by a later near-white filled shape.

    Returns dicts: page (1-based), bbox (x, y, w, h) around the covered + replacement text,
    hidden_text (what is under the patch), visible_text (what was drawn on top of the patch after
    it, "" if nothing), hidden_bbox, visible_bbox (x0,y0,x1,y1), fill_rect, fill_seqno,
    visible_font. "Under" is decided by drawing order (texttrace seqno), not by reading order."""
    cfg = cfg or load_config()
    import pymupdf
    own = isinstance(path_or_doc, (str, Path))
    doc = pymupdf.open(str(path_or_doc)) if own else path_or_doc
    out: list[dict] = []
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            parea = page.rect.width * page.rect.height
            spans = [s for s in _spans(page) if s["opacity"] >= cfg["min_text_opacity"] and s["text"].strip()]
            if not spans:
                continue
            for d in page.get_drawings():
                if d.get("type") not in ("f", "fs") or not _is_white(d.get("fill"), cfg):
                    continue
                if (d.get("fill_opacity") if d.get("fill_opacity") is not None else 1.0) < cfg["min_fill_opacity"]:
                    continue
                r = tuple(d["rect"])
                if (r[2] - r[0]) * (r[3] - r[1]) > cfg["max_fill_page_frac"] * parea:
                    continue
                hidden, visible, hb, vb, vfont = [], [], [], [], None
                for s in spans:
                    cov = [(ch, bb) for ch, bb in s["chars"] if _inside_frac(bb, r) >= cfg["char_overlap_min"]]
                    if not cov:
                        continue
                    if len(cov) >= cfg["span_dominated_frac"] * len(s["chars"]):
                        cov = s["chars"]          # the span lives in the patch: take it whole (a glyph
                                                  # that sticks out past the patch edge is still part of it)
                    if s["seqno"] < d["seqno"]:
                        hidden.append("".join(ch for ch, _ in cov)); hb += [bb for _, bb in cov]
                    elif s["seqno"] > d["seqno"]:
                        visible.append("".join(ch for ch, _ in cov)); vb += [bb for _, bb in cov]
                        vfont = vfont or s["font"]
                if not hidden:
                    continue
                h_txt, v_txt = "".join(hidden).strip(), "".join(visible).strip()
                if cfg.get("skip_identical_redraw") and v_txt and v_txt == h_txt:
                    continue
                u = _union(hb + vb)
                out.append({"page": pno + 1, "bbox": _xywh(u), "hidden_text": h_txt, "visible_text": v_txt,
                            "hidden_bbox": _union(hb), "visible_bbox": _union(vb), "fill_rect": r,
                            "fill_seqno": d["seqno"], "visible_font": vfont})
    finally:
        if own:
            doc.close()
    return out


def _covered_findings(doc, doc_id: str, cfg: dict) -> list[Finding]:
    out = []
    for h in covered_text_regions(doc, cfg)[:cfg["max_covered_findings"]]:
        repl = bool(h["visible_text"])
        out.append(Finding(
            "covered_text", MODULE, "soft", 3,
            cfg["p_covered_text"] if repl else cfg["p_covered_text_no_replacement"],
            (f"Text on page {h['page']} was covered with a white box and replaced: the hidden text is "
             f"'{h['hidden_text']}', what is shown now is '{h['visible_text']}'." if repl else
             f"Text on page {h['page']} was covered with a white box: the hidden text is '{h['hidden_text']}'."),
            doc_id, (h["page"], *h["bbox"]),
            {"hidden_text": h["hidden_text"], "visible_text": h["visible_text"],
             "visible_font": h["visible_font"], "fill_rect": list(h["fill_rect"])}))
    return out


# ----------------------------------------------------------------------------- fonts
def _font_findings(doc, doc_type: Optional[str], doc_id: str, cfg: dict) -> list[Finding]:
    allow = cfg["fonts_by_doc_type"].get(doc_type or "")
    if not allow:
        return []
    ok = {norm_font(f) for f in allow}
    bad: dict[str, dict] = {}
    for pno in range(min(doc.page_count, 20)):
        for s in _spans(doc[pno]):
            f = norm_font(s["font"])
            if s["text"].strip() and f not in ok and f not in bad:
                bad[f] = {"page": pno + 1, "bbox": _xywh(s["bbox"]), "text": s["text"][:40]}
    if not bad:
        return []
    first = min(bad.values(), key=lambda v: (v["page"], v["bbox"][1], v["bbox"][0]))
    return [Finding("font_anomaly", MODULE, "soft", 3, cfg["p_font_anomaly"],
                    f"Text on page {first['page']} uses a typeface ({', '.join(sorted(bad))}) that other "
                    f"{(doc_type or 'documents').replace('_', ' ')} files do not use.", doc_id,
                    (first["page"], *first["bbox"]),
                    {"fonts": {k: v["text"] for k, v in bad.items()}, "allowed": sorted(ok)})]


# ----------------------------------------------------------------------------- metadata
def _metadata_findings(meta: dict, doc_id: str, cfg: dict) -> list[Finding]:
    names = [n.lower() for n in cfg["editor_names"]]
    tool = " / ".join(x for x in ((meta.get("creator") or ""), (meta.get("producer") or "")) if x)
    hit = next((n for n in names if n in tool.lower()), None)
    c, m = parse_pdf_date(meta.get("creationDate")), parse_pdf_date(meta.get("modDate"))
    gap = (m - c).total_seconds() / 86400 if (c and m) else None
    gap_hit = gap is not None and gap > cfg["moddate_gap_days"]
    if not hit and not gap_hit:
        return []
    p = cfg["p_editor_and_gap"] if (hit and gap_hit) else cfg["p_editor_name"] if hit else cfg["p_gap_only"]
    why = []
    if hit:
        why.append(f"was last saved by an editing tool ({tool})")
    if gap_hit:
        why.append(f"was modified {gap:.0f} days after it was created")
    return [Finding("editor_metadata", MODULE, "soft", 3, p, "The file " + " and ".join(why) + ".",
                    doc_id, None, {"creator": meta.get("creator"), "producer": meta.get("producer"),
                                   "creation_date": meta.get("creationDate"), "mod_date": meta.get("modDate"),
                                   "gap_days": None if gap is None else round(gap, 1), "tool_match": hit})]


# ----------------------------------------------------------------------------- entry point
def analyze(path: str | Path, doc_id: str = "", doc_type: Optional[str] = None,
            cfg: Optional[dict] = None) -> list[Finding]:
    cfg = cfg or load_config()
    try:
        data = Path(path).read_bytes()
        if b"%PDF" not in data[:1024]:
            return [Finding("pdf_not_applicable", MODULE, "info", 3, 0.0,
                            "Not a PDF file, so PDF structure checks were skipped.", doc_id)]
        doc = _open_bytes(data)
        if doc.needs_pass or doc.page_count == 0:
            raise ValueError("encrypted or empty PDF")
    except Exception as exc:
        return [Finding("pdf_unreadable", MODULE, "info", 3, 0.0,
                        "Could not run PDF structure checks because the file could not be read.",
                        doc_id, None, {"error": f"{type(exc).__name__}: {exc}"})]
    out: list[Finding] = []
    try:
        for step in (lambda: _metadata_findings(doc.metadata or {}, doc_id, cfg),
                     lambda: _incremental_findings(data, doc, doc_id, cfg),
                     lambda: _covered_findings(doc, doc_id, cfg),
                     lambda: _font_findings(doc, doc_type, doc_id, cfg)):
            try:
                out += step()
            except Exception as exc:          # one failing check must not hide the others
                out.append(Finding("pdf_check_failed", MODULE, "info", 3, 0.0,
                                   "One PDF structure check could not be completed.", doc_id, None,
                                   {"error": f"{type(exc).__name__}: {exc}"}))
    finally:
        doc.close()
    if not any(f.kind != "info" for f in out):
        out.append(Finding("pdf_structure_ok", MODULE, "info", 3, 0.0,
                           "No structural signs of editing were found.", doc_id))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.pdf_forensics")
    ap.add_argument("file"); ap.add_argument("--doc-type"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    fs = analyze(a.file, Path(a.file).stem, a.doc_type, load_config(a.config))
    json.dump([f.to_json() for f in fs], sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
