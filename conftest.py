"""pytest: select the corpus with --corpus (default: env SIH_CORPUS, else corpus_v2).

    python -m pytest tests -q                               # corpus_v2
    python -m pytest tests -q --corpus corpus_test_v2       # held-out corpus (read-only use)
Tests derive every document from the manifest; none hard-codes a document id or a count.
"""
from harness import corpus_io


def pytest_addoption(parser):
    parser.addoption("--corpus", default=None, help="corpus_v2 | corpus_test_v2 | path")


def pytest_configure(config):
    name = config.getoption("--corpus")
    if name:
        corpus_io.set_corpus(name)
