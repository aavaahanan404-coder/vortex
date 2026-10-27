# SIH26261 verification modules

Pure-function modules (file/JSON in, JSON out). Only `contracts.py` is shared. Tier-4 (ML/image heuristic)
findings are advisory and can never be `hard`. Corpus selection: `--corpus corpus_v2|corpus_test_v2|PATH`
(harness, pytest), env `SIH_CORPUS`, default `./corpus_v2`. `corpus_test_v2` is HELD OUT: evaluate on it,
never build or tune configs from it (the config builders refuse it).

    pip install -r requirements.txt                # pinned for Python >= 3.11; see "Environment" below
    python -m pytest tests -q                      # corpus_v2, ~2 min
    python -m pytest tests -q --corpus corpus_test_v2

| Module | CLI | Notes |
|---|---|---|
| quality | `python -m modules.quality FILE [--doc-type T] [--config c.yaml]` | blur (variance of Laplacian, per-doc-type threshold) + skew; `config/quality.yaml`; calibration: `python -m harness.calibrate_quality > reports/quality_calibration.md` |
| qr_verify | `python -m modules.qr_verify FILE --keys keys.json [--inst ID] [--doc-type T]` | keys: `python -m harness.corpus_io --dump-keys > keys.json`; `config/qr.yaml` (`qr_required_doc_types`, `qr_missing_on_digital`) |
| pdf_forensics | `python -m modules.pdf_forensics FILE [--doc-type T]` | covered_text, incremental_update, editor_metadata, font_anomaly, signed_content_changed; `config/forensics.yaml`; `covered_text_regions()` helper returns visible + hidden strings + bbox |
| dedup | `python -m modules.dedup FILE --against F1 F2 ...` | SHA-256 + pHash + MinHash + field-region match for re-scanned templated pages; `config/dedup.yaml`; `harness.dedup_stats` shows the pair-similarity distributions behind the thresholds |
| ela | `python -m modules.ela FILE [--heatmap out.png]` | mechanics only (not evaluated on tamper data); `config/ela.yaml` |
| ttl_purge | `python -m modules.ttl_purge ROOT --now ISO [--dry-run]` | 48 h TTL of draft uploads / extraction caches; Store protocol (FileStore, MemoryStore); never reads the wall clock; `config/ttl.yaml`; scheduler: `worker/purge_job.py` (APScheduler optional) |
| diff | `python -m modules.diff OLD NEW [--overlay-dir D]` | v1 -> v2 text + visual diff, unchanged pages listed, red/green overlay PNG; info findings only; `config/diff.yaml` |
| evidence_pdf | `python -m modules.evidence_pdf CASE.json OUT.pdf` | staff-only audit PDF: flagged boxes cropped from the source, heatmaps, check / rule citations; deterministic bytes; `config/evidence.yaml` |
| templates_build | `python -m harness.build_templates` (builder); `check_layout()` in the module | per-type templates from train-split documents -> `config/templates.yaml`; layout findings (shifted / missing / overlap / out of bounds) |
| extract | `python -m modules.extract FILE --doc-type T [--force-ocr]` | text layer or Tesseract (needs the binary: `winget install UB-Mannheim.TesseractOCR`; auto-detected); ExtractedDoc with per-field conf; `low_ocr_confidence`; `config/extract.yaml` |
| adapters | `python -m modules.adapters FIELDS.json --doc-type T --inst ID --date D --world world.sqlite` | six-state registry verification with exponential-backoff retry; `WorldSource` = mock authorities over the corpus world DB (scenario flags); `registry_facts()` builds the registry view for the rules; `config/adapters.yaml` |
| rules | `python -m modules.rules FACTS.json [--rulebook r.yaml]` | simpleeval whitelist evaluator over `config/rulebook.yaml` (R001-R016) and canonical facts (`build_facts`); results pass / fail / not_applicable / not_evaluable; `simulate()` = rule-change impact on stored facts; `config/norms.yaml` are ILLUSTRATIVE, every `handbook_ref` is TODO |
| rule_import | `python -m modules.rule_import FILE.pdf` (preview) / `--template OUT.pdf` | structured rule-update PDF (a Norms table and/or a Rules table) -> checked change plan (each value up / down) -> versioned overrides of the norms and rule settings (kind, severity, p, on/off, clause); unstructured PDFs refused (AI import is later). Used by `/admin/rules` in the API and the admin page; `pipeline.rule_change_impact` previews the effect on stored cases. See `reports/BATCH_H_REPORT.md` |
| perspective | `python -m modules.perspective FILE [--out flat.png]` | page found in a phone photo (colour + brightness edges, line-fitted corners) and warped flat; `partial_document` / `document_not_found` are applicant-fixable; known limit: a dark header on a dark table; `config/perspective.yaml`; evaluation `harness.eval_perspective` |
| review | library: `open_case / assign / submit / view` | blind double-review state machine (two random independent reviewers, scores and notes hidden until both submit, third reviewer on disagreement, committee if all differ); `config/review.yaml` |
| pipeline | `python -m modules.pipeline APP.json --world world.sqlite [--dry-run] [--view client]` | the one module that chains the others: intake, perspective, quality, extract, forensics, QR, ELA, dedup, adapters, rules, score; `client_view()` / `staff_view()`; dry-run mode shows the applicant only fixable problems; `config/pipeline.yaml` |
| api | `uvicorn api.app:app_factory --factory` | FastAPI: `/apply` (applicant), `/review` (reviewer, supervisor), `/admin`; sqlite store, hash-chained audit, 48 h drafts; dev tokens in `config/api.yaml` (replace before any deployment) |
| frontend | `cd frontend && npm install && npm run dev` (needs the API on :8000; `npm run build` for production) | Next.js 15 / React 19 / Tailwind: `/` sign-in, `/apply` (upload, photo straightening, soft errors, dry run, submit), `/review` (queue, blind case viewer with bounding boxes, decisions, evidence PDF), `/admin` (purge, audit check). Browser calls `/backend/*`, proxied to the API by `next.config.mjs` (`API_URL`, default `http://127.0.0.1:8000`). See `reports/BATCH_G_REPORT.md` |
| demo | `python -m harness.demo [--port 8000] [--keep]` | the API in-process on a fresh `data_demo/`, seeded with five corpus_v2 institutions (submitted / assigned / draft / empty) and printed demo tokens; `start_demo.bat` starts API + web app and opens the presenter page `/demo` (`demo_assets/demo.html`: one-click role sign-in, talking script); see `DEMO.md` |
| score | `python -m modules.score findings.json` | `config/scoring.yaml` |
| audit | library: `AuditLog(sqlite3_conn)`; SQL in `modules/audit_trigger.sql` | no global connection |

