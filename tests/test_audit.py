import sqlite3
import pytest
from modules.audit import AuditLog, GENESIS


def mk():
    return AuditLog(sqlite3.connect(":memory:"))


def test_chain_and_verify():
    a = mk(); h1 = a.append({"who": "r1", "act": "open"}); h2 = a.append({"act": "flag"})
    assert h1 != h2 and a.verify_chain() == (True, None)
    assert a.conn.execute("select prev_hash from audit_log where seq=1").fetchone()[0] == GENESIS


def test_empty_ok():
    assert mk().verify_chain() == (True, None)


def test_triggers_block_update_delete():
    a = mk(); a.append({"x": 1})
    with pytest.raises(sqlite3.DatabaseError):
        a.conn.execute("update audit_log set entry_json='{}' where seq=1")
    with pytest.raises(sqlite3.DatabaseError):
        a.conn.execute("delete from audit_log")


def test_tamper_detected_when_triggers_bypassed():
    a = mk(); a.append({"x": 1}); a.append({"x": 2}); a.append({"x": 3})
    a.conn.execute("drop trigger audit_log_no_update")
    a.conn.execute("update audit_log set entry_json='{\"x\":9}' where seq=2")
    assert a.verify_chain() == (False, 2)
