"""QR verification: decode, check HMAC against issuer keys, compare QR date to printed date.

Input : PDF/image path, issuer keys dict {issuer: secret_hex}, optional printed date string
        (from extracted fields), optional expected institution id.
Output: list[Finding]. ids: qr_valid(info) qr_bad_signature(hard,T1) qr_date_mismatch(hard,T1)
        qr_wrong_institution(hard,T1) qr_absent qr_malformed qr_unknown_issuer qr_date_unchecked
        qr_missing_on_digital(soft,T3,p~0.5: digital PDF of a doc type listed in config
        qr_required_doc_types that has no QR; needs doc_type=).
Does NOT detect: forged QRs signed with a stolen key, QR replaced by a different valid QR for
the same institution, tampering elsewhere on the page. A QR that exists but cannot be decoded
(blur/skew) is reported as qr_absent with evidence detected_but_undecodable=True.
The module touches no database: keys are passed in. The signature is an HMAC stand-in for PKI.
"""
from __future__ import annotations

import argparse, hashlib, hmac, json, re, sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml

from contracts import Finding

MODULE = "qr_verify"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "qr.yaml"
PREFIX = "SYNQR1"
_DATE_FMTS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %B %Y", "%d %b %Y", "%d-%m-%Y")
_LABELS = {"aicte": ("Date of Approval",), "default": ("Valid Until", "Valid Up To", "Valid To")}


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def has_text_layer(path: str | Path) -> bool:
    """True for a digital PDF (page 1 has extractable text); False for scans/images/unreadable."""
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        return False
    try:
        import pymupdf
        with pymupdf.open(str(p)) as d:
            return d.page_count > 0 and bool(d[0].get_text().strip())
    except Exception:
        return False


def parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def _raster(path: Path, dpi: int) -> np.ndarray:
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError("unreadable image")
        return img
    import pymupdf
    with pymupdf.open(str(path)) as d:
        if d.page_count == 0:
            raise ValueError("PDF has no pages")
        pm = d[0].get_pixmap(dpi=dpi, alpha=False)
        a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pm.n == 3 else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)


def _embedded_images(path: Path) -> list[np.ndarray]:
    """Native-resolution embedded images of page 1 (scans are one big image)."""
    import pymupdf
    out = []
    with pymupdf.open(str(path)) as d:
        for im in d[0].get_images()[:2]:
            pm = pymupdf.Pixmap(d, im[0])
            if pm.n >= 4:
                pm = pymupdf.Pixmap(pymupdf.csRGB, pm)
            a = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width, pm.n)
            out.append(cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if pm.n == 3 else cv2.cvtColor(a, cv2.COLOR_GRAY2BGR))
    return out


