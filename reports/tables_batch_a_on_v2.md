# Detection / false-positive tables

Splits are md5(inst_id) 70/15/15 of corpus_v2; configs are built from `train` only, tuned on `val`, reported on `test` and on the held-out corpus_test_v2. n < 10 is weak evidence.

## quality

### quality on corpus_v2 (split=all, 965 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| scan_degraded | 6 | 6 | 100% | n=6, weak evidence |

False positives on clean documents: **0/900**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### quality on corpus_v2 (split=train, 677 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| scan_degraded | 5 | 5 | 100% | n=5, weak evidence |

False positives on clean documents: **0/635**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### quality on corpus_v2 (split=val, 94 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| (module targets no label) | | | | |

False positives on clean documents: **0/87**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### quality on corpus_v2 (split=test, 194 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| scan_degraded | 1 | 1 | 100% | n=1, weak evidence |

False positives on clean documents: **0/178**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### quality on corpus_test_v2 (split=all, 969 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| scan_degraded | 5 | 5 | 100% | n=5, weak evidence |

False positives on clean documents: **0/911**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

## qr_verify

### qr_verify on corpus_v2 (split=all, 268 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| certificate_reuse | 2 | 2 | 100% | n=2, weak evidence |
| incremental_edit_forgery | 3 | 3 | 100% | n=3, weak evidence |
| qr_bad_signature | 6 | 6 | 100% | n=6, weak evidence |
| qr_date_mismatch | 3 | 3 | 100% | n=3, weak evidence |
| spliced_expiry_forgery | 3 | 3 | 100% | n=3, weak evidence |

False positives on clean documents: **0/242**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Fired on labelled documents this module does not target: {'qr_absent': 2}

Misses: 0

### qr_verify on corpus_v2 (split=train, 189 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| certificate_reuse | 2 | 2 | 100% | n=2, weak evidence |
| incremental_edit_forgery | 1 | 1 | 100% | n=1, weak evidence |
| qr_bad_signature | 3 | 3 | 100% | n=3, weak evidence |
| qr_date_mismatch | 1 | 1 | 100% | n=1, weak evidence |
| spliced_expiry_forgery | 2 | 2 | 100% | n=2, weak evidence |

False positives on clean documents: **0/172**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Fired on labelled documents this module does not target: {'qr_absent': 2}

Misses: 0

### qr_verify on corpus_v2 (split=val, 24 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| qr_bad_signature | 1 | 1 | 100% | n=1, weak evidence |
| spliced_expiry_forgery | 1 | 1 | 100% | n=1, weak evidence |

False positives on clean documents: **0/22**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### qr_verify on corpus_v2 (split=test, 55 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| incremental_edit_forgery | 2 | 2 | 100% | n=2, weak evidence |
| qr_bad_signature | 2 | 2 | 100% | n=2, weak evidence |
| qr_date_mismatch | 2 | 2 | 100% | n=2, weak evidence |

False positives on clean documents: **0/48**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Misses: 0

### qr_verify on corpus_test_v2 (split=all, 272 documents scored)

| label | detected | n | rate | note |
|---|---|---|---|---|
| certificate_reuse | 2 | 2 | 100% | n=2, weak evidence |
| incremental_edit_forgery | 2 | 2 | 100% | n=2, weak evidence |
| qr_bad_signature | 5 | 5 | 100% | n=5, weak evidence |
| qr_date_mismatch | 3 | 3 | 100% | n=3, weak evidence |
| spliced_expiry_forgery | 3 | 3 | 100% | n=3, weak evidence |

False positives on clean documents: **0/248**

| clean-document FP by finding | docs |
|---|---|
| (none) | 0 |

Fired on labelled documents this module does not target: {'qr_absent': 3}

Misses: 0
