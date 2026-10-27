## run_rules on corpus_test_v2 (split=all, scenarios on, 100 institutions)

### adapters: status agrees with the manifest for 963/969 documents

| document type | expected status | n | agree |
|---|---|---|---|
| aff_letter | verified | 100 | 100 |
| affidavit | not_applicable | 100 | 100 |
| building_plan | mismatch_detected | 3 (weak) | 3 |
| building_plan | verification_pending | 2 (weak) | 2 |
| building_plan | verified | 95 | 93 |
| eoa | verified | 72 | 71 |
| faculty_list | mismatch_detected | 3 (weak) | 3 |
| faculty_list | verified | 96 | 96 |
| financial | verified | 100 | 100 |
| fire_cert | expired | 3 (weak) | 3 |
| fire_cert | mismatch_detected | 10 | 9 |
| fire_cert | source_unavailable | 1 (weak) | 1 |
| fire_cert | verified | 86 | 85 |
| infra_statement | not_applicable | 100 | 100 |
| land_doc | mismatch_detected | 8 (weak) | 8 |
| land_doc | verified | 91 | 90 |
| society_reg | manual_review_required | 3 (weak) | 3 |
| society_reg | verified | 96 | 96 |

Reason text matches for 32/32 non-verified documents with a stated reason.
Retries: 102 documents needed more than one attempt (flaky / unavailable scenarios).
Adapter disagreements (want -> got): mismatch_detected->source_unavailable x1; verified->manual_review_required x5

Disagreements: SYN-I0009-fire_cert want verified got manual_review_required (certificate not in registry; the identifier 'eiiell' was read by OCR and may be a misread); SYN-I0020-building_plan want verified got manual_review_required (identifier 'cert_no' could not be read from the document); SYN-I0025-land_doc want verified got manual_review_required (parcel not in registry; the identifier 'SYN-SY/KA/S4/13A' was read by OCR and may be a misread); SYN-I0035-fire_cert want mismatch_detected got source_unavailable (fire authority returned 503 on every attempt); SYN-I0045-building_plan want verified got manual_review_required (certificate not in registry; the identifier 'PLAN' was read by OCR and may be a misread); SYN-I0056-eoa want verified got manual_review_required (the approved-course table could not be read from the document)

### rules: result agrees with the ground truth

| rule | pass | fail | n/a | not_evaluable | agree | disagree (non-scenario) | disagree (scenario institutions) |
|---|---|---|---|---|---|---|---|
| R001 | 96 | 3 | 0 | 1 | 99/100 | 1 | 0 |
| R002 | 97 | 3 | 0 | 0 | 99/100 | 1 | 0 |
| R003 | 97 | 3 | 0 | 0 | 99/100 | 1 | 0 |
| R004 | 97 | 3 | 0 | 0 | 99/100 | 1 | 0 |
| R005 | 100 | 0 | 0 | 0 | 98/100 | 2 | 0 |
| R006 | 44 | 2 | 53 | 1 | 100/100 | 0 | 0 |
| R007 | 98 | 2 | 0 | 0 | 100/100 | 0 | 0 |
| R008 | 92 | 8 | 0 | 0 | 100/100 | 0 | 0 |
| R009 | 93 | 6 | 0 | 1 | 99/100 | 1 | 0 |
| R010 | 97 | 3 | 0 | 0 | 100/100 | 0 | 0 |
| R011 | 96 | 3 | 0 | 1 | 100/100 | 0 | 0 |
| R012 | 93 | 6 | 0 | 1 | 99/100 | 1 | 0 |
| R013 | 97 | 3 | 0 | 0 | 93/100 | 3 | 4 |
| R014 | 98 | 2 | 0 | 0 | 100/100 | 0 | 0 |
| R015 | 98 | 2 | 0 | 0 | 97/100 | 1 | 2 |
| R016 | 92 | 8 | 0 | 0 | 93/100 | 3 | 4 |

All rules: 1575/1600 agree.
Failure detail text matches for 39/39 failed rules with a stated detail.

Disagreements by kind: **false failures 0**, **failures turned to pass 0**, declined as not_evaluable 25 (of which at scenario institutions 10).
(want -> got, cause): fail->not_evaluable [scenario] x1; pass->not_evaluable [ocr/other] x15; pass->not_evaluable [scenario] x9

Disagreements (first 12): SYN-I0009 R004 want pass got not_evaluable; SYN-I0009 R013 want pass got not_evaluable; SYN-I0009 R015 want pass got not_evaluable; SYN-I0009 R016 want pass got not_evaluable; SYN-I0020 R005 want pass got not_evaluable; SYN-I0020 R013 want pass got not_evaluable; SYN-I0020 R016 want pass got not_evaluable; SYN-I0025 R009 want pass got not_evaluable; SYN-I0025 R012 want pass got not_evaluable; SYN-I0035 R013 want pass got not_evaluable [scenario]; SYN-I0035 R015 want fail got not_evaluable [scenario]; SYN-I0035 R016 want pass got not_evaluable [scenario]
