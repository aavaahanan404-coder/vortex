# Batch F report (perspective, review, pipeline, api)

Test command: `python -m pytest tests -q` -> **346 passed, 1 xfailed** (corpus_v2); `python -m pytest tests -q --corpus corpus_test_v2` -> **336 passed, 1 skipped, 1 xfailed**
(held out). The one skip was my own test hard-coding which document is missing; fixed afterwards and `tests/test_pipeline.py` re-run on both corpora (12/12).
The xfail is deliberate and strict (a known perspective limit, section 1). New dependency: `python-multipart` (uploads). Environment as before.

## What was built
| file | purpose |
|---|---|
| `modules/perspective.py`, `config/perspective.yaml`, `harness/make_photo.py`, `harness/eval_perspective.py` | find the page in a phone photo, warp it flat; synthetic photo generator with known corners |
| `modules/review.py`, `config/review.yaml` | blind double-review state machine |
| `modules/pipeline.py`, `config/pipeline.yaml`, `harness/run_pipeline.py` | chains intake -> perspective -> quality -> extract -> type check -> layout -> forensics -> QR -> ELA -> dedup -> adapters -> rules -> score; `client_view()` / `staff_view()`; dry-run mode |
| `api/app.py`, `api/store.py`, `config/api.yaml` | FastAPI gateway (`/apply`, `/review`, `/admin`), sqlite store, hash-chained audit, 48 h drafts |
| tests | perspective 17 (1 xfail), review 8, pipeline 12, api 10 |
Small changes elsewhere: `extract.analyze(precomputed=)` and `adapters.analyze(result=)` (no second OCR / second registry call); `extract` now scales **image** uploads by the template page width (before, a photo's pixels were treated as points: a real bug for any non-PDF upload); `evidence_pdf` has a `blind` mode; `scoring.yaml` knows the new applicant-fixable ids.

## 1. perspective
Evaluated on **simulated** phone photos of the corpus's clean scans (`harness.make_photo`: random rotation +-15 deg, perspective, shadow, uneven light, blur, noise; 3000x2250 px; known corners). n is small and the photos are synthetic:
| | corpus_v2 (n=14, developed on) | corpus_test_v2 (n=12, held out) |
|---|---|---|
| page found | 14/14 | 12/12 |
| corner error (mean / worst, % of page diagonal) | 0.15 / 0.20 | 0.14 / 0.16 |
| similarity of the corrected page to the original scan | 0.93 (worst 0.90) | 0.95 (worst 0.91) |
| fields read correctly after correction / on the uncorrected photo | 104/117 (88.9%) / 0% | 85/88 (96.6%) / 2.3% |
The remaining OCR misses are blur in the simulated photo (the quality gate's territory), not geometry. How it got there (each step was driven by a measured failure): the first detector locked onto the photo frame; grey-level edges missed a dark page header on a wood table (fixed with Lab colour edges); corner precision needed line fitting along the edges; a flat full-page scan with a dark header was mistaken for a cut-off page; pure noise was accepted as a page (a page candidate must now be paper-bright inside).
**Known limit (strict xfail test):** when the page's dark header is exactly the colour of a dark table, the detector takes the header/body boundary as the top edge (it has real contrast), and the corrected page is cut short. Neither the proportions (foreshortening alone moves them 11%) nor edge contrast reveals it; it needs a template- or camera-aware check. **Real phone photos have not been tested** (glare, fingers, folds, curved pages, several pages in view): please supply some.
Findings: `perspective_corrected` / `perspective_not_needed` (info), `document_not_found` and `partial_document` (soft, Tier 4, applicant-fixable: "photograph the whole page flat on a plain background").

## 2. review (blind double review)
High risk (score >= 0.6 or `high_anomaly`) gets **two random, different reviewers** (assignment is reproducible per seed); each sees the evidence but **not** the machine score, category, reasons, per-finding probabilities, or the colleague's decision and notes. After both submit and agree: resolved. If they disagree, a **third, independent reviewer** (blind) decides; the first two now see each other; three different decisions go to a committee. The machine never decides (decisions are approve / reject / request_info; an unreviewed case has no outcome). Tested: random but reproducible assignment, no second vote, outsiders see nothing, state functions do not mutate their input.

## 3. pipeline: every institution end to end
`python -m harness.run_pipeline` processes the institutions in application-date order with one shared dedup index (a duplicate is flagged when it arrives), ~6 s per application on this machine (OCR of scans and the faculty lookups dominate).
| | corpus_v2 | corpus_test_v2 (held out) |
|---|---|---|
| clean institutions auto-cleared | 39/48 (81%) | 45/50 (90%) |
| clean institutions escalated to a person | 9: 4 by design (registry outage), 4 low OCR confidence on scans, 1 exact-duplicate donor | 5: 2 by design, 1 low OCR confidence, 2 near-duplicate donors |
| institutions with a seeded defect that reach a person | **49/52** | **48/50** |
| missed (auto-cleared) | 3 x copy_move_scan | 2 x copy_move_scan |
| client-view leaks / failed steps | **0 / 0** | **0 / 0** |
Per mutation (n = 2-6 institutions each, so weak evidence): every policy rule, QR, forgery, duplicate, registry and missing-document mutation is escalated in both corpora; `high_anomaly` for the hard-evidence ones (forgeries 0.98-0.99, bad QR, registry mismatches 0.90+), `needs_human` for the norm-based soft ones (pc_shortfall, short_lease, editor_metadata alone). Full tables: `reports/run_pipeline_corpus_*.md`.
Honest reading: the escalated "clean" institutions are mostly *correct* flags the ground truth does not count (donors of a labelled duplicate or near-duplicate: the later arrival is flagged, a person decides which is the original; registry outages that are scenarios by design). **The one real gap is `copy_move_scan`: 5 seeded scans per corpus, 2-3 institutions auto-cleared; nothing in the system detects copy-move** (ELA is blind on text pages: Batch B). It needs the TruFor / MVSS-style model from the spec (a model download and training data), not another heuristic.
**Dry-run** (applicant pre-check) runs only intake, perspective, quality, extract, layout, type check and the date / missing-document rules: no forensic detector, no registry, no dedup index, no audit entry. Tested: a forged application's dry-run reports `ready_to_submit`.
**Client view** is an allow-list: fixable problems in plain words (blurry, tilted, cut-off page, wrong document type, unreadable file, low-confidence values, expired certificate, missing document) and the pre-fill values. A covered-text field is shown as an ordinary confident value (a lowered confidence there would hint at tampering). Tested for the whole corpus: 0 leaks of score, category, forensic, registry or duplicate content.
Also: a document uploaded as the wrong type is detected (template coverage < 50% then another type >= 80%) and proposed back to the applicant; a failing step becomes an info `step_failed` and the rest continues (tested with a raising detector).

## 4. api
`uvicorn api.app:app_factory --factory`. Roles (tested end to end with FastAPI's TestClient on real corpus documents):
* **applicant** (own application only; responses are the client view): create application, upload per document type (versioned drafts, **48 h TTL**, pre-flight feedback on each upload), dry-run, submit. **Submission is refused with 409 while a fixable problem remains** (product decision: say if you would rather allow it); a second submit or a later upload is refused.
* **supervisor**: queue sorted by score, random assignment of reviewers, full results, v1-v2 diffs, evidence PDF.
* **reviewer**: only assigned cases; blind view (no score/category/reasons, findings without probability or hard/soft kind), evidence PDF rendered without the score row and without `p=`; decision endpoint enforces the state machine; after a disagreement the first two see each other, the tie-breaker does not.
* **admin**: 48 h purge (drafts only: submitted files are moved out of the purge area and survive; tested at 47 h and 48 h + 1 s), audit-chain verification.
Also tested: upload file names never become paths (stored under generated keys), unknown document type 422, oversized 413, unreadable file explained to the applicant, two applicants cannot see each other, the audit log is intact after the flow and records created / processed / submitted / assigned / decision / purge events, a request_info outcome reopens the application and a re-upload creates version 2 and a diff the applicant cannot read.

## 5. What this is not (read before deploying)
1. **Authentication is static bearer tokens in `config/api.yaml` (development tokens).** Put SSO / OAuth in front and replace the token file (`SIH_API_TOKENS`). No rate limiting, CSRF, TLS, antivirus scan of uploads or CORS policy.
2. One process, one sqlite file, one processing lock, synchronous processing (a submission takes seconds, a large faculty list longer). A deployment needs a job queue, PostgreSQL (the schema in `api/store.py` is tiny) and object storage with real TTL.
3. No notifications (email/SMS), no reviewer accounts management, no UI (the frontend is not part of this repository), no Unicode font in the evidence PDF.
4. Copy-move / splicing detection by a trained model (spec Phase 2) is the largest functional gap; TruFor needs weights and the 100-500 synthetic tampered pairs the spec mentions.
5. Real phone photos for perspective, handbook norms and clauses, `exact_duplicate` severity, pymupdf 2.0 and APScheduler remain open from earlier batches.

## 6. Decisions for you
1. Authentication scheme to integrate (the role model above stays).
2. Allow submission with fixable problems (with a warning) or keep refusing (current)?
3. Reviewers: should they see per-finding evidence messages in the blind phase? They do now (without probabilities), because they cannot review without evidence; the message text itself ("covered with a white box") signals seriousness.
4. Next biggest piece: the copy-move / forgery model (needs your approval to download weights) or the frontend?
