"""FastAPI gateway: /apply (applicant portal), /review (reviewers and supervisors), /admin.

Run:  uvicorn api.app:app_factory --factory   (settings from config/api.yaml, SIH_DATA_DIR, SIH_WORLD_DB, SIH_API_TOKENS)
Test: create_app(Settings(...)) with a temp data dir, a corpus world.sqlite and test tokens.

Separation of audiences (enforced here, tested):
  * applicant  -> only their own application; every response is modules.pipeline.client_view (fixable problems and pre-fill values):
                  never a score, category, forensic / registry / duplicate finding, diff or another institution.
  * reviewer   -> only cases assigned to them; while the review is blind the machine score, category, reasons, per-finding probabilities
                  and the colleague's decision / notes are withheld (modules.review.view); the evidence PDF is rendered without scores.
  * supervisor -> the queue with scores, assignment, full results, diffs.
  * admin      -> 48-hour purge of drafts, audit-chain verification, and the rule changer (/admin/rules: structured rule PDF -> preview with
                  impact on stored cases -> apply -> versioned, restorable; every step audited; each stored result records `rules_version`).
Uploads are drafts (versioned, 48 h TTL via modules.ttl_purge) until `submit` moves them into the submitted store. Re-uploading a document
creates a new version and a v1-v2 diff for the reviewers. Submission is refused (409) while the applicant still has fixable problems.
All timestamps come from Settings.clock (injectable); the processing pipeline serialises behind one lock.
Does NOT: authenticate beyond static bearer tokens (put SSO in front), scale beyond one process, or send notifications.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pymupdf
import yaml
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from api.store import Store
from modules import adapters, ai_assist as AI, diff as diff_mod, perspective, pipeline as PL, review as R, rule_import as RI, rules as rules_mod, ttl_purge

ROOT = Path(__file__).resolve().parent.parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Settings:
    data_dir: Path
    world_db: Path
    tokens: dict
    max_upload_mb: int = 25
    rng_seed: int = 26261
    doc_types: list = field(default_factory=list)
    clock: Callable[[], str] = _now
    ai: object = "env"            # "env" = the provider chosen by the environment (GROQ_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY, or off without a key); None = off; or an injected client (tests)

    @classmethod
    def from_env(cls) -> "Settings":
        cfg = yaml.safe_load(open(ROOT / "config" / "api.yaml", encoding="utf-8"))
        tok_file = os.environ.get("SIH_API_TOKENS")
        tokens = json.load(open(tok_file, encoding="utf-8")) if tok_file else cfg["tokens"]
        return cls(Path(os.environ.get("SIH_DATA_DIR", ROOT / "data")), Path(os.environ.get("SIH_WORLD_DB", ROOT / "corpus_v2" / "world" / "world.sqlite")),
                   tokens, cfg["max_upload_mb"], cfg["rng_seed"], cfg["doc_types"])


class ApplicationIn(BaseModel):
    name: Optional[str] = None
    application_date: str
    application_type: str = "existing"
    programme_type: str = "engineering"


class DecisionIn(BaseModel):
    decision: str
    notes: str = ""


class RuleApplyIn(BaseModel):
    id: str
    acknowledge: bool = False


class RuleIdIn(BaseModel):
    id: str


class RuleRevertIn(BaseModel):
    version: int


class PurgeIn(BaseModel):
    now: Optional[str] = None
    ttl_hours: Optional[float] = None
    dry_run: bool = False


class _LockedAudit:
    """The pipeline appends to the audit log through the store's lock (one shared sqlite connection)."""

    def __init__(self, store: Store):
        self.store = store

    def append(self, entry: dict) -> str:
        return self.store.log(entry)


