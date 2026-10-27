# Batch B report (corpus v2 migration + pdf_forensics, dedup, ela)

> **UPDATE (Batch C session): the `visual_near_duplicate` acceptance miss in section 3.2 / 4 is closed.** A field-region channel now finds
> 3/3 (corpus_v2) and 3/3 (corpus_test_v2) re-scans with their donors and still links no unrelated document (0/900, 0/911): see `BATCH_C_REPORT.md` section 1
> (including its thin 0.01 held-out margin). Everything below is the report as written at the end of Batch B. The 3 xfailed tests mentioned are now real passing tests.


Test command: `python -m pytest tests -q` (corpus_v2: **159 passed, 3 xfailed**) and
`python -m pytest tests -q --corpus corpus_test_v2` (held out: **152 passed, 3 xfailed**; the held-out corpus has fewer labelled documents, so fewer parametrised cases). The 3 xfails are the known visual-near-duplicate gap (see 3.2), marked `strict`.
Full tables: `reports/tables_batch_b.md` (`python -m harness.batch_report quality qr_verify pdf_forensics dedup ela`).
Environment caveat: **Python 3.10, pymupdf 1.28.2, not the pinned pymupdf 2.0** (the pinned stack needs Python 3.11+, only 3.10 is installed
and no download was made). `get_texttrace` / `get_drawings` keys were verified on 1.28.2 only; please re-run
`tests/test_pdf_forensics.py` under 2.0. `docgen.py` needs `pdftoppm` (not installed), so **no extra data was generated**:
everything below is on corpus_v2 and corpus_test_v2 only, and n is small throughout.

## 1. What was built / changed

| area | files |
|---|---|
| migration | `harness/corpus_io.py` (switchable corpus, `split_of`, `iter_graded`, `first_doc`, `institutions`), `harness/run_module.py` (`--corpus`, `--split`, per-finding FP table, n on every row), `conftest.py` (`--corpus`), all Batch A tests rewritten without hard-coded ids or counts |
| quality | per-doc-type thresholds (`vol_min_by_doc_type`), `harness/calibrate_quality.py`, `config/quality.yaml`, `reports/quality_calibration.md` |
| qr_verify | `config/qr.yaml` (`qr_required_doc_types`), `qr_missing_on_digital`, visible-date extractor fixed (see 2.2) |
| pdf_forensics | `modules/pdf_forensics.py`, `config/forensics.yaml`, `config/fonts_allowlist.yaml` (generated), `harness/build_font_allowlist.py` (train split only; refuses held-out corpus) |
| dedup | `modules/dedup.py`, `config/dedup.yaml`, `config/dedup_boilerplate.yaml` (generated), `harness/build_dedup_boilerplate.py`, `harness/dedup_stats.py` |
| ela | `modules/ela.py`, `config/ela.yaml`, `harness/ela_stats.py` |
| harness | REGISTRY entries `pdf_forensics`, `dedup` (corpus-wide index via `prepare`), `ela`; `harness/batch_report.py`; `tests/test_pdf_forensics.py` (48), `test_dedup.py` (16), `test_ela.py` (11), `test_harness.py` (6) |

## 2. Migration to v2 (task 0)

### 2.1 Batch A modules on both corpora (n = labelled documents per row; every n < 10 is weak evidence)
| module | label | corpus_v2 | corpus_v2 test split | corpus_test_v2 |
|---|---|---|---|---|
| quality | scan_degraded | 6/6 (n=6, weak) | 1/1 (n=1, weak) | 5/5 (n=5, weak) |
| qr_verify | qr_bad_signature | 6/6 (n=6, weak) | 2/2 (n=2, weak) | 5/5 (n=5, weak) |
| qr_verify | qr_date_mismatch | 3/3 (n=3, weak) | 2/2 (n=2, weak) | 3/3 (n=3, weak) |
| qr_verify | spliced_expiry_forgery (via date mismatch) | 3/3 (n=3, weak) | n=0 | 3/3 (n=3, weak) |
| qr_verify | incremental_edit_forgery (via date mismatch) | 3/3 (n=3, weak) | 2/2 (n=2, weak) | 2/2 (n=2, weak) |
| qr_verify | certificate_reuse (via qr_wrong_institution) | 2/2 (n=2, weak) | n=0 | 2/2 (n=2, weak) |

