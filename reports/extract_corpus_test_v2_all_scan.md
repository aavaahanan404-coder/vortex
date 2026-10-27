### extract on corpus_test_v2 (split=all, source=scan)

| source | doc type | documents | fields | correct | accuracy |
|---|---|---|---|---|---|
| scan | building_plan | 1 (weak) | 7 | 7 | 100.0% |
| scan | eoa | 3 (weak) | 27 | 27 | 100.0% |
| scan | fire_cert | 3 (weak) | 18 | 18 | 100.0% |
| scan | land_doc | 4 (weak) | 36 | 35 | 97.2% |
| scan | society_reg | 9 (weak) | 63 | 63 | 100.0% |
| scan-gate-fail | affidavit | 1 (weak) | 18 | 0 | 0.0% |
| scan-gate-fail | building_plan | 2 (weak) | 14 | 0 | 0.0% |
| scan-gate-fail | eoa | 1 (weak) | 12 | 0 | 0.0% |
| scan-gate-fail | fire_cert | 1 (weak) | 6 | 0 | 0.0% |

**scan** (n=20 documents, 151 fields): accuracy 99.3%; conf >= 0.9: 100.0% of 122; conf < 0.7 (incl. not found): 80.0% of 5; not found: 0

**scan-gate-fail** (n=5 documents, 50 fields): accuracy 0.0%; conf >= 0.9: 0.0% of 0; conf < 0.7 (incl. not found): 0.0% of 49; not found: 30

Worst keys (error rate, n fields):
- scan-gate-fail affidavit applied[i].level: 100% of 4
- scan-gate-fail affidavit applied[i].intake: 100% of 4
- scan-gate-fail affidavit applied[i].course: 100% of 4
- scan-gate-fail eoa courses[i].level: 100% of 3
- scan-gate-fail eoa courses[i].intake: 100% of 3

Layout check on clean documents (n=12): false findings none
