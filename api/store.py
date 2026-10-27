"""SQLite store for the API: applications, document versions, pipeline results, diffs, review cases, and the audit log.

One connection guarded by a lock (the API serialises writes; sqlite is fine for the volumes of a regional office, a deployment moves to
PostgreSQL with the same methods). The audit log is the hash-chained, append-only one from modules.audit, on the same connection.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from modules.audit import AuditLog

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications(inst_id TEXT PRIMARY KEY, data TEXT, status TEXT, round INTEGER DEFAULT 1, created_at TEXT, submitted_at TEXT);
CREATE TABLE IF NOT EXISTS documents(inst_id TEXT, doc_type TEXT, version INTEGER, path TEXT, sha256 TEXT, state TEXT, created_at TEXT,
                                     PRIMARY KEY(inst_id, doc_type, version));
CREATE TABLE IF NOT EXISTS results(inst_id TEXT, round INTEGER, json TEXT, created_at TEXT, PRIMARY KEY(inst_id, round));
CREATE TABLE IF NOT EXISTS diffs(inst_id TEXT, doc_type TEXT, version INTEGER, json TEXT, PRIMARY KEY(inst_id, doc_type, version));
CREATE TABLE IF NOT EXISTS reviews(inst_id TEXT PRIMARY KEY, state TEXT);
"""


class Store:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.audit = AuditLog(self.conn)

    # -- applications
    def get_app(self, inst_id: str) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT * FROM applications WHERE inst_id=?", (inst_id,)).fetchone()
        return None if r is None else {**dict(r), "data": json.loads(r["data"])}

    def put_app(self, inst_id: str, data: dict, status: str, now: str, rnd: int = 1) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO applications(inst_id, data, status, round, created_at, submitted_at) VALUES (?,?,?,?,COALESCE((SELECT created_at FROM applications WHERE inst_id=?),?),(SELECT submitted_at FROM applications WHERE inst_id=?))",
                              (inst_id, json.dumps(data), status, rnd, inst_id, now, inst_id))
            self.conn.commit()

    def set_status(self, inst_id: str, status: str, now: Optional[str] = None, rnd: Optional[int] = None) -> None:
        with self.lock:
            self.conn.execute("UPDATE applications SET status=?, submitted_at=COALESCE(?, submitted_at), round=COALESCE(?, round) WHERE inst_id=?",
                              (status, now, rnd, inst_id))
            self.conn.commit()

    def all_apps(self) -> list[dict]:
        with self.lock:
            return [{**dict(r), "data": json.loads(r["data"])} for r in self.conn.execute("SELECT * FROM applications ORDER BY inst_id")]

    # -- documents
    def next_version(self, inst_id: str, doc_type: str) -> int:
        with self.lock:
            r = self.conn.execute("SELECT MAX(version) FROM documents WHERE inst_id=? AND doc_type=?", (inst_id, doc_type)).fetchone()
        return (r[0] or 0) + 1

    def add_doc(self, inst_id: str, doc_type: str, version: int, path: str, sha: str, state: str, now: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (inst_id, doc_type, version, path, sha, state, now))
            self.conn.commit()

    def latest_docs(self, inst_id: str) -> dict[str, dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM documents WHERE inst_id=? ORDER BY doc_type, version", (inst_id,)).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            out[r["doc_type"]] = dict(r)
        return out

    def doc_version(self, inst_id: str, doc_type: str, version: int) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT * FROM documents WHERE inst_id=? AND doc_type=? AND version=?", (inst_id, doc_type, version)).fetchone()
        return None if r is None else dict(r)

    def set_doc(self, inst_id: str, doc_type: str, version: int, path: str, state: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE documents SET path=?, state=? WHERE inst_id=? AND doc_type=? AND version=?", (path, state, inst_id, doc_type, version))
            self.conn.commit()

    # -- results, diffs, reviews
    def put_result(self, inst_id: str, rnd: int, result: dict, now: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?)", (inst_id, rnd, json.dumps(result, default=str), now))
            self.conn.commit()

    def get_result(self, inst_id: str) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT json FROM results WHERE inst_id=? ORDER BY round DESC LIMIT 1", (inst_id,)).fetchone()
        return None if r is None else json.loads(r[0])

    def put_diff(self, inst_id: str, doc_type: str, version: int, diff: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO diffs VALUES (?,?,?,?)", (inst_id, doc_type, version, json.dumps(diff, default=str)))
            self.conn.commit()

    def get_diff(self, inst_id: str, doc_type: str) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT json FROM diffs WHERE inst_id=? AND doc_type=? ORDER BY version DESC LIMIT 1", (inst_id, doc_type)).fetchone()
        return None if r is None else json.loads(r[0])

    def get_review(self, inst_id: str) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT state FROM reviews WHERE inst_id=?", (inst_id,)).fetchone()
        return None if r is None else json.loads(r[0])

    def put_review(self, inst_id: str, state: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO reviews VALUES (?,?)", (inst_id, json.dumps(state)))
            self.conn.commit()

    def log(self, entry: dict) -> str:
        with self.lock:
            return self.audit.append(entry)
