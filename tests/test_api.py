import json
import os
import re

import pymupdf
import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from harness import corpus_io as c

DOC_TYPES = ["society_reg", "land_doc", "building_plan", "fire_cert", "aff_letter", "faculty_list", "infra_statement", "financial", "affidavit", "eoa"]
FORBIDDEN = ("score", "category", "high_anomaly", "needs_human", "covered_text", "alt_values", "registry_mismatch", "exact_duplicate", "qr_date_mismatch",
             "qr_bad_signature", "font_anomaly", "editor_metadata", "R016", "R013", "ela_suspicious", "text_near_duplicate", "visual_near_duplicate")


def H(tok): return {"Authorization": f"Bearer {tok}"}


def digital_institution(pred):
    """First institution (manifest order) whose documents are all digital PDFs and whose mutations satisfy pred."""
    by = {}
    for e in c.iter_docs():
        by.setdefault(e["inst_id"], []).append(e)
    for i in c.institutions():
        docs = by[i["inst_id"]]
        if all(not c.is_scan(e) for e in docs) and pred(i):
            return i, docs
    pytest.skip("corpus has no such institution")


def make_client(tmp_path, insts, max_mb=25, clock=None):
    tokens = {f"app-{i['inst_id']}": {"role": "applicant", "inst_id": i["inst_id"]} for i in insts}
    tokens.update({"rev-ana": {"role": "reviewer", "user": "ana"}, "rev-ben": {"role": "reviewer", "user": "ben"},
                   "rev-chi": {"role": "reviewer", "user": "chi"}, "rev-dev": {"role": "reviewer", "user": "dev"},
                   "sup": {"role": "supervisor", "user": "sup"}, "adm": {"role": "admin", "user": "adm"}})
    now = {"t": "2026-02-01T10:00:00+00:00"}
    st = Settings(tmp_path / "data", c.corpus_root() / "world" / "world.sqlite", tokens, max_mb, 7, DOC_TYPES, clock or (lambda: now["t"]))
    cl = TestClient(create_app(st))
    cl.now = now
    return cl


def apply_all(cl, inst, docs, skip=()):
    tok = H(f"app-{inst['inst_id']}")
    r = cl.post("/apply/application", headers=tok, json={"name": inst["name"], "application_date": inst["application_date"],
                                                         "application_type": inst["application_type"], "programme_type": inst["type"]})
    assert r.status_code == 200, r.text
    out = {}
    for e in docs:
        if e["doc_type"] in skip:
            continue
        with open(e["path"], "rb") as fh:
            out[e["doc_type"]] = cl.put(f"/apply/documents/{e['doc_type']}", headers=tok, files={"file": (e["file"].split("/")[-1], fh, "application/pdf")})
    return out


@pytest.fixture
def clean():
    return digital_institution(lambda i: not i["mutations"] and not i["documents_missing"])


# ----------------------------------------------------------------------------- auth and roles
def test_authentication_and_role_separation(tmp_path, clean):
    inst, docs = clean
    cl = make_client(tmp_path, [inst])
    assert cl.get("/health").json()["ok"]
    assert cl.get("/apply/application").status_code == 401
    assert cl.get("/apply/application", headers=H("nope")).status_code == 401
    app_t = H(f"app-{inst['inst_id']}")
    for url in ("/review/queue", "/review/my-cases", "/admin/audit/verify"):
        assert cl.get(url, headers=app_t).status_code == 403                    # an applicant reaches no staff endpoint
    assert cl.get("/apply/application", headers=H("rev-ana")).status_code == 403
    assert cl.get("/review/queue", headers=H("rev-ana")).status_code == 403     # a reviewer does not see the queue (it carries scores)
    assert cl.get("/review/queue", headers=H("sup")).status_code == 200
    assert cl.post("/admin/purge", headers=H("sup"), json={}).status_code == 403


