## run_pipeline on corpus_v2 (split=all, 100 institutions)

### clean institutions (n=48): category of each

| category | n |
|---|---|
| auto_clear_candidate | 39 |
| high_anomaly | 1 |
| needs_human | 8 |

auto-cleared 39/48 (81%); escalated to a person 9/48; of those at scenario institutions (registry outage by design) 4.
Escalated clean institutions, and why: SYN-I0022 needs_human ['low_ocr_confidence']; SYN-I0087 needs_human ['rule_not_evaluable', 'verification_pending']; SYN-I0098 needs_human ['rule_not_evaluable', 'source_unavailable']; SYN-I0072 needs_human ['low_ocr_confidence']; SYN-I0050 high_anomaly ['exact_duplicate']; SYN-I0092 needs_human ['low_ocr_confidence']; SYN-I0036 needs_human ['low_ocr_confidence']; SYN-I0002 needs_human ['low_ocr_confidence']

### seeded defects (n=52 institutions with at least one mutation)

| mutation | n | escalated | high_anomaly | median score | top findings |
|---|---|---|---|---|---|
| builtup_shortfall | 3 (weak) | 3/3 | 2 | 0.95 | R002 x2, blurry x1, field_missing x1 |
| certificate_reuse | 2 (weak) | 2/2 | 2 | 0.94 | R008 x2, R015 x2, qr_wrong_institution x2 |
| copy_move_scan | 5 (weak) | 2/5 | 1 | 0.00 | low_ocr_confidence x2, manual_review_required x1, rule_not_evaluable x1 |
| double_employed_faculty | 3 (weak) | 3/3 | 3 | 0.90 | R011 x3, registry_mismatch x3 |
| editor_metadata | 3 (weak) | 3/3 | 2 | 0.99 | editor_metadata x3, field_missing x1, layout_shifted x1 |
| expired_fire_cert | 3 (weak) | 3/3 | 3 | 0.85 | R004 x3, registry_expired x3, R010 x1 |
| faculty_shortfall | 3 (weak) | 3/3 | 3 | 0.90 | R001 x3, registry_mismatch x2, R013 x1 |
| financial_arithmetic_error | 3 (weak) | 3/3 | 3 | 0.90 | R014 x3, R008 x1, R009 x1 |
| incremental_edit_forgery | 3 (weak) | 3/3 | 3 | 0.98 | R016 x3, covered_text x3, font_anomaly x3 |
| intake_exceeds_affiliation | 3 (weak) | 3/3 | 3 | 0.91 | R007 x3, R001 x2, R002 x2 |
| land_area_mismatch | 3 (weak) | 3/3 | 3 | 0.90 | R009 x3, registry_mismatch x3, visual_near_duplicate x1 |
| missing_document | 3 (weak) | 3/3 | 3 | 0.95 | R010 x3, R016 x2, qr_date_mismatch x2 |
| name_mismatch | 3 (weak) | 3/3 | 2 | 0.89 | R008 x3, R006 x1, rule_not_evaluable x1 |
| near_duplicate_land | 3 (weak) | 3/3 | 3 | 0.90 | R008 x3, registry_mismatch x3, R012 x2 |
| pc_shortfall | 3 (weak) | 3/3 | 0 | 0.70 | R003 x3 |
| qr_bad_signature | 3 (weak) | 3/3 | 3 | 0.99 | qr_bad_signature x3, R016 x1, covered_text x1 |
| qr_date_mismatch | 3 (weak) | 3/3 | 3 | 0.95 | R016 x3, qr_date_mismatch x3, registry_mismatch x3 |
| scan_degraded | 6 (weak) | 6/6 | 5 | 0.99 | low_ocr_confidence x6, blurry x5, field_missing x4 |
| shared_land_parcel | 3 (weak) | 3/3 | 3 | 0.90 | R009 x3, R012 x3, registry_mismatch x3 |
| short_lease | 2 (weak) | 2/2 | 2 | 0.91 | R006 x2, R008 x1, R014 x1 |
| spliced_expiry_forgery | 3 (weak) | 3/3 | 3 | 0.99 | R016 x3, covered_text x3, editor_metadata x3 |
| unregistered_certificate | 3 (weak) | 3/3 | 3 | 0.99 | R013 x3, qr_bad_signature x3, registry_mismatch x3 |

Institutions with a seeded defect that reach a person: **49/52**; auto-cleared (missed): ["SYN-I0053['copy_move_scan']", "SYN-I0067['copy_move_scan']", "SYN-I0084['copy_move_scan']"].

Double review would be routed for 51/100 institutions.
Client-view leaks: **0** []. Steps that failed (step_failed): 0.