Generated configs (never edit by hand; builders use the corpus_v2 TRAIN split, clean documents only):

    python -m harness.build_font_allowlist          # config/fonts_allowlist.yaml
    python -m harness.build_dedup_boilerplate --min-df 0.05   # config/dedup_boilerplate.yaml

Harness:
- `python -m harness.run_module MODULE [--corpus C] [--split all|train|val|test]` : per-label detection with n, false
  positives on clean docs by finding id. MODULE in quality, qr_verify, pdf_forensics, dedup, ela.
- `python -m harness.batch_report pdf_forensics dedup ela --out reports/tables_batch_b.md` (and `quality qr_verify --out reports/tables_batch_a_on_v2.md`) : tables per split and corpus.
- `python -m harness.run_pipeline [--corpus C]` : every institution end to end, category vs seeded defects, client-view leak check.
- `python -m harness.run_rules [--corpus C] [--ignore-scenarios] [--verbose]` : adapters vs manifest `expected_adapter`, rules vs `expected_rules` (all 16 rules x 100 institutions).
- `python -m harness.eval_extract [--corpus C --split S --source digital|scan]` (extraction accuracy), `python -m harness.calibrate_blur_ocr` (OCR vs blur).
- `python -m harness.dedup_stats`, `python -m harness.dedup_field_stats`, `python -m harness.ela_stats` : similarity / score distributions behind the thresholds.
- `uvicorn harness.fixture_server:app` : fake applications/queue/findings derived from corpus *labels* (not detector output).

Splits: `split_of(inst_id)` = md5(inst_id) 70/15/15 train/val/test. The graded-blur set is a separate manifest
(`manifest_graded_blur.jsonl`) and never counts as clean documents.

## Environment
`requirements.txt` pins pymupdf 2.0 / numpy 2.4 (Python >= 3.11). The Batch B session ran on Python 3.10 with
the nearest installable versions (`reports/environment_py310_pip_freeze.txt`: pymupdf 1.28.2, numpy 2.2.6,
opencv-python-headless 4.13.0.92, pypdf 5.9.0). The `get_texttrace` / `get_drawings` keys used by
`pdf_forensics` (`seqno`, `bbox`, `chars`, `font`, `opacity`, `type`, `dir`, `fill`, `fill_opacity`, `rect`) were
checked on 1.28.2 only: re-run `python -m pytest tests/test_pdf_forensics.py` under pymupdf 2.0.

OCR: optional; without Tesseract scans yield `ocr_unavailable` (info) and OCR tests skip. See `reports/BATCH_D_REPORT.md`.

Known limits: see `reports/BATCH_A_REPORT.md`, `reports/BATCH_B_REPORT.md`. Do not key detectors on the synthetic watermark.
