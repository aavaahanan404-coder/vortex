# Batch A report

Test command: `python -m pytest tests -q` (56 tests; last full run 55 passed + 1 added test passed).
Harness: `python -m harness.run_module quality` / `qr_verify`.

## Built
contracts.py (+schema), harness/{corpus_io,run_module,fixture_server,calibrate_quality}.py,
modules/{quality,qr_verify,score,audit}.py + audit_trigger.sql, config/{quality,scoring}.yaml, tests, README.

## Detection / false-positive table (run_module.py)
| module | label | detected | n | rate |
|---|---|---|---|---|
| quality | scan_degraded | 5 | 5 | 100% |
| qr_verify | qr_bad_signature | 4 | 4 | 100% |
| qr_verify | qr_date_mismatch | 2 | 2 | 100% |
| qr_verify | spliced_expiry_forgery (via date mismatch) | 2 | 2 | 100% |
| qr_verify | certificate_reuse (via qr_wrong_institution, bonus) | 2 | 2 | 100% |

False positives on clean docs: quality 0/728 (includes 8 scan_clean controls and 755 digital pages);
qr_verify 0/198 clean QR-bearing docs. Misses CSVs are empty (reports/misses_*.csv).

## Caveats / what did not fully work
- QR on scans: native-image extraction + OpenCV Aruco detector decodes all clean/copy-move/skewed scans
  (the first approach, PyMuPDF raster + plain detector, failed on 8 of 9). The 3 blur-sigma>=2 scans
  do not decode and are reported as qr_absent (soft, detected_but_undecodable evidence when a quad was seen).
  Scans have no text layer, so the printed-date comparison is skipped (qr_date_unchecked); needs OCR (later `extract` batch).
- Quality blur threshold (120) is a judgement call inside the feasible interval (14.9, 343.2): corpus has no mild blur.
  Skew threshold 5 deg sits in an empty gap (1.0, 7.0). Calibration was done on all labelled scans (no held-out set).
- Design fix found by test: two re-scan requests noisy-OR to 0.84; category high_anomaly therefore ignores
  applicant-fixable soft findings (reported score still includes them).
- Text-layer date extraction is fooled by cover-up forgeries (original text stays under a white rectangle);
  extractor takes the last (visible) date. Batch B pdf_forensics must report the cover-up itself.
- fixture_server findings come from corpus labels, not detector output.

## Corpus notes (not fixed; corpus untouched)
- SYN-I0064-land_doc: is_scan=true but quality=null.
- Label `quality.variance_of_laplacian` is at native 110 dpi (different scale from width-normalised VoL).
- manifest pdf_meta created/modified dates are around/after the current date (2026-10 .. 2027-12) for many docs.
- Manifest `fields.valid_to` of spliced docs holds the forged printed value; QR/registry hold the original.

## Decisions for you
1. Accept vol_min=120, skew_max=5? Or generate graded-blur scans to calibrate properly.
2. Should qr_wrong_institution (valid signature, other institution's cert) be a hard Tier-1 finding? (implemented as hard, p=.9).
3. Should qr_absent on an undecodable scan be soft/p=.3 (current) or route straight to needs_human/applicant_fixable (currently applicant_fixable)?
4. Is OCR (pytesseract/model download) allowed for the later `extract` batch? Needs your approval per the prompt.
