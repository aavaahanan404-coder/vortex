# dedup pair statistics: corpus_v2, split=test

documents in split: 194; pHash 1024 bits; MinHash 128 perms, 3-word shingles minus boilerplate

## pHash normalised Hamming distance, unrelated same-type pairs

| pair class | n pairs | min | p0.1 | p1 | p5 | median |
|---|---|---|---|---|---|---|
| dig-dig | 1740 | 0.008 | 0.020 | 0.032 | 0.051 | 0.098 |
| mixed (one scan) | 54 | 0.104 | 0.104 | 0.107 | 0.113 | 0.361 |
| scan-scan | 1 | 0.359 | 0.359 | 0.359 | 0.359 | 0.359 |

## MinHash Jaccard, unrelated same-type pairs (n=1740): p50=0.000, p99=0.094, p99.9=0.148, max=0.148

## Labelled pairs in this split: 1 (near: 1)

| pair | mode | class | hamming_frac | jaccard |
|---|---|---|---|---|
| SYN-I0020-land_doc <-> SYN-I0055-land_doc | near | mixed (one scan) | 0.096 | n/a (no text) |

## Visual threshold trade-off (pHash normalised Hamming <= T)

| T | labelled near pairs caught | unrelated pairs linked: dig-dig | mixed | scan-scan | distinct clean docs linked |
|---|---|---|---|---|---|
| 0.02 | 0/1 | 2 | 0 | 0 | 3 |
| 0.03 | 0/1 | 14 | 0 | 0 | 16 |
| 0.05 | 0/1 | 80 | 0 | 0 | 66 |
| 0.07 | 0/1 | 311 | 0 | 0 | 125 |
| 0.09 | 0/1 | 753 | 0 | 0 | 146 |
| 0.1 | 1/1 | 928 | 0 | 0 | 154 |
| 0.11 | 1/1 | 1047 | 2 | 0 | 158 |