# ----------------------------------------------------------------------------- applicant flow
def test_applicant_flow_dry_run_submit_and_lockdown(tmp_path, clean):
    inst, docs = clean
    cl = make_client(tmp_path, [inst])
    tok = H(f"app-{inst['inst_id']}")
    assert cl.get("/apply/application", headers=tok).status_code == 404
    ups = apply_all(cl, inst, docs)
    assert all(r.status_code == 200 for r in ups.values()), {k: r.text for k, r in ups.items() if r.status_code != 200}
    one = ups["fire_cert"].json()
    assert one["version"] == 1 and one["status"] == "ok" and one["prefill"]["cert_no"]["value"] and one["expires_after_hours"] == 48
    assert not [w for w in FORBIDDEN if w in json.dumps(one)]
    assert cl.post("/apply/application", headers=tok, json={"application_date": "2026-01-01"}).status_code == 409
    dr = cl.post("/apply/dry-run", headers=tok).json()
    assert dr["status"] == "ready_to_submit" and dr["mode"] == "dry_run" and not [w for w in FORBIDDEN if w in json.dumps(dr)]
    assert cl.get("/review/queue", headers=H("sup")).json() == []                  # a dry run left no trace
    sub = cl.post("/apply/submit", headers=tok)
    assert sub.status_code == 200 and sub.json()["status"] == "received" and not [w for w in FORBIDDEN if w in sub.text]
    assert cl.post("/apply/submit", headers=tok).status_code == 409                 # no second submission
    with open(docs[0]["path"], "rb") as fh:
        assert cl.put(f"/apply/documents/{docs[0]['doc_type']}", headers=tok, files={"file": ("x.pdf", fh)}).status_code == 409
    me = cl.get("/apply/application", headers=tok).json()
    assert me["status"] == "received" and set(me) <= {"application", "status", "documents", "decision"}
    q = cl.get("/review/queue", headers=H("sup")).json()
    assert len(q) == 1 and q[0]["inst_id"] == inst["inst_id"] and "score" in q[0]


def test_submit_is_refused_while_the_applicant_can_still_fix_something(tmp_path):
    inst, docs = digital_institution(lambda i: i["documents_missing"] and "affidavit" not in i["documents_missing"])
    cl = make_client(tmp_path, [inst])
    apply_all(cl, inst, docs)
    r = cl.post("/apply/submit", headers=H(f"app-{inst['inst_id']}"))
    assert r.status_code == 409 and r.json()["detail"]["status"] == "action_needed"
    codes = [x["code"] for x in r.json()["detail"]["view"]["application_issues"]]
    assert "R010" in codes and not [w for w in FORBIDDEN if w in r.text]
    assert cl.get("/review/queue", headers=H("sup")).json() == []                   # nothing was submitted


def test_bad_uploads_are_rejected_or_explained(tmp_path, clean):
    inst, docs = clean
    cl = make_client(tmp_path, [inst], max_mb=1)
    tok = H(f"app-{inst['inst_id']}")
    cl.post("/apply/application", headers=tok, json={"application_date": inst["application_date"]})
    assert cl.put("/apply/documents/no_such_type", headers=tok, files={"file": ("a.pdf", b"%PDF-1.4")}).status_code == 422
    assert cl.put("/apply/documents/fire_cert", headers=tok, files={"file": ("a.pdf", b"x" * (2 * 1024 * 1024))}).status_code == 413
    r = cl.put("/apply/documents/fire_cert", headers=tok, files={"file": ("../../evil.pdf", b"this is not a pdf")}).json()
    assert r["status"] == "needs_attention" and r["issues"][0]["code"] == "file_unreadable" and "upload a PDF or a clear photo" in r["issues"][0]["message"]
    stored = [p.name for p in (tmp_path / "data" / "drafts").iterdir() if not p.name.endswith(".meta.json")]
    assert stored and all(re.fullmatch(r"[A-Za-z0-9_\-]+__[a-z_]+__v\d+\.[a-z0-9]+", n) for n in stored)   # the client's file name never becomes a path
    assert cl.put("/apply/documents/fire_cert", headers=H("rev-ana"), files={"file": ("a.pdf", b"x")}).status_code == 403


