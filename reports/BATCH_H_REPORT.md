# Batch H report: rule changer (admin uploads a rule PDF)

Scope: an admin uploads a **structured** rule PDF, sees exactly what would change (each value up or down, with the effect on cases already
in the system), applies it with one click, and can restore any earlier version. Unstructured PDFs are refused with a clear message; reading
them with AI is the planned next step and is not built.

## How it works (for the admin)

1. `/admin` -> "Rules and norms" -> **Download template**: the current rules as an editable PDF (every norm, every rule's kind / severity / p / on-off, with today's values).
2. Edit the numbers in the PDF (or use a table-based update written in the same layout) and **upload** it.
3. A **preview** lists every change as `was -> becomes` with an up / down arrow and the percentage, the clause cited, the rows that could not be used (with page and reason), and "if applied now, on the N submitted cases: R001 pass to fail for 1 case ...".
4. **Apply to all new cases.** Changes that can quietly weaken the checks (a value moving 50% or more, a rule switched off, hard -> soft) are marked "confirm" and need a ticked box first.
5. **History**: every version keeps the uploaded PDF, the change log and the full settings; "Restore this version" / "Restore shipped rules" brings any of them back (restore is itself a new, audited version).

## The PDF format (v1)

Two ruled tables, found by their header row (any order, may continue over pages with or without repeated headers); optional lines above:
`Norms version: ...`, `Effective from: YYYY-MM-DD`, `Values validated against the handbook: yes|no`.

| Norms table | Programme type | Norm | Value | Handbook reference |
|---|---|---|---|---|
| e.g. | Engineering | students_per_faculty | 18 | Clause 5.1 |

| Rules table | Rule | Setting | Value | Handbook reference |
|---|---|---|---|---|
| e.g. | R001 | p | 0.8 | Clause 5.1 |

Settings of a rule: `kind` (hard / soft), `severity` (minor / major / critical), `p` (0-1 or a percentage), `enabled` (yes / no), `handbook_ref`. A reference in the last column also fills that rule's clause (it feeds the evidence PDF, which printed "not mapped" until now). Norm names accept spaces and capitals; `Management` means `mba`; `r1`, `R-001`, `R001` are the same rule.

## What it does and does not touch

- Changes only **numbers and settings**: the 12 norms in `config/norms.yaml` (students per faculty, built-up area per 60 seats, PCs per seat, minimum lease years for three programme types) and, per rule, kind / severity / p / on-off / clause. It never edits a rule expression, adds a norm or programme type the rulebook does not use, or decides whether a value is right.
- `config/norms.yaml` and `config/rulebook.yaml` stay the hand-written base. The PDF writes only an overrides file in the data directory (`data/rules/`); a value equal to the base drops its override, so "restore version 0" is exactly the shipped configuration (tested).
- Rejected rows never block the rest: unknown programme / norm / rule / setting, non-numeric or non-positive values, a fraction for a whole-number norm, `p` outside (0,1], two rows for the same item with different values (both rejected, so the outcome never depends on row order).
- The live pipeline is swapped under the processing lock; each stored result records the `rules_version` that produced it; previews, applies and restores are written to the hash-chained audit log (verified intact in the browser run).
- Existing cases are **not re-scored** by an update; the preview shows what would move, and new submissions use the new rules.
- `Validated: yes` in a file turns the "illustrative norms" notice off everywhere; the preview says so in a warning. It is the file's statement, not something the system checks.

## Verification (what was actually done)

- `tests/test_rule_import.py` (11 tests): template round-trip changes nothing (a 64-row rules table spanning pages), up / down / percent / clause, bad rows with reasons, conflicting and identical duplicates, risky-change acknowledgement, layering and restoring a base value, template of a changed state (a switched-off rule shows as `no`), unstructured and non-PDF refusal, store (stage, apply, stale preview refused, history, restore to 1 and 0), impact simulator (a tighter norm turns pass into fail, a looser one changes nothing, identical rules change nothing).
- `tests/test_api.py` (+2 tests): admin-only (reviewer, supervisor, applicant, no token), refusal of non-PDF and prose PDFs, template download, preview -> apply (409 without the acknowledgement) -> the running pipeline uses the new numbers -> a used preview is stale -> restore -> audit chain intact; the impact preview ran on a real submitted case.
- Browser (demo stack): downloaded-style edited PDF uploaded to `/admin`: 2 increases, 3 decreases, 4 other, 2 rows rejected with reasons, impact on the 3 submitted cases (one case flipped fail -> pass, another pass -> fail, 3 cases lose R010), the Apply button disabled until the box was ticked, applied (version 1, history entry with file name and admin), restored to shipped (version 2), audit verify intact.
- Backend suites: see the numbers appended at the end of this file.

## Limits and decisions for you

- **Structured PDFs only.** A real handbook is probably prose. Reading it needs the AI step; the plan is to turn the prose into the same table form, show it in this same preview, and require the same confirmation, so the AI never changes rules directly. That needs an approved model/API choice and test documents.
- Table detection was tested on PDFs written by this tool (ruled tables). A PDF exported from Word or a spreadsheet should work (header-based, ruled or unruled), but **no externally authored file has been tried**: please give me one real sample.
- The 50% "large change" limit and the list of risky changes are my defaults (`LARGE_CHANGE_PCT` in the module), not agreed policy.
- Authority: any admin token can apply a change. A two-person approval (one uploads, another approves) is not built.
- Applying takes effect for the next submission; dry-run checks by applicants also use the new numbers at once.
- The audit log records who, when, file name, SHA-256 and number of changes; the full change list is in `data/rules/versions/vNNNN.json` (and the PDF itself), not in the audit chain.
- Only norms listed in `norms.yaml` can be changed. Anything else in a handbook (new rules, new document types, new expressions) still needs code or a rulebook edit.

## Backend suite numbers

- `corpus_v2`: 362 passed, 1 xfailed (the known perspective limit), 5 min 20 s.
- Held-out `corpus_test_v2`: 352 passed, 1 xfailed, **1 failed**: my new API test hard-coded the MBA programme type of the corpus_v2 case, and the held-out forged case has another type. That is a test bug (the code was right: the impact preview correctly found no R001 change for a programme I had not changed). The test now uses the case's own programme type. After the fix, `tests/test_api.py` + `tests/test_rule_import.py` pass on both corpora (26 passed each); I did **not** re-run the whole held-out suite after that one-line test change.
- No threshold or detector was tuned on held-out data in this batch.
