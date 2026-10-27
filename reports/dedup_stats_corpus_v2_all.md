# dedup pair statistics: corpus_v2, split=all

documents in split: 965; pHash 1024 bits; MinHash 128 perms, 3-word shingles minus boilerplate

## pHash normalised Hamming distance, unrelated same-type pairs

| pair class | n pairs | min | p0.1 | p1 | p5 | median |
|---|---|---|---|---|---|---|
| dig-dig | 43909 | 0.008 | 0.020 | 0.033 | 0.051 | 0.100 |
| mixed (one scan) | 2561 | 0.070 | 0.082 | 0.092 | 0.105 | 0.176 |
| scan-scan | 56 | 0.057 | 0.058 | 0.065 | 0.087 | 0.202 |

## MinHash Jaccard, unrelated same-type pairs (n=43909): p50=0.000, p99=0.078, p99.9=0.180, max=0.602

## Labelled pairs in this split: 5 (near: 3)

| pair | mode | class | hamming_frac | jaccard |
|---|---|---|---|---|
| SYN-I0020-land_doc <-> SYN-I0055-land_doc | near | mixed (one scan) | 0.096 | n/a (no text) |
| SYN-I0034-fire_cert <-> SYN-I0050-fire_cert | exact | dig-dig | 0.000 | 1.000 |
| SYN-I0035-land_doc <-> SYN-I0046-land_doc | near | mixed (one scan) | 0.086 | n/a (no text) |
| SYN-I0053-fire_cert <-> SYN-I0065-fire_cert | exact | dig-dig | 0.000 | 1.000 |
| SYN-I0082-land_doc <-> SYN-I0086-land_doc | near | mixed (one scan) | 0.094 | n/a (no text) |

## Visual threshold trade-off (pHash normalised Hamming <= T)

| T | labelled near pairs caught | unrelated pairs linked: dig-dig | mixed | scan-scan | distinct clean docs linked |
|---|---|---|---|---|---|
| 0.02 | 0/3 | 67 | 0 | 0 | 80 |
| 0.03 | 0/3 | 336 | 0 | 0 | 195 |
| 0.05 | 0/3 | 1982 | 0 | 0 | 542 |
| 0.07 | 0/3 | 8030 | 0 | 1 | 767 |
| 0.09 | 1/3 | 18270 | 17 | 3 | 797 |
| 0.1 | 3/3 | 22139 | 76 | 8 | 803 |
| 0.11 | 3/3 | 24931 | 174 | 11 | 809 |