def test_two_applicants_are_isolated(tmp_path):
    a, da = digital_institution(lambda i: not i["mutations"] and not i["documents_missing"])
    b, db = digital_institution(lambda i: i["inst_id"] != a["inst_id"] and not i["mutations"] and not i["documents_missing"])
    cl = make_client(tmp_path, [a, b])
    apply_all(cl, a, da, skip=("faculty_list",)); apply_all(cl, b, db)
    ma = cl.get("/apply/application", headers=H(f"app-{a['inst_id']}")).json()
    mb = cl.get("/apply/application", headers=H(f"app-{b['inst_id']}")).json()
    assert ma["application"] == a["inst_id"] and mb["application"] == b["inst_id"] and "faculty_list" not in ma["documents"] and "faculty_list" in mb["documents"]
    assert b["inst_id"] not in json.dumps(ma) and a["inst_id"] not in json.dumps(mb)


# ----------------------------------------------------------------------------- review: blind double review
def forged_case(tmp_path):
    inst, docs = digital_institution(lambda i: "spliced_expiry_forgery" in i["mutations"] and not i["documents_missing"])
    cl = make_client(tmp_path, [inst])
    apply_all(cl, inst, docs)
    r = cl.post("/apply/submit", headers=H(f"app-{inst['inst_id']}"))
    assert r.status_code == 200, r.text
    return cl, inst, r


def test_forgery_is_invisible_to_the_applicant_and_top_of_the_staff_queue(tmp_path):
    cl, inst, sub = forged_case(tmp_path)
    assert sub.json()["status"] == "received" and not [w for w in FORBIDDEN if w in sub.text]
    q = cl.get("/review/queue", headers=H("sup")).json()
    assert q[0]["inst_id"] == inst["inst_id"] and q[0]["category"] == "high_anomaly" and q[0]["double_review"] is True and q[0]["score"] > 0.8
    full = cl.get(f"/review/cases/{inst['inst_id']}", headers=H("sup")).json()
    ids = {f["id"] for f in full["result"]["findings"] if f["kind"] != "info"}
    assert {"covered_text", "qr_date_mismatch"} <= ids


def test_blind_double_review_with_a_tie_breaker(tmp_path):
    cl, inst, _ = forged_case(tmp_path)
    i = inst["inst_id"]
    assert cl.post(f"/review/cases/{i}/assign", headers=H("rev-ana")).status_code == 403
    assert cl.post(f"/review/cases/{i}/assign", headers=H("sup")).json()["n_reviewers"] == 2
    assert cl.post(f"/review/cases/{i}/assign", headers=H("sup")).status_code == 409
    mine = {u: cl.get("/review/my-cases", headers=H(f"rev-{u}")).json() for u in ("ana", "ben", "chi", "dev")}
    a, b = [u for u, v in mine.items() if v]
    others = [u for u in mine if u not in (a, b)]
    assert cl.get(f"/review/cases/{i}", headers=H(f"rev-{others[0]}")).status_code == 403          # not assigned: nothing
    va = cl.get(f"/review/cases/{i}", headers=H(f"rev-{a}")).json()
    assert va["score"] is None and va["category"] is None and va["reasons"] is None and "findings_with_scores" not in va
    assert va["findings"] and all(set(f) == {"id", "doc_id", "tier", "message", "bbox", "evidence"} for f in va["findings"])       # no p, no kind
    pdf = cl.get(f"/review/cases/{i}/evidence.pdf", headers=H(f"rev-{a}"))
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    txt = "\n".join(p.get_text() for p in pymupdf.open(stream=pdf.content, filetype="pdf"))
    assert "covered_text" in txt and "Risk score" not in txt and not re.search(r"p=\d\.\d\d", txt)                                  # blind report
    r1 = cl.post(f"/review/cases/{i}/decision", headers=H(f"rev-{a}"), json={"decision": "reject", "notes": "date was edited"})
    assert r1.status_code == 200 and r1.json()["score"] is None and r1.json()["phase"] == "blind"
    assert cl.post(f"/review/cases/{i}/decision", headers=H(f"rev-{a}"), json={"decision": "approve"}).status_code == 409   # one vote only
    assert cl.get(f"/review/cases/{i}", headers=H(f"rev-{b}")).json()["score"] is None                  # colleague's vote and the score still hidden
    assert "decisions" not in cl.get(f"/review/cases/{i}", headers=H(f"rev-{b}")).json()
    r2 = cl.post(f"/review/cases/{i}/decision", headers=H(f"rev-{b}"), json={"decision": "approve", "notes": "looks fine"}).json()
    assert r2["phase"] == "awaiting_third" and r2["decisions"] == {a: "reject", b: "approve"} and r2["score"] > 0.8        # the two now see everything
    third = next(u for u in ("ana", "ben", "chi", "dev") if cl.get("/review/my-cases", headers=H(f"rev-{u}")).json() and u not in (a, b))
    vt = cl.get(f"/review/cases/{i}", headers=H(f"rev-{third}")).json()
    assert vt["score"] is None and "decisions" not in vt                                                   # the tie-breaker is blind
    r3 = cl.post(f"/review/cases/{i}/decision", headers=H(f"rev-{third}"), json={"decision": "reject", "notes": ""}).json()
    assert r3["phase"] == "resolved" and r3["outcome"] == "reject"
    me = cl.get("/apply/application", headers=H(f"app-{i}")).json()
    assert me["status"] == "decision_made" and me["decision"] == "reject"
    assert cl.get("/admin/audit/verify", headers=H("adm")).json()["intact"] is True


