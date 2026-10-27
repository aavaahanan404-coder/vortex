"""Seeded demo of the whole stack: the FastAPI gateway in-process on a fresh data directory, filled with a few corpus_v2 institutions.

Input : corpus_v2 (build/tune corpus; never the held-out one). Output: a running API on :8000 (the Next.js app proxies /backend to it).
Seeds : SYN-I0031 spliced + SYN-I0010 incremental edit + SYN-I0001 rules defect, all SUBMITTED (the first two assigned to reviewers);
        SYN-I0064 a draft with documents missing / a problem to fix; SYN-I0003 has no application yet (the "start" form).
Not a deployment: static dev bearer tokens (printed below), a throwaway data directory (data_demo/), mock registries.

    python -m harness.demo [--port 8000] [--keep]     (--keep reuses data_demo/ instead of re-seeding)
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from harness import corpus_io as c

ROOT = Path(__file__).resolve().parent.parent
DOC_TYPES = ["society_reg", "land_doc", "building_plan", "fire_cert", "aff_letter", "faculty_list", "infra_statement", "financial", "affidavit", "eoa"]
SUBMITTED = ["SYN-I0031", "SYN-I0010", "SYN-I0001"]
ASSIGN = ["SYN-I0031", "SYN-I0010"]
DRAFT = {"SYN-I0064": ("society_reg", "land_doc", "fire_cert", "faculty_list")}
EMPTY = ["SYN-I0003"]


def tokens(insts: list[str]) -> dict:
    t = {f"demo-applicant-{i[-5:].lower()}": {"role": "applicant", "inst_id": i} for i in insts}
    t.update({f"demo-reviewer-{u}": {"role": "reviewer", "user": u} for u in ("ana", "ben", "chi", "dev")})
    t.update({"demo-supervisor": {"role": "supervisor", "user": "sup"}, "demo-admin": {"role": "admin", "user": "adm"}})
    return t


def upload(cl, inst: dict, docs: list[dict], only=None) -> None:
    tok = {"Authorization": f"Bearer demo-applicant-{inst['inst_id'][-5:].lower()}"}
    r = cl.post("/apply/application", headers=tok, json={"name": inst["name"], "application_date": inst["application_date"],
                                                         "application_type": inst["application_type"], "programme_type": inst["type"]})
    assert r.status_code == 200, r.text
    for e in docs:
        if only is not None and e["doc_type"] not in only:
            continue
        with open(e["path"], "rb") as fh:
            r = cl.put(f"/apply/documents/{e['doc_type']}", headers=tok, files={"file": (e["file"].split("/")[-1], fh, "application/pdf")})
        if r.status_code != 200:
            print(f"  ! {inst['inst_id']} {e['doc_type']}: {r.status_code} {r.text[:120]}")


def seed(cl, app_ids: list[str]) -> None:
    by = {}
    for e in c.iter_docs():
        by.setdefault(e["inst_id"], []).append(e)
    insts = {i["inst_id"]: i for i in c.institutions()}
    for iid in SUBMITTED:
        print(f"seeding {iid} (submitted)")
        upload(cl, insts[iid], by[iid])
        r = cl.post("/apply/submit", headers={"Authorization": f"Bearer demo-applicant-{iid[-5:].lower()}"})
        print(f"  submit -> {r.status_code} {r.json().get('status', r.json().get('detail', {}).get('status'))}")
    for iid in ASSIGN:
        r = cl.post(f"/review/cases/{iid}/assign", headers={"Authorization": "Bearer demo-supervisor"})
        print(f"  assign {iid} -> {r.status_code} {r.text[:100]}")
    for iid, only in DRAFT.items():
        print(f"seeding {iid} (draft, {len(only)} documents)")
        upload(cl, insts[iid], by[iid], only=only)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    ids = SUBMITTED + list(DRAFT) + EMPTY
    data = ROOT / "data_demo"
    fresh = not (a.keep and data.exists())
    if fresh and data.exists():
        shutil.rmtree(data)
    st = Settings(data, c.corpus_root() / "world" / "world.sqlite", tokens(ids), 25, 26261, DOC_TYPES)
    app = create_app(st)
    if fresh:
        seed(TestClient(app), ids)
    print("\nDemo tokens (paste one on the sign-in page):")
    for k, v in st.tokens.items():
        print(f"  {k:28s} {v['role']:11s} {v.get('inst_id') or v.get('user')}")
    sys.stdout.flush()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
