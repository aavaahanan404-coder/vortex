"""Perspective correction on synthetic phone photos of the corpus's clean scans.

Usage: python -m harness.eval_perspective [--corpus C] [--n 20] [--ocr]
For each clean scan (first N) a photo is simulated (harness.make_photo) with known page corners. Reported: pages found, corner error
(px and % of the page diagonal; the photo is 3000x2250, like a 6.7 MP phone photo), similarity of the corrected page to the original scan (normalised
correlation at 300 px), and with --ocr the extract accuracy on the corrected page against the manifest.
"""
from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path

import cv2
import numpy as np

from harness import corpus_io as c
from harness.make_photo import photograph
from modules import extract as X
from modules import perspective as P


def native_scan(path) -> np.ndarray:
    return P.load_image(path)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (300, 424)).astype(np.float32)
    b = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (300, 424)).astype(np.float32)
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def norm(s): return re.sub(r"[^a-z0-9]", "", str(s).lower())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus"); ap.add_argument("--n", type=int, default=20); ap.add_argument("--ocr", action="store_true")
    a = ap.parse_args(argv)
    if a.corpus:
        c.set_corpus(a.corpus)
    scans = [e for e in c.iter_docs(clean_only=True) if c.is_scan(e)][:a.n]
    found = 0; errs = []; sims = []; ocr_ok = ocr_n = raw_ok = 0
    tpl = X.load_templates()
    for i, e in enumerate(scans):
        page = native_scan(e["path"])
        photo, true = photograph(page, seed=i)
        flat, info = P.correct(photo)
        if flat is None:
            continue
        found += 1
        q = np.array(info["corners"], np.float32)
        diag = float(np.hypot(*(true[2] - true[0])))
        errs.append(float(np.mean(np.linalg.norm(q - P.order_corners(true), axis=1))) / diag * 100)
        sims.append(ncc(flat, page))
        if a.ocr:
            with tempfile.TemporaryDirectory() as d:
                for tag, img in (("flat", flat), ("raw", photo)):
                    pth = Path(d) / f"{tag}.png"; cv2.imwrite(str(pth), img)
                    doc, _ = X.extract(pth, e["doc_id"], e["doc_type"], tpl)
                    truth = {k: v for k, v in e["fields"].items() if k not in ("qr", "seal", "signature") and v.get("value")}
                    ok = sum(1 for k, v in truth.items() if k in doc.fields and norm(doc.fields[k]["value"]) == norm(v["value"]))
                    if tag == "flat":
                        ocr_ok += ok; ocr_n += len(truth)
                    else:
                        raw_ok += ok
    n = len(scans)
    print(f"# perspective on {c.corpus_root().name}: {n} simulated photos of clean scans\n")
    print(f"- page found: {found}/{n}")
    if errs:
        print(f"- corner error: mean {np.mean(errs):.2f}% of the page diagonal, worst {max(errs):.2f}%")
        print(f"- similarity of the corrected page to the original scan: mean {np.mean(sims):.3f}, worst {min(sims):.3f}")
    if a.ocr and ocr_n:
        print(f"- fields read correctly after correction: {ocr_ok}/{ocr_n} ({ocr_ok / ocr_n:.1%}); on the uncorrected photo: {raw_ok}/{ocr_n} ({raw_ok / ocr_n:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