def test_request_info_reopens_the_application_and_a_reupload_creates_a_diff(tmp_path, clean):
    inst, docs = clean
    cl = make_client(tmp_path, [inst])
    apply_all(cl, inst, docs)
    tok = H(f"app-{inst['inst_id']}"); i = inst["inst_id"]
    assert cl.post("/apply/submit", headers=tok).status_code == 200
    assert cl.post(f"/review/cases/{i}/assign", headers=H("sup")).json()["n_reviewers"] in (1, 2)
    who = [u for u in ("ana", "ben", "chi", "dev") if cl.get("/review/my-cases", headers=H(f"rev-{u}")).json()]
    for u in who:
        cl.post(f"/review/cases/{i}/decision", headers=H(f"rev-{u}"), json={"decision": "request_info", "notes": "need a newer fire certificate"})
    me = cl.get("/apply/application", headers=tok).json()
    assert me["status"] == "info_requested"
    fire = next(e for e in docs if e["doc_type"] == "fire_cert")
    edited = tmp_path / "fire_v2.pdf"
    with pymupdf.open(fire["path"]) as d:
        d[0].insert_text((50, 700), "Re-issued copy", fontname="helv", fontsize=9)
        d.save(str(edited))
    with open(edited, "rb") as fh:
        r = cl.put("/apply/documents/fire_cert", headers=tok, files={"file": ("fire_v2.pdf", fh)})
    assert r.status_code == 200 and r.json()["version"] == 2
    d = cl.get(f"/review/cases/{i}/documents/fire_cert/diff", headers=H("sup")).json()
    assert d["from"] == 1 and d["to"] == 2 and d["finding"]["id"] == "diff_summary" and "Re-issued copy" in json.dumps(d)
    ev = d["finding"]["evidence"]                                       # the review page reads exactly these fields (frontend toDiff)
    assert isinstance(ev["text_changes"], list) and isinstance(ev["changed_pages"], list) and ev["n_text_changes"] >= 1
    assert all(len(t["new_bbox"] or [0, 0, 0, 0]) == 4 and {"page", "old", "new"} <= set(t) for t in ev["text_changes"])
    assert cl.get(f"/review/cases/{i}/documents/fire_cert/diff", headers=tok).status_code == 403          # the applicant never sees diffs
    assert cl.get(f"/review/cases/{i}/documents/land_doc/diff", headers=H("sup")).status_code == 404


