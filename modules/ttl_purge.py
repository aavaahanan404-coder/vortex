"""48-hour TTL purge of draft uploads and extraction caches.

Input : a Store (anything with list() -> entries and delete(key)), the current time `now` (passed in,
        never read from the wall clock), ttl_hours, optional dry_run.
        Entry = {"key": str, "created_at": datetime | ISO string, "kind": str}.
Output: a report dict {now, ttl_hours, dry_run, expired:[{key, kind, age_hours}], kept, protected, future_dated,
        errors:[{key, error}], events:[audit-ready dicts]} - the caller writes `events` to the audit log
        (this module has no database and no global state).
Rules : only kinds in config purge_kinds can expire; an entry older than (or exactly) ttl_hours is expired;
        entries dated in the future (clock skew) are kept and listed; a failing delete or an unreadable
        entry is reported in `errors` and never stops the run; a TTL below min_ttl_hours is refused.
FileStore: payload `<root>/<key>` (file or directory) with sidecar `<root>/<key>.meta.json`
        {"created_at": ISO, "kind": str}. Files without a sidecar are never touched; keys that would resolve
        outside root are rejected.
Does NOT: extend a TTL on access, purge by size, or purge from S3/Redis (write a Store for those: two methods).
CLI   : python -m modules.ttl_purge ROOT --now 2026-10-27T10:00:00 [--ttl-hours 48] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Protocol

import yaml

MODULE = "ttl_purge"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "ttl.yaml"


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def to_dt(v) -> datetime:
    """datetime or ISO-8601 string -> aware UTC datetime (naive values are taken as UTC)."""
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if not isinstance(v, datetime):
        raise ValueError(f"not a date: {v!r}")
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


class Store(Protocol):
    def list(self) -> Iterable[dict]: ...
    def delete(self, key: str) -> None: ...


class MemoryStore:
    """In-memory store for tests and dry runs."""

    def __init__(self, entries: Iterable[dict] = ()):
        self.entries = {e["key"]: dict(e) for e in entries}

    def list(self) -> list[dict]:
        return [dict(e) for e in self.entries.values()]

    def delete(self, key: str) -> None:
        del self.entries[key]


class FileStore:
    """Draft/cache entries on a filesystem, described by `<key>.meta.json` sidecars."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _payload(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if p == self.root or self.root not in p.parents:
            raise ValueError(f"key escapes the store root: {key!r}")
        return p

    def list(self) -> list[dict]:
        out = []
        for meta in sorted(self.root.glob("*.meta.json")):
            key = meta.name[:-len(".meta.json")]
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                out.append({"key": key, "created_at": m["created_at"], "kind": m["kind"]})
            except Exception as exc:                       # unreadable sidecar: report, never delete
                out.append({"key": key, "created_at": None, "kind": None, "error": f"{type(exc).__name__}: {exc}"})
        return out

    def delete(self, key: str) -> None:
        p = self._payload(key)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        (self.root / f"{key}.meta.json").unlink(missing_ok=True)

    def add(self, key: str, created_at, kind: str, data: bytes = b"") -> Path:
        """Convenience for tests / callers: write a payload and its sidecar."""
        p = self._payload(key)
        p.write_bytes(data)
        (self.root / f"{key}.meta.json").write_text(
            json.dumps({"created_at": to_dt(created_at).isoformat(), "kind": kind}), encoding="utf-8")
        return p


def plan(entries: Iterable[dict], now, ttl_hours: float, purge_kinds: Iterable[str]) -> dict:
    """Pure classification of entries. Returns {expired, kept, protected, future_dated, errors}."""
    now, kinds = to_dt(now), set(purge_kinds)
    res = {"expired": [], "kept": [], "protected": [], "future_dated": [], "errors": []}
    for e in entries:
        key = e.get("key")
        try:
            if e.get("error"):
                raise ValueError(e["error"])
            if e["kind"] not in kinds:
                res["protected"].append(key)
                continue
            age = (now - to_dt(e["created_at"])).total_seconds() / 3600.0
            if age < 0:
                res["future_dated"].append(key)
            elif age >= ttl_hours:
                res["expired"].append({"key": key, "kind": e["kind"], "age_hours": round(age, 2)})
            else:
                res["kept"].append(key)
        except Exception as exc:
            res["errors"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
    return res


def purge(store: Store, now, ttl_hours: Optional[float] = None, dry_run: bool = False,
          cfg: Optional[dict] = None) -> dict:
    """Delete expired draft/cache entries from `store`. Never raises on a bad entry."""
    cfg = cfg or load_config()
    ttl = float(cfg["ttl_hours"] if ttl_hours is None else ttl_hours)
    if ttl < cfg["min_ttl_hours"]:
        raise ValueError(f"ttl_hours={ttl} is below min_ttl_hours={cfg['min_ttl_hours']}")
    now_dt = to_dt(now)
    res = plan(store.list(), now_dt, ttl, cfg["purge_kinds"])
    deleted, events = [], []
    for e in res["expired"]:
        if dry_run:
            continue
        try:
            store.delete(e["key"])
            deleted.append(e["key"])
            events.append({"action": "ttl_purge", "key": e["key"], "kind": e["kind"], "age_hours": e["age_hours"],
                           "ttl_hours": ttl, "at": now_dt.isoformat()})
        except Exception as exc:
            res["errors"].append({"key": e["key"], "error": f"{type(exc).__name__}: {exc}"})
    return {"now": now_dt.isoformat(), "ttl_hours": ttl, "dry_run": dry_run, "expired": res["expired"],
            "deleted": deleted, "kept": len(res["kept"]), "protected": len(res["protected"]),
            "future_dated": res["future_dated"], "errors": res["errors"], "events": events}


def next_expiry(created_at, ttl_hours: float = 48) -> str:
    """When an entry created at `created_at` expires (ISO, UTC) - for 'your draft will be deleted on ...' messages."""
    return (to_dt(created_at) + timedelta(hours=ttl_hours)).isoformat()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modules.ttl_purge")
    ap.add_argument("root"); ap.add_argument("--now", required=True, help="ISO time; the wall clock is never used")
    ap.add_argument("--ttl-hours", type=float); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    rep = purge(FileStore(a.root), a.now, a.ttl_hours, a.dry_run, load_config(a.config))
    json.dump(rep, sys.stdout, indent=2)
    print()
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
