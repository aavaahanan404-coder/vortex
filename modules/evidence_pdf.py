"""One-click evidence report: flagged boxes, heatmaps and rule/check citations as a downloadable PDF.

Input : a case dict (JSON-serialisable) and an output path.
          case = {"case_id", "generated_at" (string, supplied by the caller: the wall clock is not read), "blind" (true: no risk score
                  or per-finding probability, for reviewers who must not be anchored by the machine),
                  "institution": {name, inst_id, state, application_date, ...},
                  "score": {"score", "category", "reasons"}      (output of the score module, optional),
                  "documents": [{"doc_id", "doc_type", "path", "sha256"?}],
                  "findings": [Finding.to_json() dicts],
                  "rules": {"R004": {"name", "handbook_ref"}, ...}   (optional citations for rule findings)}
Output: build_evidence_pdf(case, out_path) writes the PDF and returns {"pages", "n_findings", "n_images",
        "sha256", "path"}. Same case -> byte-identical PDF (reportlab invariant mode).
Content: cover and summary (score, category, counts by tier), then one section per hard/soft finding,
        hard first, with its trust-ladder tier, probability, plain-language message, key evidence, the page
        crop with the flagged box drawn in red (from the source PDF), the heatmap when evidence carries one,
        and the citation (check id and handbook clause or "not yet mapped"). Info findings are only counted.
Staff-only: it contains forensic detail that the applicant portal must never show.
Does NOT: decide anything, redact, sign, or embed the full source documents. A missing or unreadable
source file does not fail the report: that finding is printed without an image and says so.
CLI   : python -m modules.evidence_pdf CASE.json OUT.pdf
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MODULE = "evidence_pdf"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "evidence.yaml"
_KIND_ORDER = {"hard": 0, "soft": 1, "info": 2}


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _short(v, n: int) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n - 1] + "..."


def page_crop_png(pdf_path: str | Path, bbox: tuple, cfg: dict) -> bytes:
    """PNG of the page region around bbox=(page, x, y, w, h) with the box outlined in red."""
    import pymupdf
    from PIL import Image as PILImage, ImageDraw
    page_no, x, y, w, h = bbox
    with pymupdf.open(str(pdf_path)) as d:
        pg = d[int(page_no) - 1]
        m = cfg["crop_margin_pt"]
        clip = pymupdf.Rect(x - m, y - m, x + w + m, y + h + m) & pg.rect
        if clip.is_empty:
            clip = pg.rect
        z = cfg["crop_dpi"] / 72.0
        pm = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, colorspace=pymupdf.csRGB, alpha=False)
        img = PILImage.frombytes("RGB", (pm.width, pm.height), pm.samples)
    dr = ImageDraw.Draw(img)
    x0, y0 = (x - clip.x0) * z, (y - clip.y0) * z
    for k in range(3):
        dr.rectangle([x0 - 2 - k, y0 - 2 - k, x0 + w * z + 2 + k, y0 + h * z + 2 + k], outline=(220, 0, 0))
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue()


def _img_flowable(png: bytes, max_w_pt: float) -> Image:
    iw, ih = ImageReader(io.BytesIO(png)).getSize()
    w = min(max_w_pt, float(iw)); h = w * ih / iw
    max_h = 330.0
    if h > max_h:
        w, h = w * max_h / h, max_h
    return Image(io.BytesIO(png), width=w, height=h)


def _citation(f: dict, case: dict, cfg: dict) -> str:
    rule = (case.get("rules") or {}).get(f["id"])
    if rule:
        return f"Rule {f['id']} ({rule.get('name', '')}): " + (rule.get("handbook_ref") or cfg["handbook_ref_unmapped"])
    chk = cfg["checks"].get(f["id"])
    if chk:
        ref = (case.get("handbook_refs") or {}).get(chk["check"], cfg["handbook_ref_unmapped"])
        return f"Check {chk['check']}: {chk['desc']} - {ref}"
    return f"No citation registered for '{f['id']}' - {cfg['handbook_ref_unmapped']}"


def build_evidence_pdf(case: dict, out_path: str | Path, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], spaceBefore=8, spaceAfter=3)
    B = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=12)
    SM = ParagraphStyle("sm", parent=B, fontSize=8, textColor=colors.HexColor("#444444"))
    docs = {d["doc_id"]: d for d in case.get("documents", [])}
    fs = sorted(case.get("findings", []), key=lambda f: (_KIND_ORDER.get(f["kind"], 3), -f["p"], f["id"]))
    shown = [f for f in fs if f["kind"] in ("hard", "soft")]
    inst, sc = case.get("institution") or {}, case.get("score") or {}
    story = [Paragraph(escape(cfg["title"]), H1),
             Paragraph(f"Case {escape(str(case.get('case_id', '-')))} &nbsp;|&nbsp; generated {escape(str(case.get('generated_at', '-')))}", SM),
             Spacer(1, 6)]
    meta = [["Institution", f"{inst.get('name', '-')} ({inst.get('inst_id', '-')})"],
            ["State / application date", f"{inst.get('state', '-')} / {inst.get('application_date', '-')}"],
            *([] if case.get("blind") else [["Risk score / category", f"{sc.get('score', '-')} / {sc.get('category', '-')}"]]),
            ["Documents", ", ".join(docs) or "-"]]
    t = Table([[Paragraph(f"<b>{escape(a)}</b>", B), Paragraph(escape(str(b)), B)] for a, b in meta], colWidths=[48 * mm, 122 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 8)]
    by_tier = {}
    for f in shown:
        by_tier[f["tier"]] = by_tier.get(f["tier"], 0) + 1
    n_info = sum(1 for f in fs if f["kind"] == "info")
    story.append(Paragraph("Summary", H2))
    story.append(Paragraph(
        (f"{len(shown)} finding(s) need attention (" + ", ".join(f"{n} in {cfg['tier_names'][k].split(' - ')[0]}" for k, n in sorted(by_tier.items())) + f"); {n_info} informational finding(s) not listed."
         if shown else f"No finding needs attention ({n_info} informational finding(s) recorded)."), B))
    story.append(Paragraph(escape(cfg["disclaimer"]), SM))
    n_images = 0
    for i, f in enumerate(shown, 1):
        ptxt = "" if case.get("blind") else " p=%.2f" % f["p"]
        block = [Paragraph(f"{i}. {escape(f['id'])} &nbsp; <font size=9>[{f['kind'].upper()} - {escape(cfg['tier_names'][f['tier']])}]{ptxt}</font>", H2),
                 Paragraph(escape(f["message"]), B)]
        d = docs.get(f["doc_id"], {})
        where = f"Document: {f['doc_id']}" + (f" ({d['doc_type']})" if d.get("doc_type") else "")
        if f.get("bbox"):
            b = f["bbox"]; where += f" - page {int(b[0])}, box x={b[1]:.0f} y={b[2]:.0f} w={b[3]:.0f} h={b[4]:.0f} pt"
        block.append(Paragraph(escape(where), SM))
        ev = [(k, v) for k, v in (f.get("evidence") or {}).items() if k not in ("heatmap", "matches") and v not in (None, "", [], {})]
        rows = [[Paragraph(escape(str(k)), SM), Paragraph(escape(_short(v, cfg["max_value_chars"])), SM)] for k, v in ev[:cfg["max_evidence_items"]]]
        m = (f.get("evidence") or {}).get("matches")
        if m:
            rows.append([Paragraph("matches", SM), Paragraph(escape(_short([{k: x.get(k) for k in ("doc_id", "inst_id", "similarity", "method") if k in x} for x in m[:3]], cfg["max_value_chars"] * 2)), SM)])
        if rows:
            et = Table(rows, colWidths=[40 * mm, 130 * mm])
            et.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            block.append(et)
        if f.get("bbox"):
            try:
                src = d.get("path")
                if not src or not Path(src).exists():
                    raise FileNotFoundError("source document not available")
                block.append(Spacer(1, 4))
                block.append(_img_flowable(page_crop_png(src, tuple(f["bbox"]), cfg), cfg["crop_max_width_pt"]))
                n_images += 1
            except Exception as exc:
                block.append(Paragraph(f"(page image not available: {escape(type(exc).__name__)})", SM))
        hm = (f.get("evidence") or {}).get("heatmap")
        if hm and Path(hm).exists():
            try:
                block.append(Paragraph("Heatmap (brighter = stronger signal)", SM))
                block.append(_img_flowable(Path(hm).read_bytes(), cfg["crop_max_width_pt"]))
                n_images += 1
            except Exception:
                block.append(Paragraph("(heatmap could not be embedded)", SM))
        block.append(Paragraph("<i>" + escape(_citation(f, case, cfg)) + "</i>", SM))
        story.append(KeepTogether(block))
    if docs:
        story.append(Paragraph("Source documents", H2))
        for d in docs.values():
            sha = d.get("sha256")
            if not sha and d.get("path") and Path(d["path"]).exists():
                sha = hashlib.sha256(Path(d["path"]).read_bytes()).hexdigest()
            story.append(Paragraph(escape(f"{d['doc_id']}  sha256 {sha or 'n/a'}"), SM))
    out = Path(out_path)
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=cfg["title"], author="SIH26261", invariant=1).build(
        story, onFirstPage=_footer, onLaterPages=_footer)
    import pymupdf
    with pymupdf.open(str(out)) as d2:
        pages = d2.page_count
    return {"pages": pages, "n_findings": len(shown), "n_images": n_images, "path": str(out),
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}


def _footer(canvas, doc):
    canvas.saveState(); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 10 * mm, "STAFF ONLY - advisory evidence report, not a decision")
    canvas.drawRightString(190 * mm, 10 * mm, f"page {doc.page}"); canvas.restoreState()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.evidence_pdf")
    ap.add_argument("case_json"); ap.add_argument("out_pdf"); ap.add_argument("--config")
    a = ap.parse_args(argv)
    case = json.load(open(a.case_json, encoding="utf-8"))
    json.dump(build_evidence_pdf(case, a.out_pdf, load_config(a.config)), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
