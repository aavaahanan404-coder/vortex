"""Corpus access for tests and the harness. Read-only; never modifies the corpus.

Corpus root, in order: set_corpus() / harness --corpus, env SIH_CORPUS, ./corpus_v2.
`corpus_v2` is the main (build/tune) corpus, `corpus_test_v2` is HELD OUT (env SIH_CORPUS_TEST
overrides its location). Nothing here hard-codes a document id or a count.

"clean" = a document with no policy/forensic label (control labels such as scan_clean do not
count as defects). The graded-blur set lives in a separate manifest and is never clean.
split_of(inst_id) is the 70/15/15 train/val/test split by md5(inst_id); it is a property of
the institution, so every document of one institution lands in the same split.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

REPO = Path(__file__).resolve().parent.parent
_ROOT: Optional[Path] = None


def resolve_corpus(name: str | Path) -> Path:
    """'corpus_v2' / 'corpus_test_v2' / any path -> absolute directory."""
    s = str(name)
    if s == "corpus_test_v2" and os.environ.get("SIH_CORPUS_TEST"):
        return Path(os.environ["SIH_CORPUS_TEST"]).resolve()
    p = Path(s)
    if not p.is_absolute() and not p.exists():
        p = REPO / s
    return p.resolve()


def set_corpus(name: str | Path | None) -> Path:
    """Select the corpus for this process (None = back to env / default)."""
    global _ROOT
    _ROOT = resolve_corpus(name) if name else None
    return corpus_root()


def corpus_root() -> Path:
    if _ROOT is not None:
        return _ROOT
    return Path(os.environ.get("SIH_CORPUS", REPO / "corpus_v2")).resolve()


def _read_jsonl(p: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                e = json.loads(line)
                out[e["doc_id"]] = e
    return out


@lru_cache(maxsize=8)
def _manifest_for(root: Path) -> dict[str, dict]:
    return _read_jsonl(root / "manifest.jsonl")


@lru_cache(maxsize=8)
def _graded_for(root: Path) -> dict[str, dict]:
    return _read_jsonl(root / "manifest_graded_blur.jsonl")


def _manifest() -> dict[str, dict]:
    return _manifest_for(corpus_root())


def split_of(inst_id: str) -> str:
    """Deterministic 70/15/15 split by md5(inst_id): 'train' | 'val' | 'test'."""
    h = int(hashlib.md5(inst_id.encode()).hexdigest(), 16) % 100
    return "train" if h < 70 else ("val" if h < 85 else "test")


def is_clean(entry: dict) -> bool:
    return not any(l.get("kind") != "control" for l in entry["labels"])


def is_scan(entry: dict) -> bool:
    return bool(entry.get("is_scan"))


def iter_docs(doc_type: Optional[str] = None, label: Optional[str] = None,
              clean_only: bool = False, split: Optional[str] = None) -> Iterator[dict]:
    """Yield main-manifest entries, each with an added absolute 'path' key."""
    root = corpus_root()
    for e in _manifest().values():
        if doc_type and e["doc_type"] != doc_type:
            continue
        if label and not any(l["name"] == label for l in e["labels"]):
            continue
        if clean_only and not is_clean(e):
            continue
        if split and split_of(e["inst_id"]) != split:
            continue
        yield {**e, "path": str(root / e["file"])}


def iter_graded(split: Optional[str] = None, doc_type: Optional[str] = None) -> Iterator[dict]:
    """Yield graded-blur entries (calibration only; never counted as clean documents)."""
    root = corpus_root()
    for e in _graded_for(root).values():
        if split and split_of(e["inst_id"]) != split:
            continue
        if doc_type and e["doc_type"] != doc_type:
            continue
        yield {**e, "path": str(root / e["file"])}


def get_entry(doc_id: str) -> dict:
    e = _manifest()[doc_id]
    return {**e, "path": str(corpus_root() / e["file"])}


def first_doc(doc_type: Optional[str] = None, label: Optional[str] = None,
              clean_only: bool = False, digital: Optional[bool] = None,
              has_field: Optional[str] = None) -> dict:
    """First matching manifest entry (stable manifest order); raises LookupError if none.
    Tests use this instead of hard-coded document ids."""
    for e in iter_docs(doc_type, label, clean_only):
        if digital is not None and is_scan(e) == digital:
            continue
        if has_field and has_field not in e["fields"]:
            continue
        return e
    raise LookupError(f"no document matches doc_type={doc_type} label={label}")


def institutions() -> list[dict]:
    with open(corpus_root() / "institutions.json", encoding="utf-8") as fh:
        return json.load(fh)


def application_date(inst_id: str) -> str:
    """Application date (ISO) of an institution. Never compare against the wall clock."""
    return next(i["application_date"] for i in institutions() if i["inst_id"] == inst_id)


def truth_fields(doc_id: str) -> dict:
    """Ground-truth fields: {key: {value, bbox, page, ...}}."""
    return _manifest()[doc_id]["fields"]


def world_conn() -> sqlite3.Connection:
    """Fresh read-only connection to the mock authority DB (caller closes it)."""
    p = corpus_root() / "world" / "world.sqlite"
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def issuer_keys() -> dict[str, str]:
    con = world_conn()
    try:
        return dict(con.execute("select issuer, secret_hex from issuer_keys"))
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    if "--dump-keys" in sys.argv:          # python -m harness.corpus_io --dump-keys > keys.json
        print(json.dumps(issuer_keys(), indent=2))
