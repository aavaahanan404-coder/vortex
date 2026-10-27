from fastapi.testclient import TestClient
from harness import corpus_io as c
from harness.fixture_server import app

cl = TestClient(app)


def test_applications():
    r = cl.get("/applications").json()
    assert len(r) == len(c.institutions()) and {"inst_id", "score", "category"} <= set(r[0])


def test_application_detail_and_404():
    e = c.first_doc(label="spliced_expiry_forgery")       # a hard Tier-3 forensic label
    r = cl.get(f"/applications/{e['inst_id']}").json()
    assert r["category"] == "high_anomaly" and r["reasons"]
    assert cl.get("/applications/NOPE").status_code == 404


def test_queue_sorted_and_excludes_clear():
    q = cl.get("/queue").json()
    assert q and all(a["category"] != "auto_clear_candidate" for a in q)
    assert [a["score"] for a in q] == sorted((a["score"] for a in q), reverse=True)


def test_findings():
    e = c.first_doc(label="spliced_expiry_forgery")
    f = cl.get(f"/findings/{e['doc_id']}").json()
    assert any(x["id"] == "spliced_expiry_forgery" for x in f)
    assert cl.get(f"/findings/{c.first_doc(clean_only=True)['doc_id']}").json() == []
    assert cl.get("/findings/NOPE").status_code == 404
