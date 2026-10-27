# Corpus v2 changes (generator: docgen.py)

Fixes
- Dates now in the past: application year 2026 (AY 2026-27, prior EoA AY 2025-26, financials FY 2024-25). PDF CreationDate/ModDate are capped below `--today` (default 2026-10-27). Override with `--app-year`, `--today`.
- Near-duplicate re-scans (`visual_near_duplicate`) now carry a `quality` block and scanner metadata (v1 left quality=null).
- Scan quality blocks gain `vol_norm1000` (variance of Laplacian after width-normalising to 1000 px). `variance_of_laplacian` stays the native 110-dpi value.

Additions
- New seeded defect `incremental_edit_forgery`: fire-certificate validity extended by an appended PDF incremental update (white patch + new text; 2 `%%EOF`, `/Prev`, original text still in the text layer). Labelled forensic, rule R016. Clean documents always have a single revision.
- `manifest_graded_blur.jsonl` + `documents_extra/graded_blur/`: 24 clean source documents x 9 blur levels (sigma 0-3), for quality-gate / OCR calibration. Kept out of the main manifest so the clean set is unchanged. Labels: kind "quality", name "graded_blur".
- `summary.json` has `corpus_version: 2`.

Two corpora
- `corpus_v2/` seed 26261, 100 institutions (build configs and calibrate here; split by inst_id).
- `corpus_test_v2/` seed 777, 100 institutions: HELD OUT. Never calibrate or build configs from it.

Notes
- Spliced / incremental forgeries: `fields.valid_to.value` is the forged visible value, `hidden_original` the original (QR and registry keep the original).
- Graded-blur finding: VoL ranges overlap across document types (sigma 1.0 spans 54-117 normalised; sigma 0.75 spans 119-232; clean spans 691+), so one VoL threshold separates mild blur only roughly. Calibrate on OCR accuracy.