Clean-document false positives: quality **0/900** (corpus_v2), 0/178 (test split), **0/911** (corpus_test_v2); qr_verify **0/242**, 0/48, **0/248**
(QR-bearing clean documents). The graded-blur set is excluded from "clean".
`qr_absent` fires on labelled blur scans only (2 of the 5 blur scans in corpus_v2, 3 of 3 in corpus_test_v2: sigma >= 2 scans whose QR cannot be decoded, as decided); never on a clean document.

### 2.2 Regressions and surprises, explained
1. **Real Batch A bug exposed by the new label.** `printed_date_from_text` took "the last date line after the label". For
   `incremental_edit_forgery` the forged date is appended in a later content stream and is *not adjacent* to its label in stream order,
   so the extractor returned the ORIGINAL date, which equals the QR date: `qr_date_mismatch` would have stayed silent on every
   incremental forgery. Fixed: take every date-looking span on the label's line and return the one drawn last (draw order,
   `get_texttrace` seqno). Covered by `test_date_mismatch` / `test_cli_extraction_uses_visible_date...` for both forgery labels. This is exactly the
   "extract must know about cover-ups" problem; `pdf_forensics.covered_text_regions()` is the helper for it.
2. Tests were hard-coded to v1 (ids, 80 applications, I0010...); all now derive from the manifest. No detector regressed.
3. My render-based VoL differs from the manifest's `vol_norm1000` (median |diff| 12, max 107, bigger for blurred scans) because the
   page is re-rendered through mupdf; thresholds are calibrated on **my** measure, not the label.

### 2.3 Quality recalibration (0c) - what is and is not separable
Graded set, calibrated on corpus_v2 **train** sources (17 sources; val has **1** source, test split 6, held-out corpus 24), reported on test and corpus_test_v2.
VoL (width-normalised 1000 px) per blur level, corpus_v2 / corpus_test_v2 (min..max): sigma 0: 740..1520 / 743..1496; 0.5: 387..753 / 388..789;
**0.75: 124..230 / 124..271; 1.0: 52..105 / 53..127**; 1.25: 24..55 / 24..63; 1.5: 13..26 / 15..30; 2.0: 6..20 / 6..21; >=2.5: 3..16. Per doc type see
`reports/quality_calibration.md` (one row per type x level).
* Separable: sigma <= 0.5 vs sigma >= 1.25 by orders of magnitude; sigma 0.5 never fails and sigma >= 1.25 always fails at any threshold between 63 and 387.
* Borderline: sigma 0.75 and sigma 1.0 overlap **across document types** (an eoa at sigma 1.0 reaches 127; a society_reg at sigma 0.75 starts at 124), so a
  single global threshold (114, train-derived) separates corpus_v2 only by an 18-unit gap and **misses 2 of 24 sigma-1.0 files on corpus_test_v2**
  (the fixed 120 of Batch A misses the same 2). **Per-type thresholds** (affidavit 108, building_plan 104, eoa 153, fire_cert 105, society_reg 88;
  fallback 114) classify all graded files on train, test split and corpus_test_v2 (24/24 at sigma 1.0). Weak points: eoa is derived from **n=1** source;
  land_doc and the other 5 types have no graded files and use the global fallback.
