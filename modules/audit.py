"""Append-only, hash-chained audit log on a caller-supplied sqlite3 connection.

Each entry stores prev_hash and hash = sha256(prev_hash + canonical_json(entry)); the first
prev_hash is 64 zeros. verify_chain() recomputes the chain and reports the first bad row.
No global connection: pass one in. The SQL trigger (audit_trigger.sql) blocks UPDATE/DELETE
at the database; the chain detects tampering done by bypassing it (e.g. editing the file).
Does NOT detect: truncation of the newest rows (anchor the latest hash externally),
or a rewrite of the whole chain by someone who can recompute every hash.
"""
from __future__ import annotations

import hashlib, json, sqlite3
from pathlib import Path

GENESIS = "0" * 64
SQL_PATH = Path(__file__).with_name("audit_trigger.sql")


def canonical_json(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(prev_hash: str, entry: dict) -> str:
    return hashlib.sha256((prev_hash + canonical_json(entry)).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript(SQL_PATH.read_text())

    def last_hash(self) -> str:
        r = self.conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        return r[0] if r else GENESIS

    def append(self, entry: dict) -> str:
        prev = self.last_hash()
        h = entry_hash(prev, entry)
        self.conn.execute("INSERT INTO audit_log(prev_hash, entry_json, hash) VALUES (?,?,?)",
                          (prev, canonical_json(entry), h))
        self.conn.commit()
        return h

    def verify_chain(self) -> tuple[bool, int | None]:
        """(True, None) if intact, else (False, seq of first bad row)."""
        prev = GENESIS
        for seq, p, ej, h in self.conn.execute(
                "SELECT seq, prev_hash, entry_json, hash FROM audit_log ORDER BY seq"):
            if p != prev or hashlib.sha256((p + ej).encode("utf-8")).hexdigest() != h:
                return False, seq
            prev = h
        return True, None
