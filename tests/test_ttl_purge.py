import json
from datetime import datetime, timedelta, timezone

import pytest

from modules import ttl_purge as T
from worker import purge_job

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CFG = T.load_config()


def ent(key, hours_old, kind="draft_upload"):
    return {"key": key, "created_at": (NOW - timedelta(hours=hours_old)).isoformat(), "kind": kind}


def test_expiry_boundaries():
    rep = T.plan([ent("a", 47.99), ent("b", 48), ent("c", 48.01), ent("d", 0)], NOW, 48, CFG["purge_kinds"])
    assert [e["key"] for e in rep["expired"]] == ["b", "c"] and rep["kept"] == ["a", "d"]


def test_only_draft_and_cache_kinds_expire():
    s = T.MemoryStore([ent("d", 100), ent("c", 100, "extraction_cache"), ent("s", 100, "submitted"),
                       ent("e", 100, "evidence"), ent("x", 100, "unknown_kind")])
    rep = T.purge(s, NOW, cfg=CFG)
    assert sorted(rep["deleted"]) == ["c", "d"] and rep["protected"] == 3
    assert set(s.entries) == {"s", "e", "x"}                      # nothing else was touched


def test_dry_run_deletes_nothing_but_reports():
    s = T.MemoryStore([ent("d", 100)])
    rep = T.purge(s, NOW, dry_run=True, cfg=CFG)
    assert rep["expired"] and not rep["deleted"] and rep["events"] == [] and "d" in s.entries


def test_future_dated_and_malformed_entries_are_kept_and_reported():
    s = T.MemoryStore([ent("future", -5), {"key": "bad", "created_at": "not a date", "kind": "draft_upload"},
                       {"key": "nokind", "created_at": NOW.isoformat()}, ent("old", 60)])
    rep = T.purge(s, NOW, cfg=CFG)
    assert rep["future_dated"] == ["future"] and {e["key"] for e in rep["errors"]} == {"bad", "nokind"}
    assert set(s.entries) == {"future", "bad", "nokind"} and rep["deleted"] == ["old"]


def test_naive_and_z_timestamps_are_utc():
    assert T.to_dt("2026-10-27T12:00:00Z") == NOW and T.to_dt(datetime(2026, 10, 27, 12, 0)) == NOW
    assert T.to_dt("2026-10-27T17:30:00+05:30") == NOW
    with pytest.raises(ValueError):
        T.to_dt(12345)


def test_ttl_floor_and_override():
    with pytest.raises(ValueError):
        T.purge(T.MemoryStore([ent("d", 5)]), NOW, ttl_hours=0.1, cfg=CFG)
    s = T.MemoryStore([ent("d", 5)])
    assert T.purge(s, NOW, ttl_hours=4, cfg=CFG)["deleted"] == ["d"]
    assert T.next_expiry("2026-10-25T12:00:00+00:00") == "2026-10-27T12:00:00+00:00"


def test_failing_delete_does_not_stop_the_run():
    class Flaky(T.MemoryStore):
        def delete(self, key):
            if key == "boom":
                raise OSError("locked")
            super().delete(key)
    s = Flaky([ent("boom", 99), ent("ok", 99)])
    rep = T.purge(s, NOW, cfg=CFG)
    assert rep["deleted"] == ["ok"] and rep["errors"][0]["key"] == "boom" and "boom" in s.entries
    assert [e["key"] for e in rep["events"]] == ["ok"] and rep["events"][0]["action"] == "ttl_purge"


def test_filestore_end_to_end(tmp_path):
    fs = T.FileStore(tmp_path)
    fs.add("draft1.pdf", NOW - timedelta(hours=49), "draft_upload", b"x")
    fs.add("draft2.pdf", NOW - timedelta(hours=1), "draft_upload", b"y")
    fs.add("final.pdf", NOW - timedelta(hours=500), "submitted", b"z")
    d = tmp_path / "cache1"; d.mkdir(); (d / "a.json").write_text("{}")
    (tmp_path / "cache1.meta.json").write_text(json.dumps({"created_at": (NOW - timedelta(hours=72)).isoformat(),
                                                           "kind": "extraction_cache"}))
    (tmp_path / "stray.txt").write_text("no sidecar: never touched")
    rep = T.purge(fs, NOW, cfg=CFG)
    assert sorted(rep["deleted"]) == ["cache1", "draft1.pdf"]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["draft2.pdf", "draft2.pdf.meta.json", "final.pdf", "final.pdf.meta.json", "stray.txt"]


def test_filestore_refuses_keys_outside_root(tmp_path):
    root = tmp_path / "store"; root.mkdir()
    (tmp_path / "victim.txt").write_text("keep")
    fs = T.FileStore(root)
    for bad in ("../victim.txt", "..", "a/../../victim.txt"):
        with pytest.raises(ValueError):
            fs.delete(bad)
    assert (tmp_path / "victim.txt").exists()
    s = T.MemoryStore([ent("../victim.txt", 99)])             # a hostile sidecar key goes through purge() as an error
    class Wrap(T.FileStore):
        def list(self): return [ent("../victim.txt", 99)]
    rep = T.purge(Wrap(root), NOW, cfg=CFG)
    assert rep["errors"] and (tmp_path / "victim.txt").exists()


def test_unreadable_sidecar_is_an_error_not_a_delete(tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"data"); (tmp_path / "x.pdf.meta.json").write_text("{not json")
    rep = T.purge(T.FileStore(tmp_path), NOW, cfg=CFG)
    assert rep["errors"][0]["key"] == "x.pdf" and (tmp_path / "x.pdf").exists()


def test_cli_requires_now_and_reports(tmp_path, capsys):
    T.FileStore(tmp_path).add("d", NOW - timedelta(hours=60), "draft_upload")
    with pytest.raises(SystemExit):
        T.main([str(tmp_path)])
    assert T.main([str(tmp_path), "--now", NOW.isoformat(), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["expired"][0]["key"] == "d" and (tmp_path / "d").exists()


def test_worker_run_once_logs_audit_events():
    s = T.MemoryStore([ent("d", 99), ent("k", 1)])
    log = []
    rep = purge_job.run_once(s, NOW, log.append, CFG)
    assert rep["deleted"] == ["d"] and [e["key"] for e in log] == ["d"] and "k" in s.entries


def test_worker_start_without_apscheduler_fails_loudly(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name.startswith("apscheduler"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(RuntimeError):
        purge_job.start(T.MemoryStore())
