## run_pipeline on corpus_test_v2 (split=all, 100 institutions)

### clean institutions (n=50): category of each

| category | n |
|---|---|
| auto_clear_candidate | 45 |
| needs_human | 5 |

auto-cleared 45/50 (90%); escalated to a person 5/50; of those at scenario institutions (registry outage by design) 2.
Escalated clean institutions, and why: SYN-I0042 needs_human ['low_ocr_confidence', 'rule_not_evaluable', 'verification_pending']; SYN-I0085 needs_human ['rule_not_evaluable', 'source_unavailable']; SYN-I0078 needs_human ['low_ocr_confidence']; SYN-I0039 needs_human ['visual_near_duplicate']; SYN-I0093 needs_human ['visual_near_duplicate']

### seeded defects (n=50 institutions with at least one mutation)

| mutation | n | escalated | high_anomaly | median score | top findings |
|---|---|---|---|---|---|
| builtup_shortfall | 3 (weak) | 3/3 | 2 | 0.99 | R002 x3, blurry x1, field_missing x1 |
| certificate_reuse | 2 (weak) | 2/2 | 2 | 0.98 | R008 x2, exact_duplicate x2, qr_wrong_institution x2 |
| copy_move_scan | 5 (weak) | 3/5 | 2 | 0.73 | low_ocr_confidence x1, manual_review_required x1, rule_not_evaluable x1 |
| double_employed_faculty | 3 (weak) | 3/3 | 3 | 0.90 | R011 x3, registry_mismatch x3 |
| editor_metadata | 2 (weak) | 2/2 | 0 | 0.70 | editor_metadata x2 |
| expired_fire_cert | 3 (weak) | 3/3 | 3 | 0.85 | R004 x3, registry_expired x3 |
| faculty_shortfall | 3 (weak) | 3/3 | 1 | 0.70 | R001 x3, R008 x1, R015 x1 |
| financial_arithmetic_error | 2 (weak) | 2/2 | 2 | 0.85 | R014 x2 |
| incremental_edit_forgery | 2 (weak) | 2/2 | 2 | 0.99 | R016 x2, covered_text x2, font_anomaly x2 |
| intake_exceeds_affiliation | 2 (weak) | 2/2 | 2 | 0.99 | R007 x2, R016 x1, covered_text x1 |
| land_area_mismatch | 2 (weak) | 2/2 | 2 | 0.90 | R009 x2, registry_mismatch x2, manual_review_required x1 |
| missing_document | 3 (weak) | 3/3 | 3 | 0.90 | R010 x3, rule_not_evaluable x2, registry_mismatch x2 |
| name_mismatch | 3 (weak) | 3/3 | 1 | 0.70 | R008 x3, R013 x1, qr_bad_signature x1 |
| near_duplicate_land | 3 (weak) | 3/3 | 3 | 0.90 | R008 x3, R012 x3, registry_mismatch x3 |
| pc_shortfall | 2 (weak) | 2/2 | 1 | 0.82 | R003 x2, manual_review_required x1 |
| qr_bad_signature | 2 (weak) | 2/2 | 2 | 0.99 | qr_bad_signature x2, manual_review_required x1 |
| qr_date_mismatch | 3 (weak) | 3/3 | 3 | 0.95 | R016 x3, qr_date_mismatch x3, registry_mismatch x3 |
| scan_degraded | 5 (weak) | 5/5 | 5 | 1.00 | layout_shifted x5, low_ocr_confidence x5, tilted x5 |
| shared_land_parcel | 3 (weak) | 3/3 | 3 | 0.90 | R009 x3, R012 x3, registry_mismatch x3 |
| short_lease | 2 (weak) | 2/2 | 0 | 0.70 | R006 x2 |
| spliced_expiry_forgery | 3 (weak) | 3/3 | 3 | 0.99 | R016 x3, covered_text x3, editor_metadata x3 |
| unregistered_certificate | 3 (weak) | 3/3 | 3 | 0.99 | R013 x3, qr_bad_signature x3, registry_mismatch x3 |

Institutions with a seeded defect that reach a person: **48/50**; auto-cleared (missed): ["SYN-I0090['copy_move_scan']", "SYN-I0030['copy_move_scan']"].

Double review would be routed for 50/100 institutions.
Client-view leaks: **0** []. Steps that failed (step_failed): 0.