def _safe_ext(name: str) -> str:
    ext = Path(name or "").suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ".bin"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    st = settings or Settings.from_env()
    app = FastAPI(title="SIH26261 verification gateway")
    data = Path(st.data_dir)
    for d in ("drafts", "submitted", "work", "evidence"):
        (data / d).mkdir(parents=True, exist_ok=True)
    store = Store(str(data / "gateway.sqlite"))
    drafts = ttl_purge.FileStore(data / "drafts")
    source = adapters.WorldSource(st.world_db)
    keys = {r[0]: r[1] for r in source.con.execute("select issuer, secret_hex from issuer_keys")}
    pipe = PL.Pipeline(source, keys, audit=_LockedAudit(store), work_dir=data / "work")
    proc = threading.Lock()
    rstore = RI.RuleStore(data / "rules")                       # rule updates: overrides on top of config/norms.yaml + config/rulebook.yaml
    base_rb = rules_mod.load_rulebook()
    base_nd = yaml.safe_load(open(RI.CONFIG_DIR / "norms.yaml", encoding="utf-8"))
    pipe.rulebook, pipe.norms = RI.effective(base_rb, base_nd, rstore.current())
    reviewers = sorted(t["user"] for t in st.tokens.values() if t["role"] == "reviewer")
    review_cfg = R.load_config()

    # ------------------------------------------------------------------ auth
    def principal(authorization: Optional[str] = Header(None)) -> dict:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        p = st.tokens.get(authorization.split(None, 1)[1].strip())
        if p is None:
            raise HTTPException(401, "unknown token")
        return p

    def need(*roles):
        def dep(p: dict = Depends(principal)) -> dict:
            if p["role"] not in roles:
                raise HTTPException(403, "this endpoint is not available to your role")
            return p
        return dep

    applicant, reviewer_, supervisor, admin = need("applicant"), need("reviewer"), need("supervisor"), need("admin")
    staff = need("reviewer", "supervisor")

    def app_dict(inst_id: str) -> dict:
        a = store.get_app(inst_id)
        if a is None:
            raise HTTPException(404, "no application: create it first")
        return {"inst_id": inst_id, **a["data"]}

    def uploads_of(inst_id: str) -> list[dict]:
        return [{"doc_id": f"{inst_id}-{dt}", "doc_type": dt, "path": d["path"]} for dt, d in store.latest_docs(inst_id).items()]

    def public_status(a: dict) -> str:
        s = a["status"]
        return {"draft": "draft", "info_requested": "info_requested"}.get(s, "decision_made" if s.startswith("decided_") else "received")

    # ------------------------------------------------------------------ health
    @app.get("/health")
    def health():
        return {"ok": True, "reviewers": len(reviewers)}

    @app.get("/me")
    def me(p: dict = Depends(principal)):
        """Who the token belongs to (the frontend routes by role). Never returns the token or anything about other users."""
        return {k: p[k] for k in ("role", "user", "inst_id") if k in p}

    # ------------------------------------------------------------------ /apply
    def _decode_image(blob: bytes):
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(422, "this is not an image the server can read")
        return img

    @app.post("/apply/photo/detect")
    def photo_detect(file: UploadFile = File(...), p: dict = Depends(applicant)):
        """Corners of the page in a phone photo (original pixels), for the crop tool's draggable handles. Nothing is stored."""
        blob = file.file.read(st.max_upload_mb * 1024 * 1024 + 1)
        if len(blob) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"file larger than {st.max_upload_mb} MB")
        img = _decode_image(blob)
        h, w = img.shape[:2]
        q, info = perspective.detect_quad(img)
        if q is None:
            return {"found": False, "width": w, "height": h, "corners": [[0, 0], [w, 0], [w, h], [0, h]],
                    "message": "We could not find the edges of the page. Drag the corners onto the page corners, or retake the photo flat on a plain background."}
        flat, cinfo = perspective.correct(img)
        msg = {"corrected": "Page found. Check that the four handles sit on the page corners, then straighten.",
               "not_needed": "The page already looks flat and upright.",
               "partial": "We could not see all four corners clearly. Drag the handles onto the corners, or retake the photo."}.get(cinfo["status"], "")
        return {"found": True, "width": w, "height": h, "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in q],
                "status": cinfo["status"], "message": msg}

    @app.post("/apply/photo/warp")
    def photo_warp(file: UploadFile = File(...), corners: str = "", p: dict = Depends(applicant)):
        """Warp the photo flat using the (possibly hand-adjusted) corners TL, TR, BR, BL. Returns a PNG; nothing is stored."""
        import cv2
        import numpy as np
        blob = file.file.read(st.max_upload_mb * 1024 * 1024 + 1)
        if len(blob) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"file larger than {st.max_upload_mb} MB")
        img = _decode_image(blob)
        try:
            q = np.array(json.loads(corners), np.float32).reshape(4, 2)
            if not np.isfinite(q).all():
                raise ValueError
        except Exception:
            raise HTTPException(422, "corners must be a JSON list of four [x, y] points")
        h, w = img.shape[:2]
        q = perspective.order_corners(np.clip(q, [-0.1 * w, -0.1 * h], [1.1 * w, 1.1 * h]))
        if abs(cv2.contourArea(q)) < 0.05 * w * h:
            raise HTTPException(422, "the four corners enclose too small an area")
        cfg = perspective.load_config()
        flat = perspective.warp(img, q, cfg["output_width"], cfg["target_aspect"])
        ok, buf = cv2.imencode(".png", flat)
        return Response(buf.tobytes(), media_type="image/png")

    @app.post("/apply/application")
    def create_application(body: ApplicationIn, p: dict = Depends(applicant)):
        inst = p["inst_id"]
        if store.get_app(inst):
            raise HTTPException(409, "application already exists")
        store.put_app(inst, body.model_dump(), "draft", st.clock())
        store.log({"event": "application_created", "inst_id": inst, "at": st.clock()})
        return {"status": "draft", "application": inst}

    @app.get("/apply/application")
    def my_application(p: dict = Depends(applicant)):
        a = store.get_app(p["inst_id"])
        if a is None:
            raise HTTPException(404, "no application")
        out = {"application": p["inst_id"], "status": public_status(a), "documents": sorted(store.latest_docs(p["inst_id"]))}
        if a["status"].startswith("decided_"):
            out["decision"] = a["status"][len("decided_"):]
        return out

    @app.put("/apply/documents/{doc_type}")
    def upload(doc_type: str, file: UploadFile = File(...), p: dict = Depends(applicant)):
        inst = p["inst_id"]
        if doc_type not in st.doc_types:
            raise HTTPException(422, f"unknown document type; use one of {st.doc_types}")
        a = store.get_app(inst)
        if a is None:
            raise HTTPException(404, "create the application first")
        if a["status"] not in ("draft", "info_requested"):
            raise HTTPException(409, "the application is submitted; no changes are accepted now")
        blob = file.file.read(st.max_upload_mb * 1024 * 1024 + 1)
        if len(blob) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"file larger than {st.max_upload_mb} MB")
        ver = store.next_version(inst, doc_type)
        key = f"{inst}__{doc_type}__v{ver}{_safe_ext(file.filename)}"      # the client's file name never reaches the file system
        path = drafts.add(key, st.clock(), "draft_upload", blob)
        sha = hashlib.sha256(blob).hexdigest()
        store.add_doc(inst, doc_type, ver, str(path), sha, "draft", st.clock())
        with proc:                                                         # pre-flight: quick, client-safe checks only
            d = pipe.process_document(app_dict(inst), {"doc_id": f"{inst}-{doc_type}", "doc_type": doc_type, "path": str(path)}, dry_run=True)
        prev = store.doc_version(inst, doc_type, ver - 1) if ver > 1 else None
        if prev and Path(prev["path"]).exists():
            try:
                fs = diff_mod.analyze(prev["path"], str(path), f"{inst}-{doc_type}")
                store.put_diff(inst, doc_type, ver, {"from": ver - 1, "to": ver, "finding": fs[0].to_json()})
            except Exception:
                pass
        view = PL.client_view({"mode": "dry_run", "app": {"inst_id": inst}, "documents": [PL.doc_to_json(d)], "rules": []})
        return {"doc_type": doc_type, "version": ver, "expires_after_hours": ttl_purge.load_config()["ttl_hours"], **view["documents"][0]}

    def _dry_run(inst: str) -> dict:
        with proc:
            res = pipe.process_application(app_dict(inst), uploads_of(inst), dry_run=True, now=st.clock())
        return PL.client_view(res)

    @app.post("/apply/dry-run")
    def dry_run(p: dict = Depends(applicant)):
        if not store.get_app(p["inst_id"]):
            raise HTTPException(404, "create the application first")
        return _dry_run(p["inst_id"])                                      # nothing is stored, logged or indexed

    @app.post("/apply/submit")
    def submit(p: dict = Depends(applicant)):
        inst = p["inst_id"]
        a = store.get_app(inst)
        if a is None:
            raise HTTPException(404, "create the application first")
        if a["status"] not in ("draft", "info_requested"):
            raise HTTPException(409, "already submitted")
        check = _dry_run(inst)
        if check["status"] == "action_needed":
            raise HTTPException(409, detail={"status": "action_needed", "view": check})
        now = st.clock()
        with proc:
            rules_version = rstore.version()
            res = pipe.process_application(app_dict(inst), uploads_of(inst), dry_run=False, now=now)
        for dt, d in store.latest_docs(inst).items():                       # drafts become submitted records (no longer purged)
            dst = data / "submitted" / inst / f"r{a['round']}" / Path(d["path"]).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if Path(d["path"]).exists():
                shutil.move(d["path"], dst)
                (data / "drafts" / f"{Path(d['path']).name}.meta.json").unlink(missing_ok=True)
            store.set_doc(inst, dt, d["version"], str(dst), "submitted")
        # re-point the stored result at the submitted files
        sub = {dt: d["path"] for dt, d in store.latest_docs(inst).items()}
        for dd in res["documents"]:
            dd["path"] = sub.get(dd["doc_type"], dd["path"])
        res["rules_version"] = rules_version                                  # which rule settings produced this result (see /admin/rules)
        store.put_result(inst, a["round"], res, now)
        state = R.open_case(inst, res["score"]["score"], res["score"]["category"])
        state["reasons"] = res["score"]["reasons"]
        store.put_review(inst, state)
        store.set_status(inst, "submitted", now)
        store.log({"event": "submitted", "inst_id": inst, "round": a["round"], "score": res["score"]["score"], "category": res["score"]["category"], "at": now})
        return PL.client_view(res)

    # ------------------------------------------------------------------ /review
    def case_for(inst: str) -> tuple[dict, dict, dict]:
        a, res, rv = store.get_app(inst), store.get_result(inst), store.get_review(inst)
        if a is None or res is None or rv is None:
            raise HTTPException(404, "no such submitted case")
        return a, res, rv

    def blind_findings(res: dict) -> list[dict]:
        """Evidence for a reviewer without the machine's confidence: no probability, no kind (hard/soft ordering is itself a score)."""
        return [{k: f.get(k) for k in ("id", "doc_id", "tier", "message", "bbox", "evidence")} for f in res["findings"] if f["kind"] != "info"]

    @app.get("/review/queue")
    def queue(p: dict = Depends(supervisor)):
        rows = []
        for a in store.all_apps():
            res, rv = store.get_result(a["inst_id"]), store.get_review(a["inst_id"])
            if res is None:
                continue
            rows.append({"inst_id": a["inst_id"], "name": a["data"].get("name"), "status": a["status"], "score": res["score"]["score"],
                         "category": res["score"]["category"], "double_review": rv["double"], "phase": rv["phase"], "assigned": rv["assigned"]})
        return sorted(rows, key=lambda r: -r["score"])

    @app.post("/review/cases/{inst}/assign")
    def assign(inst: str, p: dict = Depends(supervisor)):
        a, res, rv = case_for(inst)
        try:
            new = R.assign(rv, reviewers, random.Random(f"{st.rng_seed}:{inst}:{a['round']}"))
        except R.ReviewError as exc:
            raise HTTPException(409, str(exc))
        store.put_review(inst, new)
        store.set_status(inst, "in_review")
        store.log({"event": "assigned", "inst_id": inst, "reviewers": new["assigned"], "by": p["user"], "at": st.clock()})
        return {"inst_id": inst, "phase": new["phase"], "n_reviewers": len(new["assigned"])}

    @app.get("/review/my-cases")
    def my_cases(p: dict = Depends(reviewer_)):
        out = []
        for a in store.all_apps():
            rv = store.get_review(a["inst_id"])
            if rv and p["user"] in rv["assigned"]:
                out.append({"inst_id": a["inst_id"], "phase": rv["phase"], "you_submitted": p["user"] in rv["decisions"]})
        return out

    @app.get("/review/cases/{inst}")
    def case(inst: str, p: dict = Depends(staff)):
        a, res, rv = case_for(inst)
        if p["role"] == "supervisor":
            return {"result": res, "review": rv, "pages": {d["doc_type"]: page_sizes(d["path"]) for d in res["documents"]}}
        try:
            v = R.view(rv, p["user"], review_cfg)
        except R.ReviewError:
            raise HTTPException(403, "this case is not assigned to you")
        blind = not R.revealed(rv, p["user"])
        v["application"] = {k: res["app"].get(k) for k in ("inst_id", "name", "application_date", "application_type")}
        v["documents"] = [{"doc_id": d["doc_id"], "doc_type": d["doc_type"], "fields": d["fields"], "pages": page_sizes(d["path"])} for d in res["documents"]]
        v["findings"] = blind_findings(res)
        if not blind:
            v["findings_with_scores"] = [f for f in res["findings"] if f["kind"] != "info"]
        return v

    @app.post("/review/cases/{inst}/decision")
    def decision(inst: str, body: DecisionIn, p: dict = Depends(reviewer_)):
        a, res, rv = case_for(inst)
        try:
            new = R.submit(rv, p["user"], body.decision, body.notes, reviewers, random.Random(f"{st.rng_seed}:{inst}:third"), review_cfg)
        except R.ReviewError as exc:
            raise HTTPException(409, str(exc))
        store.put_review(inst, new)
        store.log({"event": "decision", "inst_id": inst, "reviewer": p["user"], "decision": body.decision, "phase": new["phase"], "at": st.clock()})
        if new["phase"] == "resolved":
            outcome = new["outcome"]
            if outcome == "request_info":
                store.set_status(inst, "info_requested", rnd=a["round"] + 1)
            else:
                store.set_status(inst, f"decided_{outcome}")
        return R.view(new, p["user"], review_cfg)

    @app.get("/review/cases/{inst}/evidence.pdf")
    def evidence(inst: str, p: dict = Depends(staff)):
        a, res, rv = case_for(inst)
        blind = True
        if p["role"] == "reviewer":
            if p["user"] not in rv["assigned"]:
                raise HTTPException(403, "this case is not assigned to you")
            blind = not R.revealed(rv, p["user"])
        else:
            blind = False
        out = data / "evidence" / f"{inst}_{'blind' if blind else 'full'}.pdf"
        shown = {**res, "score": {} if blind else res["score"]}
        case_dict = {"case_id": inst, "generated_at": st.clock(), "blind": blind,
                     "institution": {"name": res["app"].get("name") or inst, "inst_id": inst, "application_date": res["app"]["application_date"]},
                     "score": shown["score"], "documents": [{"doc_id": d["doc_id"], "doc_type": d["doc_type"], "path": d["path"]} for d in res["documents"]],
                     "findings": res["findings"], "rules": {r["id"]: {"name": r["name"]} for r in res["rules"]}}
        from modules import evidence_pdf
        evidence_pdf.build_evidence_pdf(case_dict, out)
        return FileResponse(str(out), media_type="application/pdf", filename=out.name)

    def page_sizes(path: str) -> list[dict]:
        try:
            with pymupdf.open(path) as d:
                return [{"w": round(pg.rect.width, 2), "h": round(pg.rect.height, 2)} for pg in d]
        except Exception:
            return []

    @app.get("/review/cases/{inst}/documents/{doc_type}/page/{n}.png")
    def page_image(inst: str, doc_type: str, n: int, dpi: int = 110, p: dict = Depends(staff)):
        """A rendered page of a SUBMITTED document, for the bounding-box viewer. Same access rule as the case itself."""
        a, res, rv = case_for(inst)
        if p["role"] == "reviewer" and p["user"] not in rv["assigned"]:
            raise HTTPException(403, "this case is not assigned to you")
        d = next((x for x in res["documents"] if x["doc_type"] == doc_type), None)
        if d is None or not Path(d["path"]).exists():
            raise HTTPException(404, "no such document")
        dpi = max(50, min(int(dpi), 200))
        try:
            with pymupdf.open(d["path"]) as doc:
                if not 1 <= n <= doc.page_count:
                    raise HTTPException(404, "no such page")
                png = doc[n - 1].get_pixmap(dpi=dpi, alpha=False).tobytes("png")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, "this document cannot be rendered as pages")
        return Response(png, media_type="image/png")

    @app.get("/review/cases/{inst}/documents/{doc_type}/diff")
    def doc_diff(inst: str, doc_type: str, p: dict = Depends(staff)):
        a, res, rv = case_for(inst)
        if p["role"] == "reviewer" and p["user"] not in rv["assigned"]:
            raise HTTPException(403, "this case is not assigned to you")
        d = store.get_diff(inst, doc_type)
        if d is None:
            raise HTTPException(404, "no earlier version of this document")
        return d

    # ------------------------------------------------------------------ /admin
    @app.post("/admin/purge")
    def purge(body: PurgeIn, p: dict = Depends(admin)):
        now = body.now or st.clock()
        rep = ttl_purge.purge(drafts, now, body.ttl_hours, body.dry_run)
        for ev in rep["events"]:
            store.log(ev)
        return {k: rep[k] for k in ("now", "ttl_hours", "dry_run", "expired", "deleted", "kept", "protected", "errors")}

    @app.get("/admin/audit/verify")
    def audit_verify(p: dict = Depends(admin)):
        with store.lock:
            ok, bad = store.audit.verify_chain()
        n = store.conn.execute("select count(*) from audit_log").fetchone()[0]
        return {"intact": ok, "first_bad_seq": bad, "entries": n}

    # ------------------------------------------------------------------ AI assistant (optional, off without a key)
    ai = AI.client_from_env() if st.ai == "env" else st.ai
    ai_cache: dict = {}

    def need_ai():
        if ai is None:
            raise HTTPException(503, "AI help is not set up on this server (no AI API key)")
        return ai

    @app.get("/ai/status")
    def ai_status(p: dict = Depends(need("reviewer", "supervisor", "admin"))):
        return {"enabled": ai is not None, "model": getattr(ai, "model", None), "service": getattr(ai, "service", None)}

    @app.post("/review/cases/{inst}/ai-explain")
    def ai_explain(inst: str, p: dict = Depends(staff)):
        client = need_ai()
        a, res, rv = case_for(inst)
        blind = False
        if p["role"] == "reviewer":
            if p["user"] not in rv["assigned"]:
                raise HTTPException(403, "this case is not assigned to you")
            blind = not R.revealed(rv, p["user"])             # a blind reviewer's explanation is built without score or probabilities
        key = (inst, a["round"], blind)
        if key in ai_cache:
            return {**ai_cache[key], "cached": True}
        summary = AI.case_summary(res["app"], res["findings"], None if blind else res["score"], [r for r in res["rules"] if r.get("result") == "fail"], blind)
        out = AI.explain_case(client, summary)
        store.log({"event": "ai_used", "feature": "explain_case", "inst_id": inst, "blind": blind, "model": client.model, "ok": out["ok"], "by": p["user"], "at": st.clock()})
        if not out["ok"]:
            raise HTTPException(502, out["error"])
        ai_cache[key] = {**out, "service": client.service, "blind": blind}
        return ai_cache[key]

    # ------------------------------------------------------------------ /admin/rules (rule changer)
    def rules_state() -> dict:
        ov = rstore.current()
        rb, norms = RI.effective(base_rb, base_nd, ov)
        on = {r["id"]: r for r in rb["rules"]}
        base_norms = base_nd["programme_types"]
        return {"version": int(ov.get("version", 0)), "modified": bool(ov.get("norms") or ov.get("rules") or ov.get("meta")), "norms_version": rb.get("norms_version"), "norms_validated": bool(rb.get("norms_validated")),
                "norms": [{"programme_type": p, "key": k, "value": v, "shipped": base_norms.get(p, {}).get(k), "ref": (ov.get("norm_refs") or {}).get(f"{p}.{k}")}
                          for p, vals in norms.items() for k, v in vals.items()],
                "rules": [{"id": r["id"], "name": r["name"], "tier": r["tier"], "enabled": r["id"] in on,
                           **{k: on.get(r["id"], {**r, **(ov.get("rules") or {}).get(r["id"], {})}).get(k) for k in ("kind", "severity", "p", "handbook_ref")}}
                          for r in base_rb["rules"]],
                "history": rstore.history()}

    @app.get("/admin/rules")
    def rules_get(p: dict = Depends(admin)):
        return rules_state()

    @app.get("/admin/rules/template.pdf")
    def rules_template(p: dict = Depends(admin)):
        rb, _ = RI.effective(base_rb, base_nd, rstore.current())
        n, r = RI.template_rows(base_rb, base_nd, rstore.current())
        out = data / "work" / "rule_update_template.pdf"
        RI.write_rule_pdf(out, {"norms_version": rb.get("norms_version", ""), "validated": bool(rb.get("norms_validated"))}, n, r)
        return Response(out.read_bytes(), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="rule_update_template.pdf"'})

    @app.post("/admin/rules/upload")
    async def rules_upload(file: UploadFile = File(...), p: dict = Depends(admin)):
        raw = await file.read(st.max_upload_mb * 1024 * 1024 + 1)
        if len(raw) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"the file is larger than {st.max_upload_mb} MB")
        if not raw.startswith(b"%PDF"):
            raise HTTPException(422, "this is not a PDF file")
        try:
            parsed = RI.read_pdf(raw)
        except RI.RuleImportError as exc:
            raise HTTPException(422, str(exc))
        plan = RI.build_plan(parsed, base_rb, base_nd, rstore.current(), {"name": Path(file.filename or "rules.pdf").name, "sha256": hashlib.sha256(raw).hexdigest()})
        return stage_preview(plan, raw, p)

    def stage_preview(plan: dict, raw: bytes, p: dict) -> dict:
        """Impact on stored cases, stage the plan (nothing applies yet), audit, and return it without the overrides document."""
        cur = rstore.current()
        results = {a["inst_id"]: r for a in store.all_apps() if (r := store.get_result(a["inst_id"])) is not None}
        try:
            impact = PL.rule_change_impact(results, RI.effective(base_rb, base_nd, cur), RI.effective(base_rb, base_nd, plan["new_overrides"])) if plan["changes"] else None
        except Exception:
            impact = None                                                    # a preview must not fail because the what-if could not run
        plan["impact"] = impact
        sid = rstore.stage(plan, raw, st.clock())
        store.log({"event": "rules_previewed", "id": sid, "file": plan["source"]["name"], "sha256": plan["source"]["sha256"], "n_changes": len(plan["changes"]), "ai": bool(plan.get("ai")), "by": p["user"], "at": st.clock()})
        return {**{k: v for k, v in plan.items() if k != "new_overrides"}, "id": sid, "based_on_version": rstore.version()}

    @app.post("/admin/rules/ai-upload")
    async def rules_ai_upload(file: UploadFile = File(...), p: dict = Depends(admin)):
        """An unstructured rule document (prose handbook, scan): the AI turns it into table rows, which then take the normal path."""
        client = need_ai()
        raw = await file.read(st.max_upload_mb * 1024 * 1024 + 1)
        if len(raw) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"the file is larger than {st.max_upload_mb} MB")
        if not raw.startswith(b"%PDF"):
            raise HTTPException(422, "this is not a PDF file")
        rb_now, norms_now = RI.effective(base_rb, base_nd, rstore.current())
        got = AI.rules_from_document(client, raw, norms_now, base_rb["rules"])
        store.log({"event": "ai_used", "feature": "rules_from_document", "model": client.model, "ok": got["ok"], "by": p["user"], "at": st.clock()})
        if not got["ok"]:
            raise HTTPException(422, got["error"])
        plan = RI.build_plan(got["parsed"], base_rb, base_nd, rstore.current(), {"name": Path(file.filename or "rules.pdf").name, "sha256": hashlib.sha256(raw).hexdigest(), "ai": True})
        plan["ai"], plan["ai_notes"] = True, got["notes"]
        plan["warnings"].insert(0, f"These changes were read from the document by an AI model ({client.service}). Check every value against the quoted sentence before applying.")
        plan["needs_ack"].append({"label": "Every change in this list", "why": "was read from the document by an AI model, so it needs your check against the quoted source"})
        return stage_preview(plan, raw, p)

    @app.post("/admin/rules/ai-explain")
    def rules_ai_explain(body: RuleIdIn, p: dict = Depends(admin)):
        client = need_ai()
        plan = rstore.staged(body.id)
        if plan is None:
            raise HTTPException(404, "this preview no longer exists; upload the file again")
        out = AI.explain_plan(client, plan)
        store.log({"event": "ai_used", "feature": "explain_rule_changes", "model": client.model, "ok": out["ok"], "by": p["user"], "at": st.clock()})
        if not out["ok"]:
            raise HTTPException(502, out["error"])
        return {**out, "service": client.service}

    @app.post("/admin/rules/apply")
    def rules_apply(body: RuleApplyIn, p: dict = Depends(admin)):
        with proc:                                                           # never swap the rules under a case that is being processed
            try:
                out = rstore.apply(body.id, p["user"], body.acknowledge, st.clock())
            except RI.RuleImportError as exc:
                raise HTTPException(409, str(exc))
            pipe.rulebook, pipe.norms = RI.effective(base_rb, base_nd, rstore.current())
        h = rstore.history()[-1]
        store.log({"event": "rules_applied", "version": out["version"], "file": h.get("source_name"), "sha256": h.get("sha256"), "n_changes": out["changes"], "by": p["user"], "at": st.clock()})
        return rules_state()

    @app.post("/admin/rules/revert")
    def rules_revert(body: RuleRevertIn, p: dict = Depends(admin)):
        with proc:
            try:
                out = rstore.revert(body.version, p["user"], st.clock())
            except RI.RuleImportError as exc:
                raise HTTPException(404, str(exc))
            pipe.rulebook, pipe.norms = RI.effective(base_rb, base_nd, rstore.current())
        store.log({"event": "rules_reverted", "version": out["version"], "restored": out["restored"], "by": p["user"], "at": st.clock()})
        return rules_state()

    app.state.store, app.state.settings, app.state.pipeline = store, st, pipe
    return app


def app_factory() -> FastAPI:
    """uvicorn api.app:app_factory --factory   (settings from the environment; nothing is created at import time)"""
    return create_app(Settings.from_env())
