# Batch C report (near-duplicate fix + evidence_pdf, diff, ttl_purge)

Test command: `python -m pytest tests -q` -> **198 passed** (corpus_v2); `python -m pytest tests -q --corpus corpus_test_v2` -> **190 passed** (held out).
No xfails remain. Environment caveats as in `BATCH_B_REPORT.md` (Python 3.10, pymupdf 1.28.2 rather than 2.0).

## 1. Near-duplicates of re-scanned pages: fixed (closes the Batch B acceptance miss)

You asked for the best option. Option (a) from my report (compare extracted fields) needs OCR, and no Tesseract binary is installed here; installing system software is not something I did unasked.
So I built the best OCR-free equivalent: a **field-region channel** in `modules/dedup.py`.

How it works: for a document type, the boxes where the digital documents in the index *differ from each other* (per-pixel std across up to 40 of them, merged into text-line boxes) are the variable fields;
the fixed template is ignored. A scan is compared with each digital document of the same type only inside those boxes (background-normalised ink, best normalised cross-correlation within +-4 px, mean over boxes).
Nothing is stored per type: the boxes are learned from the index (>= 10 digital documents of the type needed). A digital query is checked against the scans of its type, so the donor sees the pair too.
Finding: `visual_near_duplicate`, soft, **Tier 4** (image heuristic, never hard), `evidence.method = "field_regions"`, `field_score`.

| | corpus_v2 | corpus_test_v2 (held out) |
|---|---|---|
| labelled re-scans (n=3 each, weak evidence): score against donor | 0.963-0.964, donor is rank 1 in 3/3 | 0.963-0.964, rank 1 in 3/3 |
| best score of every other scan (n=25 / n=22) | max 0.875, median 0.807 | max **0.930**, median 0.812 |
| `visual_near_duplicate` detected, with donor | **3/3** | **3/3** |
| `exact_duplicate` detected, with donor | 2/2 | 2/2 |
| clean-document false positives (dedup, all finding types) | **0/900** (train 0/635, val 0/87, test split 0/178) | **0/911** |

Data: `reports/dedup_field_scores_corpus_v2.md`, `..._corpus_test_v2.md`; full tables `reports/tables_batch_b.md`.
Honest caveats:
* **Threshold margin.** `min_field_score = 0.94` was fixed from corpus_v2 alone by a preset rule (worst unrelated scan 0.875 + 0.75 x the gap to 0.963), *before* looking at the held-out corpus. The held-out corpus then
  had one unrelated scan at 0.930 (a society_reg, 8 boxes): it passes, but with a margin of **0.01**. Types with few variable boxes (society_reg 8, eoa 6) are the weak spot; land_doc (21 boxes) is the strong case.
* The re-scans score 0.963-0.964 in both corpora with almost no spread: that looks like the ceiling of this generator's scan pipeline. A more heavily degraded real scan will score lower; expect recall to drop before precision does.
* A fresh upload test (the donor page re-rendered at 110 dpi with blur, noise and JPEG q70, never in the index) is matched to its donor; a different clean scan is not.
* Not handled: scan <-> scan pairs (donor is itself a scan), a scan of a page with edited fields (one changed field of 21 lowers the score by ~0.02), types with fewer than 10 digital documents in the index.
* pHash remains as the narrow copy detector (JPEG re-save, rescale, contrast), threshold 0.04, raster pairs only.
* Memory/time: the first scan of a type renders up to 40 digital documents to learn boxes, then crops for each candidate; about 20 s extra for a whole 965-document corpus. A production index should persist crops.

## 2. Batch C modules (none is a detector; not in the harness REGISTRY, tested directly)