# ----------------------------------------------------------------------------- 48-hour TTL
def test_drafts_expire_after_48_hours_but_submitted_files_do_not(tmp_path):
    a, da = digital_institution(lambda i: not i["mutations"] and not i["documents_missing"])
    b, db = digital_institution(lambda i: i["inst_id"] != a["inst_id"] and not i["mutations"] and not i["documents_missing"])
    cl = make_client(tmp_path, [a, b])
    apply_all(cl, a, da); apply_all(cl, b, db)
    assert cl.post("/apply/submit", headers=H(f"app-{a['inst_id']}")).status_code == 200            # a is submitted, b is still a draft
    drafts = tmp_path / "data" / "drafts"
    n_b = len([p for p in drafts.iterdir() if not p.name.endswith(".meta.json")])
    assert n_b == len(db) and not [p for p in drafts.iterdir() if a["inst_id"] in p.name]             # a's files left the draft area
    early = cl.post("/admin/purge", headers=H("adm"), json={"now": "2026-02-02T09:00:00+00:00"}).json()
    assert early["deleted"] == []
    late = cl.post("/admin/purge", headers=H("adm"), json={"now": "2026-02-03T10:00:01+00:00"}).json()   # 48 h + 1 s after the uploads
    assert len(late["deleted"]) == len(db) and not any(not p.name.endswith(".meta.json") for p in drafts.iterdir())
    sub = tmp_path / "data" / "submitted" / a["inst_id"]
    assert len(list(sub.rglob("*.pdf"))) == len(da)                                                  # submitted records survive the purge


def test_audit_log_records_the_flow_and_stays_intact(tmp_path, clean):
    inst, docs = clean
    cl = make_client(tmp_path, [inst])
    apply_all(cl, inst, docs)
    cl.post("/apply/submit", headers=H(f"app-{inst['inst_id']}"))
    v = cl.get("/admin/audit/verify", headers=H("adm")).json()
    assert v["intact"] and v["entries"] >= 3
    events = [json.loads(r[0])["event"] for r in cl.app.state.store.conn.execute("select entry_json from audit_log order by seq")]
    assert events[0] == "application_created" and "pipeline_processed" in events and "submitted" in events


# ----------------------------------------------------------------------------- /me, photo tools, page images
def test_me_reports_the_role_and_nothing_else(tmp_path, clean):
    inst, _ = clean
    cl = make_client(tmp_path, [inst])
    assert cl.get("/me").status_code == 401
    assert cl.get("/me", headers=H(f"app-{inst['inst_id']}")).json() == {"role": "applicant", "inst_id": inst["inst_id"]}
    assert cl.get("/me", headers=H("rev-ana")).json() == {"role": "reviewer", "user": "ana"}
    assert cl.get("/me", headers=H("sup")).json()["role"] == "supervisor" and cl.get("/me", headers=H("adm")).json()["role"] == "admin"


def test_photo_detect_and_warp_for_the_crop_tool(tmp_path, clean):
    import cv2
    import numpy as np
    from harness.make_photo import photograph
    from modules import perspective as P
    inst, _ = clean
    cl = make_client(tmp_path, [inst])
    tok = H(f"app-{inst['inst_id']}")
    scan = next(e for e in c.iter_docs(clean_only=True) if c.is_scan(e))
    photo, true = photograph(P.load_image(scan["path"]), seed=3)
    ok, jpg = cv2.imencode(".jpg", photo)
    r = cl.post("/apply/photo/detect", headers=tok, files={"file": ("p.jpg", jpg.tobytes())}).json()
    assert r["found"] and r["width"] == photo.shape[1] and len(r["corners"]) == 4 and "straighten" in r["message"]
    diag = float(np.hypot(*(true[2] - true[0])))
    assert float(np.mean(np.linalg.norm(np.array(r["corners"]) - P.order_corners(true), axis=1))) < 0.01 * diag
    w = cl.post("/apply/photo/warp", headers=tok, params={"corners": json.dumps(r["corners"])}, files={"file": ("p.jpg", jpg.tobytes())})
    assert w.status_code == 200 and w.headers["content-type"] == "image/png"
    out = cv2.imdecode(np.frombuffer(w.content, np.uint8), cv2.IMREAD_COLOR)
    assert out.shape[1] == P.load_config()["output_width"] and out.mean() > 120
    # hand-adjusted corners (the user dragged a handle) are accepted; nonsense is refused
    assert cl.post("/apply/photo/warp", headers=tok, params={"corners": "[[0,0],[1,1]]"}, files={"file": ("p.jpg", jpg.tobytes())}).status_code == 422
    assert cl.post("/apply/photo/warp", headers=tok, params={"corners": "[[1,1],[2,1],[2,2],[1,2]]"}, files={"file": ("p.jpg", jpg.tobytes())}).status_code == 422
    assert cl.post("/apply/photo/detect", headers=tok, files={"file": ("x.jpg", b"not an image")}).status_code == 422
    noise = np.clip(np.random.RandomState(1).normal(120, 40, (600, 500, 3)), 0, 255).astype(np.uint8)
    nr = cl.post("/apply/photo/detect", headers=tok, files={"file": ("n.png", cv2.imencode(".png", noise)[1].tobytes())}).json()
    assert nr["found"] is False and len(nr["corners"]) == 4 and "could not find" in nr["message"]
    assert cl.post("/apply/photo/detect", headers=H("rev-ana"), files={"file": ("p.jpg", jpg.tobytes())}).status_code == 403
    assert not list((tmp_path / "data" / "drafts").glob("*"))                                       # nothing was stored