def _try(img: np.ndarray, scales=(1, 2, 3)) -> tuple[list[str], bool]:
    seen = False
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for sc in scales:
        im = g if sc == 1 else cv2.resize(g, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
        for det in (cv2.QRCodeDetectorAruco(), cv2.QRCodeDetector()):
            ok, texts, pts, _ = det.detectAndDecodeMulti(cv2.cvtColor(im, cv2.COLOR_GRAY2BGR))
            seen = seen or (pts is not None and len(pts) > 0)
            got = [t for t in (texts or []) if t]
            if got:
                return got, False
    return [], seen


def decode_qr(path: str | Path, dpis=(150, 300)) -> tuple[list[str], bool]:
    """Return (payload strings, detected_but_undecodable). Tries the native embedded image
    first (scans), then PyMuPDF rasters; two OpenCV detectors; 1-3x upscaling."""
    p = Path(path)
    cands: list[np.ndarray] = []
    seen = False
    if p.suffix.lower() == ".pdf":
        cands += _embedded_images(p)
    for img in cands:
        got, sq = _try(img)
        if got:
            return got, False
        seen = seen or sq
    for dpi in (dpis if p.suffix.lower() == ".pdf" else (0,)):
        got, sq = _try(_raster(p, dpi), (1,) if dpi == dpis[0] else (1, 2))
        if got:
            return got, False
        seen = seen or sq
    return [], seen


def sign(secret_hex: str, cert: str, inst: str, valid: str) -> str:
    return hmac.new(bytes.fromhex(secret_hex), f"{cert}|{inst}|{valid}".encode(),
                    hashlib.sha256).hexdigest()[:20]


def analyze(path: str | Path, doc_id: str = "", keys: Optional[dict] = None,
            printed_valid: Optional[str] = None, expected_inst_id: Optional[str] = None,
            expected: bool = True, doc_type: Optional[str] = None,
            cfg: Optional[dict] = None) -> list[Finding]:
    keys = keys or {}
    try:
        texts, undecodable = decode_qr(path)
    except Exception as exc:
        return [Finding("qr_unreadable", MODULE, "info", 1, 0.0,
                        "Could not check the QR code because the file could not be read.",
                        doc_id, None, {"error": f"{type(exc).__name__}: {exc}"})]
    payloads = [t for t in texts if t.startswith(PREFIX)]
    if not payloads:
        cfg = cfg or load_config()
        if (doc_type in cfg["qr_required_doc_types"] and not undecodable and has_text_layer(path)):
            return [Finding("qr_missing_on_digital", MODULE, "soft", 3, cfg["p_qr_missing_on_digital"],
                            f"This {doc_type.replace('_', ' ')} is a digital PDF but carries no QR code, "
                            "which issued documents of this type normally have.", doc_id, None,
                            {"doc_type": doc_type})]
        if expected:
            msg = ("A QR code appears on the page but could not be read; please upload a clearer copy."
                   if undecodable else "No QR code was found on this document.")
            return [Finding("qr_absent", MODULE, "soft", 3, 0.3, msg, doc_id, None,
                            {"detected_but_undecodable": undecodable})]
        return [Finding("qr_absent", MODULE, "info", 3, 0.0, "No QR code on this document.", doc_id)]
    raw = payloads[0]
    ev = {"payload": raw, "n_qr": len(payloads)}
    parts = raw.split("|")
    if len(parts) != 6:
        return [Finding("qr_malformed", MODULE, "soft", 1, 0.5,
                        "The QR code content is not in the expected format.", doc_id, None, ev)]
    _, issuer, cert, inst, valid, sig = parts
    if issuer not in keys:
        return [Finding("qr_unknown_issuer", MODULE, "soft", 1, 0.5,
                        f"The QR code names an issuer we cannot check ({issuer}).", doc_id, None, ev)]
    out: list[Finding] = []
    if not hmac.compare_digest(sign(keys[issuer], cert, inst, valid), sig):
        out.append(Finding("qr_bad_signature", MODULE, "hard", 1, 0.99,
                           "The QR code's security signature does not match, so it was not "
                           "issued by the named authority or was altered.", doc_id, None, ev))
    elif printed_valid is None:
        out.append(Finding("qr_date_unchecked", MODULE, "info", 1, 0.0,
                           "QR signature is valid; the printed date was not available to compare.",
                           doc_id, None, ev))
    if expected_inst_id and inst != expected_inst_id and not out:
        out.append(Finding("qr_wrong_institution", MODULE, "hard", 1, 0.9,
                           f"The QR code belongs to a different institution ({inst}).",
                           doc_id, None, {**ev, "expected": expected_inst_id}))
    if printed_valid is not None and not any(f.id == "qr_bad_signature" for f in out):
        qd, pd_ = parse_date(valid), parse_date(printed_valid)
        if qd is None or pd_ is None:
            out.append(Finding("qr_date_unchecked", MODULE, "info", 1, 0.0,
                               "Could not read one of the dates to compare.", doc_id, None,
                               {**ev, "printed": printed_valid}))
        elif qd != pd_:
            out.append(Finding("qr_date_mismatch", MODULE, "hard", 1, 0.95,
                               f"The date printed on the document ({pd_.isoformat()}) differs from "
                               f"the date in its signed QR code ({qd.isoformat()}).", doc_id, None,
                               {**ev, "printed": printed_valid, "qr_date": valid}))
    if not out:
        out.append(Finding("qr_valid", MODULE, "info", 1, 0.0,
                           "QR code signature and date are consistent.", doc_id, None, ev))
    return out


def _page_spans(page) -> list[tuple[int, str, tuple]]:
    """Horizontal text spans of a page as (draw-order seqno, text, bbox), via get_texttrace."""
    out = []
    for sp in page.get_texttrace():
        if sp.get("type") != 0 or tuple(round(v, 2) for v in sp["dir"]) != (1.0, 0.0):
            continue
        out.append((sp["seqno"], "".join(chr(ch[0]) for ch in sp["chars"]), tuple(sp["bbox"])))
    return out


def printed_date_from_text(path: str | Path, issuer_hint: str = "default") -> Optional[str]:
    """CLI helper: the VISIBLE date printed on the line of a date label, from the text layer.

    A forger may cover the original date and draw a new one on top (in the same content stream
    or in an appended incremental update); both stay in the text layer, and an appended date
    need not follow the label in stream order. So: take every date-looking span on the label's
    line (to its right, vertical overlap) and return the one drawn LAST. pdf_forensics reports
    the cover-up itself."""
    import pymupdf
    try:
        with pymupdf.open(str(path)) as d:
            page = d[0]
            spans = _page_spans(page)
            lines = [l.strip() for l in page.get_text().splitlines() if l.strip()]
    except Exception:
        return None
    for lab in _LABELS.get(issuer_hint, _LABELS["default"]):
        for _, text, (x0, y0, x1, y1) in spans:
            if text.strip() != lab:
                continue
            cands = []
            for sq, t2, (a0, b0, a1, b1) in spans:
                ov = min(y1, b1) - max(y0, b0)
                if a0 >= x1 - 1 and ov > 0.5 * min(y1 - y0, b1 - b0) and parse_date(t2.strip()):
                    cands.append((sq, t2.strip()))
            if cands:
                return max(cands)[1]
        for i, l in enumerate(lines[:-1]):          # fallback: value on the next text line
            if l == lab:
                return lines[i + 1]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.qr_verify")
    ap.add_argument("file"); ap.add_argument("--keys", required=True, help="JSON {issuer: secret_hex}")
    ap.add_argument("--inst"); ap.add_argument("--config"); ap.add_argument("--doc-type")
    a = ap.parse_args(argv)
    keys = json.load(open(a.keys))
    texts, _ = decode_qr(a.file)
    issuer = next((t.split("|")[1] for t in texts if t.startswith(PREFIX) and t.count("|") == 5), "default")
    pv = printed_date_from_text(a.file, "aicte" if issuer == "aicte" else "default")
    fs = analyze(a.file, Path(a.file).stem, keys, pv, a.inst, doc_type=a.doc_type,
                 cfg=load_config(a.config))
    json.dump([f.to_json() for f in fs], sys.stdout, indent=2); print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
