# dedup pair statistics: corpus_v2, split=train

documents in split: 677; pHash 1024 bits; MinHash 128 perms, 3-word shingles minus boilerplate

## pHash normalised Hamming distance, unrelated same-type pairs

| pair class | n pairs | min | p0.1 | p1 | p5 | median |
|---|---|---|---|---|---|---|
| dig-dig | 21219 | 0.012 | 0.020 | 0.031 | 0.051 | 0.102 |
| mixed (one scan) | 1509 | 0.070 | 0.084 | 0.092 | 0.105 | 0.172 |
| scan-scan | 41 | 0.072 | 0.072 | 0.072 | 0.092 | 0.182 |

## MinHash Jaccard, unrelated same-type pairs (n=21219): p50=0.000, p99=0.062, p99.9=0.148, max=0.602

## Labelled pairs in this split: 4 (near: 2)

| pair | mode | class | hamming_frac | jaccard |
|---|---|---|---|---|
| SYN-I0034-fire_cert <-> SYN-I0050-fire_cert | exact | dig-dig | 0.000 | 1.000 |
| SYN-I0035-land_doc <-> SYN-I0046-land_doc | near | mixed (one scan) | 0.086 | n/a (no text) |
| SYN-I0053-fire_cert <-> SYN-I0065-fire_cert | exact | dig-dig | 0.000 | 1.000 |
| SYN-I0082-land_doc <-> SYN-I0086-land_doc | near | mixed (one scan) | 0.094 | n/a (no text) |

## Visual threshold trade-off (pHash normalised Hamming <= T)

| T | labelled near pairs caught | unrelated pairs linked: dig-dig | mixed | scan-scan | distinct clean docs linked |
|---|---|---|---|---|---|
| 0.02 | 0/2 | 26 | 0 | 0 | 38 |
| 0.03 | 0/2 | 183 | 0 | 0 | 124 |
| 0.05 | 0/2 | 969 | 0 | 0 | 336 |
| 0.07 | 0/2 | 3799 | 0 | 0 | 523 |
| 0.09 | 1/2 | 8639 | 12 | 2 | 564 |
| 0.1 | 2/2 | 10555 | 50 | 6 | 568 |
| 0.11 | 2/2 | 11920 | 117 | 9 | 572 |
