# SIH26261 corpus v2 (all data synthetic)
corpus_v2/        main corpus (seed 26261, 100 institutions) - see CHANGELOG_v2.md
corpus_test_v2/   held-out corpus (seed 777) - evaluation only
Regenerate: python corpus_v2/docgen.py --n 100 --seed 26261 --out my_corpus  (options: --app-year, --today, --graded-sources, --no-watermark, --norms)
