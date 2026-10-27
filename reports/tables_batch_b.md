# Detection / false-positive tables

Splits are md5(inst_id) 70/15/15 of corpus_v2; configs are built from `train` only, tuned on `val`, reported on `test` and on the held-out corpus_test_v2. n < 10 is weak evidence.

## pdf_forensics

### pdf_forensics on corpus_v2 (split=all, 965 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| editor_metadata | 3 | 3 | 100% | n=3, weak evidence |
| incremental_edit_forgery | 3 | 3 | 100% | n=3, weak evidence |
| spliced_expiry_forgery | 3 | 3 | 100% | n=3, weak evidence |

False positives on clean documents: **0/900**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### pdf_forensics on corpus_v2 (split=train, 677 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| editor_metadata | 2 | 2 | 100% | n=2, weak evidence |
| incremental_edit_forgery | 1 | 1 | 100% | n=1, weak evidence |
| spliced_expiry_forgery | 2 | 2 | 100% | n=2, weak evidence |

False positives on clean documents: **0/635**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### pdf_forensics on corpus_v2 (split=val, 94 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| editor_metadata | 1 | 1 | 100% | n=1, weak evidence |
| spliced_expiry_forgery | 1 | 1 | 100% | n=1, weak evidence |

False positives on clean documents: **0/87**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### pdf_forensics on corpus_v2 (split=test, 194 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| incremental_edit_forgery | 2 | 2 | 100% | n=2, weak evidence |

False positives on clean documents: **0/178**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### pdf_forensics on corpus_test_v2 (split=all, 969 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| editor_metadata | 2 | 2 | 100% | n=2, weak evidence |
| incremental_edit_forgery | 2 | 2 | 100% | n=2, weak evidence |
| spliced_expiry_forgery | 3 | 3 | 100% | n=3, weak evidence |

False positives on clean documents: **0/911**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

## dedup

### dedup on corpus_v2 (split=all, 965 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| exact_duplicate | 2 | 2 | 100% | n=2, weak evidence |
| visual_near_duplicate | 3 | 3 | 100% | n=3, weak evidence |

False positives on clean documents: **0/900**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Correct link endpoints on unlabelled donors (not counted as FP): 4

Fired on labelled documents this module does not target: {'visual_near_duplicate': 1}

Misses: 0

### dedup on corpus_v2 (split=train, 677 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| exact_duplicate | 2 | 2 | 100% | n=2, weak evidence |
| visual_near_duplicate | 2 | 2 | 100% | n=2, weak evidence |

False positives on clean documents: **0/635**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Correct link endpoints on unlabelled donors (not counted as FP): 4

Misses: 0

### dedup on corpus_v2 (split=val, 94 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/87**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### dedup on corpus_v2 (split=test, 194 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| visual_near_duplicate | 1 | 1 | 100% | n=1, weak evidence |

False positives on clean documents: **0/178**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Fired on labelled documents this module does not target: {'visual_near_duplicate': 1}

Misses: 0

### dedup on corpus_test_v2 (split=all, 969 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| exact_duplicate | 2 | 2 | 100% | n=2, weak evidence |
| visual_near_duplicate | 3 | 3 | 100% | n=3, weak evidence |

False positives on clean documents: **0/911**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Correct link endpoints on unlabelled donors (not counted as FP): 4

Fired on labelled documents this module does not target: {'visual_near_duplicate': 1}

Misses: 0

## ela

### ela on corpus_v2 (split=all, 965 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/900**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### ela on corpus_v2 (split=train, 677 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/635**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### ela on corpus_v2 (split=val, 94 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/87**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### ela on corpus_v2 (split=test, 194 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/178**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### ela on corpus_test_v2 (split=all, 969 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/911**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0
