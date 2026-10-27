# dedup pair statistics: corpus_v2, split=val

documents in split: 94; pHash 1024 bits; MinHash 128 perms, 3-word shingles minus boilerplate

## pHash normalised Hamming distance, unrelated same-type pairs

| pair class | n pairs | min | p0.1 | p1 | p5 | median |
|---|---|---|---|---|---|---|
| dig-dig | 402 | 0.018 | 0.018 | 0.035 | 0.049 | 0.094 |
| mixed (one scan) | 9 | 0.135 | 0.135 | 0.135 | 0.135 | 0.146 |

## MinHash Jaccard, unrelated same-type pairs (n=402): p50=0.000, p99=0.093, p99.9=0.147, max=0.156

## Labelled pairs in this split: 0 (near: 0)

| pair | mode | class | hamming_frac | jaccard |
|---|---|---|---|---|

## Visual threshold trade-off (pHash normalised Hamming <= T)

| T | labelled near pairs caught | unrelated pairs linked: dig-dig | mixed | scan-scan | distinct clean docs linked |
|---|---|---|---|---|---|
| 0.02 | 0/0 | 2 | 0 | 0 | 4 |
| 0.03 | 0/0 | 3 | 0 | 0 | 5 |
| 0.05 | 0/0 | 21 | 0 | 0 | 23 |
| 0.07 | 0/0 | 88 | 0 | 0 | 54 |
| 0.09 | 0/0 | 188 | 0 | 0 | 70 |
| 0.1 | 0/0 | 225 | 0 | 0 | 73 |
| 0.11 | 0/0 | 240 | 0 | 0 | 74 |
