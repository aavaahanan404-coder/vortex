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
| Admin, Rules and norms | `admin_rule_pdfs/handbook_prose_example.pdf` | refused by the table reader; with a Gemini key, "Read it with AI" proposes 3 changes (see the AI section) |

All of it is synthetic. Bharathi College is a clean institution, so its papers pass every check; submitting it adds a low-risk fourth case to the queue. The other demo institutions are already loaded; their papers are not interchangeable with Bharathi's.

## A five-minute story
1. **Applicant** (`demo-applicant-i0064`): the checklist shows what is uploaded. Click **Check my application**: it says the fire certificate is not valid on the application date and a mandatory document is missing, in plain words, with no hint of fraud checks. Open "Values we read from this document".
2. **Photo upload** on any empty card: pick a photo of a printed page (or any image); drag the four corner handles; **Straighten**; **Use this page**. Values that were hard to read are highlighted "please check".
3. **Supervisor** (`demo-supervisor`): the queue is ordered by score. Open SYN-I0031, choose the **Fire safety certificate** tab: the covered date is boxed on the page, with the finding beside it. **Assign** SYN-I0001. **Evidence report (PDF)** downloads the audit file.
4. **Reviewer** (`demo-reviewer-dev`, a different browser or private window): the same case, **blind**: no score, no probabilities. Record a decision; the second reviewer decides separately (`demo-reviewer-chi`).
5. **Admin** (`demo-admin`) -> **Rules and norms**: Download template, then upload `demo_assets/rule_update_example.pdf`. The preview shows each value going up or down, rejected rows with reasons, and the effect on the submitted cases. Tick the confirmation (it switches a rule off), **Apply**, then **Restore shipped rules**. **Verify audit trail** shows every step chained.

## Hosting it on the internet (Render)
The repository contains a `Dockerfile` and a `render.yaml` blueprint: one container runs the demo API and the web app on one public address.
1. Open **https://render.com/deploy?repo=https://github.com/aavaahanan404-coder/vortex** and sign in (a free Render account is enough; GitHub sign-in works).
2. Press **Apply / Deploy**. The first build takes about 10 minutes. The demo data is prepared during the build, so later starts are quick.
3. Render shows the address, like `https://vortex-xxxx.onrender.com`. Open **`<address>/demo`**: that is the Demo hub. Use "Download all sample files (zip)" there for the files to upload.

Things to know:
- **Anyone with the address can sign in as admin or reviewer** (the demo tokens are shown on the hub) and change the rules. That is acceptable only because all data is sample data; the site returns to its starting state whenever it restarts. Do not upload real documents to it.
- On the free plan the site **sleeps after about 15 minutes idle**; the next visitor waits a few minutes for it to wake. Wake it before presenting.
- The free plan has 512 MB of memory. If Render reports the service was killed for memory (reading scanned pages uses the most), change `plan: free` to `plan: starter` in `render.yaml` (a paid plan) and redeploy.
- The image has not been built on the development computer (no Docker there); its parts were tested separately: the pinned dependencies in a clean environment, seeding, start-up from baked data, the zip and hub, and the web-server command. If the first Render build fails, send me the build log.

## AI help - optional (Groq, OpenRouter or Google Gemini)
With an AI key the system can: (1) **explain a case's anomalies** in plain words, with a short note under each finding (button "Explain these anomalies" on a case page, for supervisors and reviewers); (2) **explain a rule-change preview** ("Explain these changes in plain words"); (3) **read a prose rule document** that the table reader refuses ("Read it with AI"), turning it into the same preview, with the document's quoted sentence beside each change and a "Show the resulting rule settings as YAML" view. Without a key all of this is hidden and nothing else changes.

**Pick a free provider and get a key** (never paste it into chat, a file in git, or the hub page):
| Provider | Where to get a free key | Notes |
|---|---|---|
| **Groq** (recommended) | https://console.groq.com/keys | fast open models (Llama, gpt-oss); text only; small free token limits, so long rule documents are cut to 14,000 characters |
| OpenRouter | https://openrouter.ai/keys | only models ending in `:free` are ever used, so nothing can be billed; free models can be slow or busy |
| Google Gemini | https://aistudio.google.com/apikey | the only one that can read a **scanned** rule PDF; free quota may be zero for some keys |

**Set the key**
- On this computer: copy `.env.example` to `.env` (git ignores it), put the key after the matching name (`GROQ_API_KEY=` etc.), then start with `start_demo.bat`. The window says "AI help: on".
- On Render: Dashboard, the service, Environment, add the variable (`GROQ_API_KEY`, `OPENROUTER_API_KEY` or `GEMINI_API_KEY`) and save; the service restarts. If several keys are set, Groq is used first, then OpenRouter, then Gemini; `AI_PROVIDER=groq|openrouter|gemini` forces one, `AI_MODEL` overrides the model.

**Rules it follows**
- It explains and proposes; it never decides, and it cannot apply anything. An AI-read rule document goes through the same checks as a table, shows every change with the quoted sentence, and **always needs the ticked confirmation**. It cannot set the "validated" flag or add rules, norms or programme types.
- **A blind reviewer's explanation is written without the machine's score, probabilities or hard/soft labels**, and is shown no score.
- What is sent to the provider: finding messages and trimmed evidence (case explanations), the change list (plan explanations), or the rule document's text (AI import). Never an applicant's uploaded documents, names or tokens. Use only sample data unless your organisation has approved sending this to that provider; free tiers may log prompts, so read the provider's terms.
- Each use is written to the audit log (who, what feature, which model; not the content). Answers for a case are kept in memory so repeated clicks do not call the provider again. A usage-limit answer is never retried and pauses AI calls for a minute.
- Each answer carries the line "Written by an AI model ... can be wrong ... does not decide anything".

## If something goes wrong
- Page says it cannot reach the server: the API window is still seeding or was closed; wait for the token list.
- With `-Dev`, sign-in does nothing the first time: the dev server was compiling; try again (the production start does not do this).
- Port already in use: close old SIH26261 windows (or `Get-Process python,node`), start again.
- Start from scratch: delete the `data_demo` folder or run without `-Keep`.

## Be upfront when presenting
Sample data only (synthetic corpus, mock registries); the norms are illustrative, not the handbook's; sign-in is by static token, not single sign-on; the copy-and-paste forgery detector is not built yet; photo straightening was tested on simulated photos.