def test_page_images_follow_the_case_access_rules(tmp_path):
    cl, inst, _ = forged_case(tmp_path)
    i = inst["inst_id"]
    url = f"/review/cases/{i}/documents/fire_cert/page/1.png"
    r = cl.get(url, headers=H("sup"))
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert cl.get(url, headers=H("rev-ana")).status_code == 403                                      # reviewers need an assignment
    cl.post(f"/review/cases/{i}/assign", headers=H("sup"))
    who = next(u for u in ("ana", "ben", "chi", "dev") if cl.get("/review/my-cases", headers=H(f"rev-{u}")).json())
    assert cl.get(url, headers=H(f"rev-{who}")).status_code == 200
    assert cl.get(f"/review/cases/{i}/documents/fire_cert/page/9.png", headers=H("sup")).status_code == 404
    assert cl.get(f"/review/cases/{i}/documents/no_such/page/1.png", headers=H("sup")).status_code == 404
    assert cl.get(url, headers=H(f"app-{i}")).status_code == 403
    small, big = (len(cl.get(url + f"?dpi={d}", headers=H("sup")).content) for d in (50, 5000))      # dpi is clamped, not trusted
    assert small < big < 5_000_000
    v = cl.get(f"/review/cases/{i}", headers=H(f"rev-{who}")).json()
    fire = next(d for d in v["documents"] if d["doc_type"] == "fire_cert")
    assert fire["pages"] and abs(fire["pages"][0]["w"] - 595.28) < 1 and cl.get(f"/review/cases/{i}", headers=H("sup")).json()["pages"]["fire_cert"]


# ----------------------------------------------------------------------------- rule changer (/admin/rules)
def rule_pdf(tmp_path, name, norm_rows=(), rule_rows=(), meta=None):
    from modules import rule_import as RI
    p = tmp_path / name
    RI.write_rule_pdf(p, meta or {}, [list(r) for r in norm_rows], [list(r) for r in rule_rows])
    return p


def up(cl, p, who="adm", name=None):
    return cl.post("/admin/rules/upload", headers=H(who), files={"file": (name or p.name, p.read_bytes(), "application/pdf")})


