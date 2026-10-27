## run_rules on corpus_v2 (split=all, scenarios on, 100 institutions)

### adapters: status agrees with the manifest for 959/965 documents

| document type | expected status | n | agree |
|---|---|---|---|
| aff_letter | verified | 99 | 99 |
| affidavit | not_applicable | 100 | 100 |
| building_plan | mismatch_detected | 3 (weak) | 3 |
| building_plan | verification_pending | 1 (weak) | 1 |
| building_plan | verified | 96 | 95 |
| eoa | verified | 68 | 67 |
| faculty_list | mismatch_detected | 3 (weak) | 3 |
| faculty_list | verified | 96 | 96 |
| financial | verified | 99 | 99 |
| fire_cert | expired | 3 (weak) | 3 |
| fire_cert | mismatch_detected | 11 | 11 |
| fire_cert | source_unavailable | 2 (weak) | 2 |
| fire_cert | verified | 84 | 83 |
| infra_statement | not_applicable | 100 | 100 |
| land_doc | mismatch_detected | 9 (weak) | 8 |
| land_doc | verified | 91 | 90 |
| society_reg | manual_review_required | 1 (weak) | 1 |
| society_reg | verified | 99 | 98 |

Reason text matches for 32/32 non-verified documents with a stated reason.
Retries: 106 documents needed more than one attempt (flaky / unavailable scenarios).
Adapter disagreements (want -> got): mismatch_detected->manual_review_required x1; verified->manual_review_required x5

Disagreements: SYN-I0005-eoa want verified got manual_review_required (identifier 'ref_no' could not be read from the document); SYN-I0016-building_plan want verified got manual_review_required (certificate not in registry; the identifier 'SYN-BPA/RJ/2023/3941 1' was read by OCR and may be a misread); SYN-I0021-fire_cert want verified got manual_review_required (certificate not in registry; the identifier 'tee Pe By pe come' was read by OCR and may be a misread); SYN-I0037-land_doc want verified got manual_review_required (parcel not in registry; the identifier 've ee' was read by OCR and may be a misread); SYN-I0047-society_reg want verified got manual_review_required (society not in registry; the identifier 'below Stands' was read by OCR and may be a misread); SYN-I0082-land_doc want mismatch_detected got manual_review_required (parcel not in registry; the identifier 'SYN-SY/TS/S80/8A' was read by OCR and may be a misread)

### rules: result agrees with the ground truth

| rule | pass | fail | n/a | not_evaluable | agree | disagree (non-scenario) | disagree (scenario institutions) |
|---|---|---|---|---|---|---|---|
| R001 | 94 | 5 | 0 | 1 | 98/100 | 2 | 0 |
| R002 | 95 | 5 | 0 | 0 | 98/100 | 2 | 0 |
| R003 | 95 | 5 | 0 | 0 | 98/100 | 2 | 0 |
| R004 | 97 | 3 | 0 | 0 | 99/100 | 1 | 0 |
| R005 | 100 | 0 | 0 | 0 | 100/100 | 0 | 0 |
| R006 | 39 | 2 | 59 | 0 | 100/100 | 0 | 0 |
| R007 | 97 | 3 | 0 | 0 | 100/100 | 0 | 0 |
| R008 | 92 | 8 | 0 | 0 | 100/100 | 0 | 0 |
| R009 | 93 | 7 | 0 | 0 | 98/100 | 2 | 0 |
| R010 | 97 | 3 | 0 | 0 | 100/100 | 0 | 0 |
| R011 | 96 | 3 | 0 | 1 | 100/100 | 0 | 0 |
| R012 | 94 | 6 | 0 | 0 | 98/100 | 2 | 0 |
| R013 | 97 | 3 | 0 | 0 | 95/100 | 2 | 3 |
| R014 | 96 | 3 | 0 | 1 | 100/100 | 0 | 0 |
| R015 | 98 | 2 | 0 | 0 | 97/100 | 1 | 2 |
| R016 | 91 | 9 | 0 | 0 | 95/100 | 2 | 3 |

All rules: 1576/1600 agree.
Failure detail text matches for 47/47 failed rules with a stated detail.

Disagreements by kind: **false failures 0**, **failures turned to pass 0**, declined as not_evaluable 24 (of which at scenario institutions 8).
(want -> got, cause): fail->not_evaluable [ocr/other] x2; pass->not_evaluable [ocr/other] x14; pass->not_evaluable [scenario] x8

Disagreements (first 12): SYN-I0016 R013 want pass got not_evaluable; SYN-I0016 R016 want pass got not_evaluable; SYN-I0021 R004 want pass got not_evaluable; SYN-I0021 R013 want pass got not_evaluable; SYN-I0021 R015 want pass got not_evaluable; SYN-I0021 R016 want pass got not_evaluable; SYN-I0037 R009 want pass got not_evaluable; SYN-I0037 R012 want pass got not_evaluable; SYN-I0082 R009 want pass got not_evaluable; SYN-I0082 R012 want fail got not_evaluable; SYN-I0083 R001 want pass got not_evaluable; SYN-I0083 R002 want pass got not_evaluable
