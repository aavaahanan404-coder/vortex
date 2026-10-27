### extract on corpus_v2 (split=all, source=scan)

| source | doc type | documents | fields | correct | accuracy |
|---|---|---|---|---|---|
| scan | affidavit | 1 (weak) | 18 | 17 | 94.4% |
| scan | building_plan | 5 (weak) | 35 | 35 | 100.0% |
| scan | eoa | 2 (weak) | 21 | 21 | 100.0% |
| scan | fire_cert | 5 (weak) | 30 | 30 | 100.0% |
| scan | land_doc | 5 (weak) | 44 | 42 | 95.5% |
| scan | society_reg | 4 (weak) | 28 | 26 | 92.9% |
| scan-gate-fail | affidavit | 2 (weak) | 33 | 0 | 0.0% |
| scan-gate-fail | eoa | 1 (weak) | 15 | 0 | 0.0% |
| scan-gate-fail | fire_cert | 1 (weak) | 6 | 0 | 0.0% |
| scan-gate-fail | land_doc | 1 (weak) | 8 | 0 | 0.0% |
| scan-gate-fail | society_reg | 1 (weak) | 7 | 0 | 0.0% |

**scan** (n=22 documents, 176 fields): accuracy 97.2%; conf >= 0.9: 98.6% of 141; conf < 0.7 (incl. not found): 70.0% of 10; not found: 0

**scan-gate-fail** (n=6 documents, 69 fields): accuracy 0.0%; conf >= 0.9: 0.0% of 0; conf < 0.7 (incl. not found): 0.0% of 67; not found: 34

Worst keys (error rate, n fields):
- scan-gate-fail affidavit applied[i].level: 100% of 7
- scan-gate-fail affidavit applied[i].intake: 100% of 7
- scan-gate-fail affidavit applied[i].course: 100% of 7
- scan-gate-fail eoa courses[i].level: 100% of 4
- scan-gate-fail eoa courses[i].intake: 100% of 4

Layout check on clean documents (n=14): false findings none