### ttl_purge (`modules/ttl_purge.py`, `config/ttl.yaml`, `worker/purge_job.py`) - 13 tests
* `purge(store, now, ttl_hours=48, dry_run)` over any Store (`list()`, `delete(key)`); `FileStore` (sidecar `<key>.meta.json` with `created_at`, `kind`) and `MemoryStore` provided. `now` is a parameter: **the wall clock is never read** (CLI requires `--now`).
* Only `draft_upload` and `extraction_cache` expire; every other kind is protected; age >= 48 h expires (boundary tested at 47.99 / 48 / 48.01 h); future-dated entries and unreadable sidecars are kept and reported; a failing delete does not stop the run;
  keys escaping the store root are refused; files without a sidecar are never touched; TTL below 1 h is refused (a config typo cannot wipe live drafts).
* Returns audit-ready `events` for the caller to write to the append-only log (no database inside the module). `worker/purge_job.run_once` is the testable unit; `start()` uses APScheduler if installed (**not installed here**, optional) and otherwise raises, so a deployment cannot silently not purge.
* Not done: S3/Redis stores (two methods to write), extending a TTL on access.

### diff (`modules/diff.py`, `config/diff.yaml`) - 12 tests
* `compare` / `analyze` (info findings only: `diff_summary`, `diff_identical`, `diff_unreadable`): word-level text diff in reading order (faint watermark text ignored) plus a pixel diff of renders (covers scans and pasted images);
  lists **unchanged pages** (nothing to re-audit), changed pages, added/removed pages, text changes (replace/insert/delete with old and new boxes), visual regions in the newer page's points. `render_overlay` writes the red (only in v1) / green (only in v2) image.
* Real-data test: for each of the 5 incremental forgeries across the two corpora (3 in corpus_v2 tested; the held-out run covers its 2), revision 1 vs the final file reports the forged date as inserted text and a visual region within 15 pt of the `valid_to` field.
* Deliberately info only: a re-upload legitimately changes things. It does not see text hidden under a white patch (both layers keep it) - that is `pdf_forensics`. Pages are compared by number (a reordered upload looks entirely changed).

### evidence_pdf (`modules/evidence_pdf.py`, `config/evidence.yaml`) - 9 tests
* `build_evidence_pdf(case, out)`: cover (institution, score/category, documents), summary by tier, then one section per hard/soft finding (hard first): trust-ladder tier, probability, plain-language message, key evidence,
  the **page crop from the source PDF with the flagged box in red**, the ELA heatmap when `evidence.heatmap` exists, and the citation (check id such as F-QR / F-HIDDEN / F-DUP, or rule id with the rulebook's handbook clause). Info findings are only counted. Footer "STAFF ONLY - advisory".
* The shipped rulebook has `handbook_ref: TODO` everywhere, so reports say "handbook clause not yet mapped" until the regulator fills it (`case["rules"]` / `case["handbook_refs"]`). `F-REVISION`, `F-FONT`, `F-ELA` are my own internal check ids (the rulebook has none for them), marked as such in the output.
* Deterministic: the same case produces byte-identical PDFs (tested). A missing source file, hostile markup, 5,000-character evidence values and unknown finding ids do not break it. Checked visually on a forged certificate (hard QR mismatch, covered text with the date boxed).
* Limits: standard Latin-1 PDF fonts, so **Indic or other non-Latin institution names will not render** (embed a Unicode font before real data); no redaction, signing or embedding of the full source documents; one case per report.

## 3. Test inventory
corpus_v2 collection (198): audit 4, contract 9, dedup 18, diff 12, ela 11, evidence_pdf 9, fixture_server 4, harness 6, pdf_forensics 48, qr_verify 26, quality 31, score 7, ttl_purge 13. The held-out run (190) differs only in the number of parametrised cases.

## 4. Decisions / things to know
1. **Install Tesseract (you pre-approved it for `extract`)?** With text from scans, near-duplicate matching can compare real field values instead of pixels, and the 0.01 margin above goes away. I did not install it.
2. Unicode font for `evidence_pdf` before any real (non-Latin) data.
3. `pip install apscheduler` is needed only where the purge job actually runs.
4. Still open from Batch B: blur policy tolerant vs strict, `exact_duplicate` severity, pymupdf 2.0 re-run on your machine.
5. Remaining roadmap (not started): templates_build, extract, rules (whitelist evaluator), adapters (retry/backoff), perspective, pipeline, api.
