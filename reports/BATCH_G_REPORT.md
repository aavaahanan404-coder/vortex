# Batch G report: frontend (Next.js) and demo stack

Scope: the applicant portal (`/apply`), the review dashboard (`/review`, `/review/[inst]`), the admin page (`/admin`) and a seeded
demo server. Input: the FastAPI gateway of Batch F (plus five small endpoints added here). Output: `frontend/`, `harness/demo.py`,
`.claude/launch.json` (one folder above the repo root, where the app looks for it).

## What was built

| Area | Files | Behaviour |
|---|---|---|
| Stack | `frontend/` (Next.js 15.5, React 19, TypeScript 5 strict, Tailwind 3, lucide-react) | Browser calls `/backend/*`; `next.config.mjs` rewrites to the API (no CORS). Token in `sessionStorage`. |
| Sign-in | `src/app/page.tsx`, `src/lib/auth.tsx` | Pastes a bearer token, `GET /me` gives the role, redirect to `/apply`, `/review` or `/admin`. Every page redirects a wrong role. Dev-token warning is shown on the page. |
| Applicant | `src/app/apply/page.tsx`, `UploadCard.tsx` | Start form when no application exists; 10 document cards (drag-drop or button); per-card issues, read-back table of extracted values (low-confidence values highlighted "please check"), version, 48 h note; progress bar; "Check my application" (dry run); Submit with the 409 `action_needed` response shown as a fix list; status banners (draft / info requested / received / decision made). Applicants never see a score, category or forensic finding (the API does not send them; checked in the browser). |
| Photos | `CropTool.tsx`, `src/lib/quality.ts` | Instant in-browser blur / size warning; server finds the page corners (`/apply/photo/detect`), the person drags or arrow-keys four handles (2 px, Shift 10 px), server warps (`/apply/photo/warp`), preview, "Use this page" uploads the straightened PNG. |
| Review queue | `src/app/review/page.tsx` | Supervisor: queue by score with category, review phase, reviewers, Assign. Reviewer: own cases only. |
| Case viewer | `src/app/review/[inst]/page.tsx`, `PageViewer.tsx` | Document tabs, page images fetched with the token as blobs, SVG boxes in PDF points over each page (clicking a finding jumps to its page and highlights its box), findings per document and across documents, Tier-4 wording "a pointer, never proof", extracted fields with alternative values, earlier-version diff panel with overlay toggle, evidence PDF download, rules needing attention (supervisor). |
| Blind review | same | A reviewer sees no score, category, probability or hard/soft kind until their decision is recorded and the state machine reveals; banner says so. Decision panel approve / request information / reject + notes; cannot be changed. |
| Admin | `src/app/admin/page.tsx` | Purge preview and real purge (with a confirm), hash-chain audit verification. |
| Demo | `harness/demo.py` | In-process API on `data_demo/`, seeded from corpus_v2: SYN-I0031 (spliced date), SYN-I0010 (incremental edit), SYN-I0001 (rules defect) submitted, the first two assigned; SYN-I0064 a 4-document draft; SYN-I0003 with no application. Prints tokens (`demo-applicant-i0064`, `demo-reviewer-ana|ben|chi|dev`, `demo-supervisor`, `demo-admin`). |
| API additions | `api/app.py`, `tests/test_api.py` | `GET /me`, `POST /apply/photo/detect`, `POST /apply/photo/warp`, `GET /review/cases/{inst}/documents/{doc_type}/page/{n}.png`, page sizes in case views. 3 new tests. |

## Verification (what was actually done)

- `npx tsc --noEmit` clean; `next build` succeeds (7 routes, first-load JS 104-116 kB).
- Full backend suite after the API additions: `corpus_v2` 349 passed, 1 xfailed (the known dark-header perspective limit) in 5 min 20 s; held-out `corpus_test_v2` 340 passed, 1 xfailed in 4 min 55 s. There are no frontend unit tests; the frontend was checked by hand in the browser as listed below.
- In the built-in browser against the demo stack:
  - Supervisor: queue ordered 0.99 / 0.98 / 0.91 with Assign on the unassigned case; case SYN-I0031 shows the machine assessment and the fire certificate with the covered "Valid Until" date boxed in red on the rendered page, finding cards beside it.
  - Reviewer `dev` (blind): banner shown; page text contains no score, category, probability, "soft"/"hard"; decision recorded through the UI; the case stays blind while the second reviewer is outstanding (as the state machine requires).
  - Applicant SYN-I0064: sign-in redirect, progress "3 of 9", values read back, "Check my application" lists the expired fire certificate and the missing document; a synthetic skewed-page photo went through detect, handles, Straighten, Use this page, upload, OCR, and returned the soft message "some values could not be read reliably" (about 15 s for OCR).
  - Admin: purge preview and audit verify ("Intact: 13 entries verified").
  - Phone width (375 px): applicant page lays out in one column without horizontal scroll.
- Bugs found and fixed during this check: `bmp.close()` before reading its size (`quality.ts`); duplicate React keys from zero-contribution score reasons (now only reasons with contribution > 0 are listed); admin purge counts (`kept` / `protected` are numbers, not lists); a `never`-typed status banner map.

## Not verified / limits (read before relying on this)

- ~~The earlier-version diff panel was not exercised in the browser.~~ **Found broken, now fixed (Batch H follow-up):** when a document had been re-uploaded, the review page crashed ("Application error: a client-side exception") because it read the diff from the wrong level of the API payload (`finding.evidence`, not the top level). Fixed with a tolerant mapping, an app-level error screen, and a test pinning the payload shape; re-verified in the production build on a real re-uploaded case (tabs, findings, overlay).
- Crop handles were checked on a generated image with the detector's corners and the Straighten button; **dragging with mouse or touch and arrow-key movement were not driven**. Real phone photos were not tested (carried over from Batch F).
- No accessibility audit: labels, roles, aria text and focus styles are written in, but no screen reader or keyboard-only pass was run.
- Evidence PDF download was not clicked in the browser (the endpoint is covered by the API tests).
- The first sign-in after a cold `next dev` start did not redirect while `/review` was compiling; a second sign-in redirected correctly. Production build not exercised for this.
- Single-file uploads only; the PDF multi-page photo capture flow (several photos into one document) is not built.
- Auth is still a static token. SSO, session expiry and CSRF treatment need to be decided before any deployment. The "submit refused while fixable problems exist" policy (409) is unchanged.

## Still open from earlier batches

Copy-move / splice detector (the only systematic miss: `copy_move_scan`; classical Option A first, a trained model needs your approval and weight download), real phone-photo tests, handbook norms and clauses (all illustrative / TODO), `exact_duplicate` severity decision, Unicode font in the evidence PDF, pymupdf 2.0 re-run, APScheduler install, same-society informational finding. Wording note: the generic "a mandatory document is missing" message should name the document.

## Run it

    python -m harness.demo                    # API on :8000, seeded
    cd frontend && npm install && npm run dev # http://localhost:3000
