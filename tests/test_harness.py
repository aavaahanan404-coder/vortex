import collections

import pytest

from harness import corpus_io as c
from harness import run_module as rm


def test_split_is_deterministic_and_roughly_70_15_15():
    ids = [i["inst_id"] for i in c.institutions()]
    sp = collections.Counter(c.split_of(i) for i in ids)
    assert set(sp) == {"train", "val", "test"} and all(c.split_of(i) == c.split_of(i) for i in ids)
    n = len(ids)
    assert 0.55 * n <= sp["train"] <= 0.85 * n and sp["val"] >= 1 and sp["test"] >= 1


def test_set_corpus_switches_and_restores():
    base = c.corpus_root()
    other = "corpus_test_v2" if base.name != "corpus_test_v2" else "corpus_v2"
    n0 = len(list(c.iter_docs()))
    try:
        c.set_corpus(other)
        assert c.corpus_root().name == other and len(list(c.iter_docs())) != 0
    finally:
        c.set_corpus(base)
    assert c.corpus_root() == base and len(list(c.iter_docs())) == n0


def test_clean_excludes_labelled_and_graded_set_is_separate():
    clean = list(c.iter_docs(clean_only=True))
    assert clean and all(not any(l["kind"] != "control" for l in e["labels"]) for e in clean)
    graded = list(c.iter_graded())
    assert graded and not ({g["doc_id"] for g in graded} & {e["doc_id"] for e in c.iter_docs()})
    assert all(g["labels"][0]["name"] == "graded_blur" for g in graded)


def test_first_doc_filters_and_missing_raises():
    assert c.first_doc("fire_cert", digital=True)["doc_type"] == "fire_cert"
    with pytest.raises(LookupError):
        c.first_doc("no_such_type")


def test_registry_shape():
    for name, spec in rm.REGISTRY.items():
        assert {"run", "applies", "targets"} <= set(spec), name


def test_summarize_counts_weak_evidence_and_splits():
    rows = [{"doc_id": "a", "split": "train", "clean": True, "labels": [], "ids": {"covered_text"},
             "active": {"covered_text"}, "expected": set()},
            {"doc_id": "b", "split": "test", "clean": False, "labels": ["spliced_expiry_forgery"],
             "ids": {"covered_text"}, "active": {"covered_text"}, "expected": set()},
            {"doc_id": "c", "split": "test", "clean": False, "labels": ["editor_metadata"], "ids": set(),
             "active": set(), "expected": set()}]
    r = rm.summarize("pdf_forensics", rows, "test")
    assert r["det"] == {"spliced_expiry_forgery": [1, 1], "editor_metadata": [0, 1]} and r["fp"] == [0, 0]
    assert len(r["misses"]) == 1
    allr = rm.summarize("pdf_forensics", rows)
    assert allr["fp"] == [1, 1] and allr["fp_by_id"]["covered_text"] == 1
    assert "weak evidence" in rm.render(allr)
