# dedup pair statistics: corpus_test_v2, split=all

documents in split: 969; pHash 1024 bits; MinHash 128 perms, 3-word shingles minus boilerplate

## pHash normalised Hamming distance, unrelated same-type pairs

| pair class | n pairs | min | p0.1 | p1 | p5 | median |
|---|---|---|---|---|---|---|
| dig-dig | 44514 | 0.010 | 0.020 | 0.033 | 0.053 | 0.100 |
| mixed (one scan) | 2233 | 0.070 | 0.072 | 0.092 | 0.104 | 0.209 |
| scan-scan | 57 | 0.055 | 0.055 | 0.055 | 0.088 | 0.227 |

## MinHash Jaccard, unrelated same-type pairs (n=44514): p50=0.000, p99=0.086, p99.9=0.180, max=0.656

## Labelled pairs in this split: 5 (near: 3)

| pair | mode | class | hamming_frac | jaccard |
|---|---|---|---|---|
| SYN-I0003-land_doc <-> SYN-I0039-land_doc | near | mixed (one scan) | 0.082 | n/a (no text) |
| SYN-I0019-land_doc <-> SYN-I0060-land_doc | near | mixed (one scan) | 0.092 | n/a (no text) |
| SYN-I0030-fire_cert <-> SYN-I0035-fire_cert | exact | dig-dig | 0.000 | 1.000 |
| SYN-I0061-fire_cert <-> SYN-I0072-fire_cert | exact | dig-dig | 0.000 | 1.000 |
| SYN-I0087-land_doc <-> SYN-I0093-land_doc | near | mixed (one scan) | 0.094 | n/a (no text) |

## Visual threshold trade-off (pHash normalised Hamming <= T)

| T | labelled near pairs caught | unrelated pairs linked: dig-dig | mixed | scan-scan | distinct clean docs linked |
|---|---|---|---|---|---|
| 0.02 | 0/3 | 60 | 0 | 0 | 73 |
| 0.03 | 0/3 | 304 | 0 | 0 | 196 |
| 0.05 | 0/3 | 1865 | 0 | 0 | 525 |
| 0.07 | 0/3 | 8143 | 0 | 2 | 776 |
| 0.09 | 1/3 | 18751 | 22 | 5 | 813 |
| 0.1 | 3/3 | 22660 | 89 | 6 | 817 |
| 0.11 | 3/3 | 25426 | 194 | 8 | 827 |