* Policy, not data: I chose "tolerant" (sigma <= 0.75 passes, matches the generator's `expected_gate` for 21/24). The "strict" alternative (sigma 0.75 fails)
  puts the global threshold at ~294 (affidavit 330, society_reg 231 ...), 10% below the weakest clean real scan (326): see decision 1.
* Gate check on the main manifests: all 8 blur `scan_degraded` scans are flagged blurry (5 + 3; VoL 6..18); the 3 skew-only scans (VoL 233, 258, 403) pass the blur check and are caught by skew;
  clean, copy-move and near-duplicate scans (n=14+12, 5+5, 3+3; VoL 244..805): 0 flagged blurry or tilted; 1881 digital pages (min VoL 1396): 0 flagged.
* Final calibration against OCR accuracy is still the `extract` batch's job.

### 2.4 `qr_missing_on_digital` (0d)
`config/qr.yaml`: `qr_required_doc_types: [building_plan, fire_cert, eoa]`, `p_qr_missing_on_digital: 0.5`. A **digital** PDF (text layer) of such a type with no decodable QR
gives one soft Tier-3 finding `qr_missing_on_digital` (instead of `qr_absent`); scans keep `qr_absent` (soft, applicant fixable); other types and callers
that pass no `doc_type` are unchanged. The corpus has **no** clean digital document of those types without a QR, so the test builds one by painting a white box over a real QR
(plus config-switch, scan and unrelated-type tests). FP on corpus: 0.

## 3. Batch B

### 3.1 pdf_forensics (soft, Tier 3 throughout; docstring lists what is NOT detected)
Findings: `covered_text` (p 0.9; 0.75 if nothing was drawn on top), `incremental_update` (0.5; 0.25 if no page content changed),
`signed_content_changed` (0.85, /ByteRange only, no PKI), `editor_metadata` (0.6 tool name, 0.35 date gap > 7 days, 0.7 both), `font_anomaly` (0.6).
Detection / false positives (clean = no non-control label; n = labelled documents):

| label | corpus_v2 (all) | corpus_v2 test split | corpus_test_v2 |
|---|---|---|---|
| spliced_expiry_forgery | 3/3 (n=3, weak) | n=0 | 3/3 (n=3, weak) |
| incremental_edit_forgery | 3/3 (n=3, weak) | 2/2 (n=2, weak) | 2/2 (n=2, weak) |
| editor_metadata | 3/3 (n=3, weak) | n=0 | 2/2 (n=2, weak) |
| **clean-document false positives** | **0/900** | **0/178** | **0/911** |

Clean-document false positives **by finding type**: covered_text 0, incremental_update 0, editor_metadata 0, font_anomaly 0, signed_content_changed 0
(train 0/635, val 0/87 as well). Beyond "fires": every one of the 11 forged files reports `hidden_text` equal to the manifest's `hidden_original` and `visible_text` equal to
the forged value; `covered_text` bbox IoU with the `valid_to` field 0.77..0.81; on the 5 incremental files **both** `incremental_update` (2 revisions, `/Prev`, added text = forged date,
bbox IoU 0.54..0.56 because it is the whole white patch) and `covered_text` fire. Spliced/incremental documents also fire `font_anomaly` (Courier-Bold) and, for spliced, `editor_metadata`
(their creators are editing tools) - corroborating signals, not false positives.
**Every trigger on clean documents (asked for): none.** Of the 1,811 clean documents (both corpora) **not one contains a near-white filled shape at all** (all 11 white fills in the two corpora
are in the 11 forged files), all have exactly one revision, and the only fonts are Helvetica(-Bold) and Times(-Roman)(-Bold) (Courier-Bold appears only in forged files). So the corpus gives **no
evidence about legitimate white boxes**; that risk is tested synthetically instead (`test_legitimate_white_shapes_stay_silent`: box before text, box elsewhere, page-size background drawn
afterwards, identical redraw, 18%-opacity watermark under a box, off-white box, stroke-only; I checked each guard fails open when relaxed). Logic changes made along the way: (a) text
replaced by a glyph that sticks out of the patch was truncated ("08-Jan-202") -> spans mostly inside the patch are reported whole (`span_dominated_frac`); (b) guards listed above came from the
synthetic legitimate cases, not from corpus failures. Generalisation: a synthetic cover-up of a value on a clean document of **each of the 10 document types** is detected.
Config: font allow-list from train-split clean digital docs (10 types, 2-4 fonts each), `moddate_gap_days: 7` (clean = 0 days, editor documents = 20..200 days, so 1..19 are equally supported),
all other numbers are priors, not fitted. Editor names other than the 4 in the corpus (ilovepdf, sejda, photoshop, phantompdf) are unvalidated domain knowledge.

### 3.2 dedup (exact 0.8 soft T3; text near-dup 0.5 soft T3; visual near-dup 0.5 soft **T4**)
| label | corpus_v2 (all) | corpus_v2 test split | corpus_test_v2 |
|---|---|---|---|
| exact_duplicate | 2/2 (n=2, weak), donor named | n=0 | 2/2 (n=2, weak), donor named |
| visual_near_duplicate | **0/3 (n=3, weak)** | **0/1 (n=1, weak)** | **0/3 (n=3, weak)** |
| clean-document false positives | 0/900 | 0/178 | 0/911 |

Exact: each duplicate's evidence lists its `derived_from` donor and the donor reports the pair back (each end is flagged; the unlabelled donor end is counted as a *correct link*, 2 per corpus, not as an FP).
No unrelated documents are linked (0 clean-document findings other than those 4 endpoints).

**Acceptance miss: `visual_near_duplicate` is not detected (0/3 and 0/3).** Why, with the numbers (all in `reports/dedup_stats_*.md`):
* Same-template pages are almost identical to a page hash. Unrelated same-type **digital** pairs have normalised pHash distance min 0.008-0.012, p1 0.033, median 0.10. At the original
  16x16 hash size they collide at 0.000. The re-rendered scans sit at **0.082-0.098** from their donors (corpus_v2: 0.086, 0.094, 0.096; corpus_test_v2: 0.082, 0.092, 0.094).
* Unrelated pairs involving a scan reach down to 0.057 (corpus_v2) / 0.055 (corpus_test_v2) and the 1st percentile of mixed scan/digital pairs is 0.092. Catching the donors needs T = 0.10, which links
  **76 / 89 unrelated mixed pairs** and flags **72 of 900 / 90 of 911 clean documents (8/14 and 9/12 clean scans)**, donors included. Trade-off table: `reports/dedup_stats_corpus_v2_all.md`, `..._test_v2_all.md`.
* I tried (and rejected, details available) a background-normalised hash, ink-containment, and a template-subtracted residual correlation: the donor is rank 1 among digital candidates in 6/6 cases but
  by margins of 0.01-0.18 that I cannot defend with 6 positives (and unrelated scans reach correlation 0.91).
* Shipped: pHash 32x32 (1024 bits), only for pairs where at least one page is raster, **T = 0.04**: measured on 26 clean scans it catches JPEG re-saves (max distance 0.027), re-scaling (0.023) and contrast changes
  (0.016) with 0 unrelated links (margin to the closest unrelated pair 0.015). It does **not** catch brightness offsets that clip the background (+18 levels: median 0.052) or any rotation (0.5 degrees: 0.11).
* The real fix is a field-level comparison (document no., survey no., area: the `near_duplicate_land` policy rule does exactly this on extracted fields) after `templates_build` / `extract`.

Text channel (MinHash, 128 perms, 3-word shingles): raw text is useless here (unrelated same-type pairs: median Jaccard 0.98 for land_doc, many exactly 1.0 - one template). The module therefore drops
shingles that are boilerplate for the document type (df >= 5% of train-split clean docs; `config/dedup_boilerplate.yaml`). Unrelated same-type max Jaccard: 0.602 (train), 0.656 (held-out);
`min_jaccard` = 0.75 (chosen above the worst unrelated pair seen, **not** fitted to positives: the corpus has no labelled text near-duplicate). Estimator validated against true Jaccard
(0.00/0.20/0.50/0.79/1.00 -> 0.00/0.23/0.52/0.79/1.00); the first implementation (a*x+b mod p) was **wrong** (one shared tiny hash decided every permutation, Jaccard 0.02 estimated as 0.98) - caught by the pair statistics, now a regression test.
A re-saved copy with new metadata (different bytes) is reported as `text_near_duplicate`, not exact (test).

### 3.3 ela (mechanics only)
Re-save at quality 90, |difference| (max over channels), auto-gain (p99.9 -> 255), JET heatmap PNG, summary score = max robust z over content blocks (16 px), `ela_suspicious` soft **Tier 4** above a placeholder score of 18.
Unit tests cover error-map properties, re-save stability, amplification/heatmap shape, localisation of a pasted never-compressed patch on a synthetic photo (bbox on the patch), flat page, scan (native image) vs
digital (150 dpi) input, bad inputs, CLI. **Not evaluated on tamper data, and it will be weak on text documents**: score distribution (`reports/ela_scores_*.md`):

| group (corpus_v2) | n | median | p95 | max |
|---|---|---|---|---|
| clean digital | 886 | 9.7-9.8 | 13.5 | 15.0 |
| clean scan (train split values) | 12 | 5.7 | 7.8 | 8.8 |
| covered-text forgeries | 6 | 9.6 | 9.7 | 9.7 |
| copy_move_scan (n=5, weak) | 5 | 5.6 | 6.0 | 6.1 |

Forged pages score like clean pages. The threshold (18 = 1.2 x train-clean max) therefore yields 0 clean false positives (0/900, 0/178, 0/911) *by construction* and says nothing about detection.

## 4. Acceptance status
| criterion | status |
|---|---|
| detect every spliced / incremental / editor_metadata document in both corpora | **met** (11/11 forged, 5/5 editor; n small) |
| zero findings on clean documents (table by finding type) | **met** (0 on 900 + 911; train 635 and val 87 too) |
| dedup finds every exact_duplicate with its donor, links no unrelated documents | **met** (4/4) |
| dedup finds every visual_near_duplicate with its donor | **NOT met** (0/6): see 3.2 |
| configs from corpus_v2 train only, tuned on val, reported on test + corpus_test_v2 | met, with caveats: font allow-list and boilerplate are train-built (builders refuse the held-out corpus); val is tiny (94 documents, 1 graded source, 1 spliced + 1 editor_metadata document, no incremental forgery), so "tuned on val" was a sanity check only |

## 5. Decisions for you
1. **Blur policy.** Keep "tolerant" (sigma 0.75 passes; per-type thresholds above) or "strict" (sigma 0.75 fails; global ~294, near the weakest clean scan)? Needs OCR accuracy; I recommend tolerant until `extract` measures it.
2. **Visual near-duplicates of re-scanned templated pages.** (a) leave the shipped narrow detector and catch these through extracted fields (`near_duplicate_land`) after `extract`; (b) accept ~8% clean-document link noise at T=0.10 for scan-involving pairs, advisory Tier 4 only; (c) ask the generator for scans with distinct content per template (or provide `pdftoppm` so I can generate 400+ institutions for a real threshold). I recommend (a).
3. **Severity of `exact_duplicate`.** Soft p=0.8: byte-identical certificates across institutions are certain, but a trust running two colleges can share a society registration legitimately. Hard Tier 1 would need a rule per document type.
4. Run `tests/test_pdf_forensics.py` under pymupdf 2.0 on your machine, and tell me if the key names differ.
5. Per-type eoa blur threshold rests on one train source; regenerate with a larger graded set if you want it firmer (`python corpus_v2/docgen.py --n 400 --seed 99 --out corpus_big --graded-sources 120`, held out unless you say it is for tuning).

Not started: Batch C.
