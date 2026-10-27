# Running the demo

## Start: one double-click
Double-click **`start_demo.bat`** (or run `.un_demo.ps1` in PowerShell). It starts the API and the web app in two windows, waits until everything is ready (about 1.5 minutes: the demo data is prepared and the web app is built), and opens the **Demo hub** in your browser.

The **Demo hub** (`http://localhost:3000/demo`) is the one page you present from:
- a green "System: ready" light;
- **Open as...** buttons: one click opens a new tab already signed in as the supervisor, a reviewer, an applicant or the admin (no tokens to type);
- the five-minute story, step by step, with what to say and what to click, and tick boxes to keep your place;
- the "be upfront" list of limits, the example rule PDF to download, and quick fixes.

Later starts: `start_demo.bat -Keep` keeps the data of the last run. Close the two windows to stop. To show it on other devices on the same Wi-Fi, give them the address the script prints (`http://<your-ip>:3000`; allow Node.js through the firewall when asked). They sign in with the tokens below. The tokens are static: use a private network, never the open internet.

### Without the script (the hub is then at the same address, /demo)
```powershell
.venv\Scripts\python.exe -m harness.demo --port 8000          # window 1 (add --keep to reuse data)
cd frontend; npm run build; npx next start -H 0.0.0.0 -p 3000 # window 2 (use "npm run dev" for development)
```

### First time on a new computer
Python 3.10+ and Node 20+; then `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt` (the pinned pymupdf 2.0 needs Python 3.11+; on 3.10 install pymupdf 1.28.2), `cd frontend; npm install`. Scanned-document OCR needs Tesseract (`winget install UB-Mannheim.TesseractOCR`); without it scans just report that OCR is unavailable.

## Sign in
Paste a token on the first page; each token is one role.

| Role | Token | What you see |
|---|---|---|
| Supervisor | `demo-supervisor` | review queue with scores, Assign button, full case view |
| Reviewer | `demo-reviewer-dev` (also `-chi`, `-ben`, `-ana`) | only assigned cases, **blind** (no score) |
| Applicant, draft with problems | `demo-applicant-i0064` | 3-4 documents uploaded, expired fire certificate, missing documents |
| Applicant, empty | `demo-applicant-i0003` | "Start your application" form |
| Applicant, already submitted | `demo-applicant-i0031` (also `i0010`, `i0001`) | "received" banner |
| Admin | `demo-admin` | rules changer, purge, audit check |

Cases in the queue: SYN-I0031 (expiry date covered and replaced, reviewers dev + chi), SYN-I0010 (edited after signing, reviewers chi + ben), SYN-I0001 (rules defect, not yet assigned).

## Which files to upload
Everything is ready in **`demo_assets/sample_uploads/`** (the Demo hub shows this table and the folder path with a Copy button). Details are in that folder's `README.txt`; to rebuild it: `python -m harness.make_demo_samples`.

| Where | Upload | What it shows |
|---|---|---|
| Applicant: new (`demo-applicant-i0003`), the start form first | form values: Bharathi College of Engineering, 2026-01-14, Existing institution, Engineering | |
| Same applicant, cards 1-9 | `applicant_SYN-I0003/01_...pdf` to `09_...pdf` (10 is the optional last card) | each document accepted, values read back; "Check my application" says ready |
| Same applicant, Building plan card | `03_building_plan_PHONE_PHOTO.jpg` instead of the PDF | corner handles, Straighten, values read from the photo |
| Any empty card | `03_building_plan_BLURRY_PHOTO.jpg` | "looks blurry" warning before upload |
| Admin, Rules and norms | `admin_rule_pdfs/rule_update_example.pdf` | preview with ups and downs, a rejected row, a rule switched off |
| Admin, Rules and norms | `admin_rule_pdfs/handbook_prose_example.pdf` | refused: prose handbooks need the (planned) AI step |

All of it is synthetic. Bharathi College is a clean institution, so its papers pass every check; submitting it adds a low-risk fourth case to the queue. The other demo institutions are already loaded; their papers are not interchangeable with Bharathi's.

## A five-minute story
1. **Applicant** (`demo-applicant-i0064`): the checklist shows what is uploaded. Click **Check my application**: it says the fire certificate is not valid on the application date and a mandatory document is missing, in plain words, with no hint of fraud checks. Open "Values we read from this document".
2. **Photo upload** on any empty card: pick a photo of a printed page (or any image); drag the four corner handles; **Straighten**; **Use this page**. Values that were hard to read are highlighted "please check".
3. **Supervisor** (`demo-supervisor`): the queue is ordered by score. Open SYN-I0031, choose the **Fire safety certificate** tab: the covered date is boxed on the page, with the finding beside it. **Assign** SYN-I0001. **Evidence report (PDF)** downloads the audit file.
4. **Reviewer** (`demo-reviewer-dev`, a different browser or private window): the same case, **blind**: no score, no probabilities. Record a decision; the second reviewer decides separately (`demo-reviewer-chi`).
5. **Admin** (`demo-admin`) -> **Rules and norms**: Download template, then upload `demo_assets/rule_update_example.pdf`. The preview shows each value going up or down, rejected rows with reasons, and the effect on the submitted cases. Tick the confirmation (it switches a rule off), **Apply**, then **Restore shipped rules**. **Verify audit trail** shows every step chained.

## If something goes wrong
- Page says it cannot reach the server: the API window is still seeding or was closed; wait for the token list.
- With `-Dev`, sign-in does nothing the first time: the dev server was compiling; try again (the production start does not do this).
- Port already in use: close old SIH26261 windows (or `Get-Process python,node`), start again.
- Start from scratch: delete the `data_demo` folder or run without `-Keep`.

## Be upfront when presenting
Sample data only (synthetic corpus, mock registries); the norms are illustrative, not the handbook's; sign-in is by static token, not single sign-on; the copy-and-paste forgery detector is not built yet; photo straightening was tested on simulated photos.
