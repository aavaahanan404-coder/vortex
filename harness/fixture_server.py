"""Fixture API for the frontend team. Findings are DERIVED FROM CORPUS LABELS (not from running
the detectors), so the frontend can start before the pipeline exists.
Run: uvicorn harness.fixture_server:app --reload
Endpoints: GET /applications, /applications/{inst_id}, /queue, /findings/{doc_id}
"""
from __future__ import annotations

import json
from functools import lru_cache

from fastapi import FastAPI, HTTPException

from contracts import Finding
from harness import corpus_io as c
from modules.score import score_findings

app = FastAPI(title="SIH26261 fixture server")

_REASON = {
    "qr_bad_signature": "The QR code's security signature does not match.",
    "qr_date_mismatch": "The printed date differs from the date in the signed QR code.",
    "spliced_expiry_forgery": "Text on the page appears to have been covered and re-typed (validity date).",
    "incremental_edit_forgery": "The file was edited after it was issued: a later revision changes the validity date.",
    "editor_metadata": "The file was last saved by an image/PDF editing tool.",
    "exact_duplicate": "This file is identical to a document submitted by another institution.",
    "visual_near_duplicate": "This document looks almost identical to another institution's document.",
    "copy_move_scan": "A region of the scan appears to be copied from elsewhere on the page.",
    "scan_degraded": "The scan is too blurry or tilted to read reliably.",
}


@lru_cache(maxsize=4)
def _institutions_for(root) -> dict:
    return {i["inst_id"]: i for i in c.institutions()}


def _institutions() -> dict:
    return _institutions_for(c.corpus_root())          # keyed by corpus so --corpus switches work


def _findings(doc_id: str) -> list[Finding]:
    e = c.get_entry(doc_id); out = []
    for l in e["labels"]:
        if l["kind"] == "control":
            continue
        tier = l.get("tier") or 3
        name = l["name"]
        if l["kind"] == "policy":          # policy labels become rule findings in a later batch
            out.append(Finding(f"policy:{name}", "fixture", "soft", 3, 0.5,
                               f"Possible rule problem: {name.replace('_', ' ')}.", doc_id))
            continue
        kind = "soft" if tier == 4 else "hard"
        out.append(Finding(name, "fixture", kind, tier, 0.9 if kind == "hard" else 0.5,
                           _REASON.get(name, name), doc_id,
                           (1, *e["fields"]["valid_to"]["bbox"]) if name in ("spliced_expiry_forgery", "incremental_edit_forgery") and "valid_to" in e["fields"] else None,
                           {"label": l}))
    return out


def _app(inst_id: str) -> dict:
    inst = _institutions()[inst_id]
    docs = [e for e in c.iter_docs() if e["inst_id"] == inst_id]
    fs = [f for e in docs for f in _findings(e["doc_id"])]
    s = score_findings(fs)
    return {"inst_id": inst_id, "name": inst["name"], "state": inst["state"], "type": inst["type"],
            "application_date": inst["application_date"], "documents": [e["doc_id"] for e in docs],
            "missing_documents": inst["documents_missing"], "n_findings": len(fs), **s}


@app.get("/applications")
def applications():
    return [{k: v for k, v in _app(i).items() if k != "reasons"} for i in _institutions()]


@app.get("/applications/{inst_id}")
def application(inst_id: str):
    if inst_id not in _institutions():
        raise HTTPException(404, "unknown application")
    return _app(inst_id)


@app.get("/queue")
def queue():
    items = [_app(i) for i in _institutions()]
    items = [a for a in items if a["category"] != "auto_clear_candidate"]
    return sorted(items, key=lambda a: -a["score"])


@app.get("/findings/{doc_id}")
def findings(doc_id: str):
    try:
        return [f.to_json() for f in _findings(doc_id)]
    except KeyError:
        raise HTTPException(404, "unknown document")