def test_rule_changer_is_admin_only_and_refuses_what_is_not_a_structured_pdf(tmp_path):
    cl = make_client(tmp_path, [])
    for who in ("rev-ana", "sup"):
        assert cl.get("/admin/rules", headers=H(who)).status_code == 403
        assert cl.get("/admin/rules/template.pdf", headers=H(who)).status_code == 403
        assert cl.post("/admin/rules/upload", headers=H(who), files={"file": ("r.pdf", b"%PDF-1.4")}).status_code == 403
    assert cl.post("/admin/rules/apply", headers=H("sup"), json={"id": "x"}).status_code == 403
    assert cl.get("/admin/rules").status_code == 401
    assert cl.post("/admin/rules/upload", headers=H("adm"), files={"file": ("r.pdf", b"hello")}).status_code == 422
    doc = pymupdf.open(); doc.new_page().insert_text((50, 80), "Faculty shall be one per fifteen students.")
    r = cl.post("/admin/rules/upload", headers=H("adm"), files={"file": ("prose.pdf", doc.tobytes())})
    assert r.status_code == 422 and "unstructured" in r.json()["detail"] and "AI" in r.json()["detail"]
    assert cl.post("/admin/rules/apply", headers=H("adm"), json={"id": "deadbeef"}).status_code == 409
    assert cl.post("/admin/rules/revert", headers=H("adm"), json={"version": 7}).status_code == 404


def test_rule_changer_preview_apply_use_and_restore(tmp_path):
    cl, inst, _ = forged_case(tmp_path)
    prog = inst["type"]                                                                       # the case's own programme type (differs per corpus)
    before = cl.app.state.pipeline.norms[prog]["students_per_faculty"]
    assert before > 5
    s = cl.get("/admin/rules", headers=H("adm")).json()
    assert s["version"] == 0 and s["modified"] is False and s["norms_version"] == "illustrative-v0" and s["norms_validated"] is False and len(s["rules"]) == 16 and s["history"] == []
    t = cl.get("/admin/rules/template.pdf", headers=H("adm"))
    assert t.status_code == 200 and t.content[:4] == b"%PDF"
    same = cl.post("/admin/rules/upload", headers=H("adm"), files={"file": ("t.pdf", t.content)}).json()          # the unedited template changes nothing
    assert same["changes"] == [] and same["rejected"] == [] and same["impact"] is None
    p = rule_pdf(tmp_path, "u.pdf", [(prog, "students_per_faculty", "5", "Clause 5.1"), (prog, "min_lease_years", "x", "")], [("R001", "p", "0.9", "")],
                 {"norms_version": "hb-2025", "validated": False})
    pre = up(cl, p).json()
    assert [c["direction"] for c in pre["changes"]] == ["decrease", "increase"] and len(pre["rejected"]) == 1 and pre["needs_ack"]
    assert pre["impact"]["cases_checked"] == 1 and "R001" in pre["impact"]["rule_changes"]                         # the stored case would now fail R001
    assert "new_overrides" not in pre
    assert cl.get("/admin/rules", headers=H("adm")).json()["version"] == 0                                         # a preview changes nothing
    assert cl.post("/admin/rules/apply", headers=H("adm"), json={"id": pre["id"]}).status_code == 409             # -67% needs a yes
    after = cl.post("/admin/rules/apply", headers=H("adm"), json={"id": pre["id"], "acknowledge": True})
    assert after.status_code == 200
    s = after.json()
    assert s["version"] == 1 and s["norms_version"] == "hb-2025" and s["history"][0]["by"] == "adm" and s["history"][0]["n_changes"] == 2
    assert next(n for n in s["norms"] if n["key"] == "students_per_faculty" and n["programme_type"] == prog)["value"] == 5
    assert next(r for r in s["rules"] if r["id"] == "R001")["p"] == 0.9
    live = cl.app.state.pipeline
    assert live.norms[prog]["students_per_faculty"] == 5 and {r["id"]: r for r in live.rulebook["rules"]}["R001"]["p"] == 0.9   # the next case uses it
    assert cl.post("/admin/rules/apply", headers=H("adm"), json={"id": pre["id"], "acknowledge": True}).status_code == 409           # a used preview is stale
    assert cl.get("/admin/audit/verify", headers=H("adm")).json()["intact"] is True
    back = cl.post("/admin/rules/revert", headers=H("adm"), json={"version": 0}).json()
    assert back["version"] == 2 and back["modified"] is False and cl.app.state.pipeline.norms[prog]["students_per_faculty"] == before
    assert [h["kind"] for h in back["history"]] == ["apply", "revert"]
