"""Builds demo_assets/sample_uploads/: the files a presenter uploads during the demo (everything is synthetic, from corpus_v2).

  applicant_SYN-I0003/   the ten documents of SYN-I0003 (a clean institution: every check passes), named in the order of the upload cards,
                         plus a simulated phone photo of its building plan and a blurry one
  admin_rule_pdfs/       a structured rule update (accepted, with one changed norm that the preview flags) and a prose page (refused)
Deterministic. The corpus is only read. Run:  python -m harness.make_demo_samples
"""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from harness import corpus_io as c
from harness.make_photo import photograph

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo_assets" / "sample_uploads"
INST = "SYN-I0003"

PROSE = """APPROVAL PROCESS HANDBOOK (sample text, written for the demo)

3.4 Faculty. An institution shall maintain, at all times, at least one full-time teacher for every eighteen students
admitted in an engineering programme, and one for every twelve students in a management (MBA) programme. Part-time
faculty shall not be counted towards this ratio unless the Council has granted a written exception.

3.5 Land and building. The built-up area available for instruction shall be adequate for the sanctioned intake. Where
the land is held on lease, the lease shall run for not less than twenty-five years from the date of application.

3.6 Fire safety. A fire safety certificate valid on the date of application shall be furnished with every
application. A certificate that has lapsed is treated as if none had been furnished.

This page is ordinary prose, not a table, so the table reader cannot read it and refuses it. With the Gemini key
set, "Read it with AI" turns the numbers above into a preview for you to check and confirm."""

README = """SAMPLE FILES FOR THE DEMO (all synthetic)

APPLICANT  -> sign in with the Demo hub button "Applicant: new" (token demo-applicant-i0003), folder applicant_SYN-I0003/
  1. Start your application. Use exactly:   Institution name: Bharathi College of Engineering
                                            Application date: 2026-01-14
                                            Application type: Existing institution   Type of institution: Engineering
  2. Upload the files 01 .. 09 on the matching cards (the numbers follow the order of the cards on the page).
     10_prior_eoa_letter.pdf is the optional "Previous approval (EoA)" card.
  3. Photo demo: on the "Building plan approval" card upload 03_building_plan_PHONE_PHOTO.jpg INSTEAD of the PDF.
     Drag the corner handles, Straighten, Use this page. (A real phone photo of any printed page also works.)
  4. Blur warning demo: choose 03_building_plan_BLURRY_PHOTO.jpg: the page warns before uploading.
  5. Press "Check my application": everything is fine. Submit (this one is clean, it will be scored low).
  Documents 01-09 and the photo belong to Bharathi College; the other institutions' papers are not interchangeable.

ADMIN -> Demo hub button "Admin" (demo-admin), Rules and norms, folder admin_rule_pdfs/
  rule_update_example.pdf      a structured update: accepted, with notes (a bad row, a rule switched off); Apply, then Restore.
  handbook_prose_example.pdf   a prose page: the table reader refuses it; with the Gemini key, "Read it with AI" proposes 3 changes
                               (engineering 18 students per teacher, MBA 12, lease 25 years) for you to check and confirm.
"""


def main() -> int:
    docs = sorted((e for e in c.iter_docs() if e["inst_id"] == INST), key=lambda e: e["file"])
    dst = OUT / f"applicant_{INST}"
    shutil.rmtree(OUT, ignore_errors=True)
    dst.mkdir(parents=True)
    for e in docs:
        shutil.copy(e["path"], dst / Path(e["file"]).name)
    plan = next(e for e in docs if e["doc_type"] == "building_plan")
    with pymupdf.open(plan["path"]) as d:
        pix = d[0].get_pixmap(dpi=150, alpha=False)
    page = cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3), cv2.COLOR_RGB2BGR)
    photo, _ = photograph(page, seed=7, canvas=(2400, 1800), kind="wood", tilt=7.0, jitter=0.04)
    cv2.imwrite(str(dst / "03_building_plan_PHONE_PHOTO.jpg"), photo, [cv2.IMWRITE_JPEG_QUALITY, 88])
    cv2.imwrite(str(dst / "03_building_plan_BLURRY_PHOTO.jpg"), cv2.GaussianBlur(photo, (0, 0), 14), [cv2.IMWRITE_JPEG_QUALITY, 88])

    rules = OUT / "admin_rule_pdfs"
    rules.mkdir()
    shutil.copy(ROOT / "demo_assets" / "rule_update_example.pdf", rules / "rule_update_example.pdf")
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_textbox(pymupdf.Rect(50, 60, 545, 780), PROSE, fontsize=11, fontname="helv")
    doc.save(str(rules / "handbook_prose_example.pdf"))
    (OUT / "README.txt").write_text(README, encoding="utf-8")
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"{p.relative_to(OUT).as_posix():60s} {p.stat().st_size // 1024:5d} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
