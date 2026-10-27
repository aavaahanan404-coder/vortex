# Continue SIH26261: Batch A is DONE. Start Batch B only after reading reports/BATCH_A_REPORT.md.

Attach: the original "build the SIH26261 verification modules" prompt (still authoritative), the corpus zip, and sih26261_repo.zip.
Setup: unzip the repo; extract the corpus to ./corpus (or set SIH_CORPUS); `pip install -r requirements.txt --break-system-packages`;
verify with `python -m pytest tests -q` (expect all pass, ~50 s) and `python -m harness.run_module qr_verify`.

## Your task now
1. Read reports/BATCH_A_REPORT.md; relay the 4 "decisions" to the user and continue with defaults unless they answer.
2. Build **Batch B** (spec section 5, items 9-11): `modules/pdf_forensics.py` (+ `config/forensics.yaml`, a helper script that builds the per-doc-type
   font allow-list from clean docs), `modules/dedup.py`, `modules/ela.py`; tests; register them in `harness/run_module.py` REGISTRY
   (dict of run / applies / targets, see qr_verify entry); report the detection/FP table; STOP and report. Do not start Batch C.
3. Keep every convention: pure functions, no cross-module imports, config in YAML, Tier-4 never "hard", `analyze(...)-> list[Finding]`, fail-safe on bad input,
   CLI `python -m modules.<name> FILE`, docstring states input/output/what is NOT detected, honest numbers, never modify the corpus.

## Facts for Batch B (learned in Batch A)
- Use `import pymupdf` (v2.0), not `fitz`. Verify key names of get_texttrace/get_drawings/get_text("rawdict") before relying on them.
- Labels: spliced_expiry_forgery = I0010-fire_cert, I0045-fire_cert (must be detected, tier 1 per label); editor_metadata = I0003-infra_statement, I0051-infra_statement
  (creator 'ilovepdf.com' / 'Adobe Photoshop 25.2 (Windows)', ModDate months after created); exact_duplicate = I0002-fire_cert (<-I0008), I0047-fire_cert (<-I0012);
  visual_near_duplicate = I0064-land_doc (<-I0007, is_scan raster re-render; footer text still says SYN-I0007); copy_move_scan = 5 scans with mask PNGs (tier 4, soft only).
  Acceptance: detect every spliced + editor_metadata doc; ZERO findings on clean docs (harness clean = no non-control label, 728 docs).
- Spliced forgery anatomy (I0010): the original date text '26-Jul-2026' stays in the text layer; a WHITE (1,1,1) filled rect (drawn y~218) is drawn over it; then new text
  '03-May-2028' is drawn on top (bbox ~[205,217,268,231]). So covered_text = text span whose bbox is mostly inside a later-seqno near-white fill. Beware clean docs that
  legitimately draw white boxes/backgrounds; test on all clean docs and report any trigger, then fix logic/config and say what changed.
- pdf_meta dates in the manifest are not trustworthy as "in the future" signals; use ModDate-CreationDate gap + editor/producer names. Clean creators: SynthIssuer/{Crystal Reports 13.0, Apache FOP 2.6, JasperReports 6.20, iText 5.5.13, wkhtmltopdf 0.12.6, Microsoft Word 2019}.
  The corpus does NOT contain incremental-update streams yet (README), so unit-test %%EOF/Prev detection on a synthetic incrementally-saved PDF you create with pypdf/pymupdf.
- Every page has a SYNTHETIC watermark/footer: do not key on it. Dedup: exact = sha256 (also derived_from.donor_sha256 in manifest); near = pHash page 1 + MinHash text shingles;
  scans have no text layer so MinHash is empty for them (use pHash). Report similarity distribution of unrelated pairs to justify thresholds.
- Scans: image-only PDFs; page 1 raster via pymupdf. QR decode needs the Aruco detector on the native embedded image (see modules/qr_verify.py).
- Findings from the harness registry: label->finding-id mapping lives in harness/run_module.py TARGETS.

## Remaining roadmap (do not start unless asked)
Batch C: evidence_pdf, diff, ttl_purge. Later: templates_build, extract (needs OCR approval), rules (whitelist evaluator), adapters (retry/backoff), perspective, pipeline, api.
Known gaps: scans' printed date vs QR needs OCR; tests for fixture_server use label-derived findings only.
