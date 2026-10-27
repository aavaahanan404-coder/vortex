-- Append-only audit log (SQLite). modules/audit.py runs this script on init.
CREATE TABLE IF NOT EXISTS audit_log (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  prev_hash  TEXT NOT NULL,
  entry_json TEXT NOT NULL,      -- canonical JSON of the entry
  hash       TEXT NOT NULL       -- sha256(prev_hash || entry_json)
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
-- PostgreSQL equivalent:
--   CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
--   BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$ LANGUAGE plpgsql;
--   CREATE TRIGGER audit_log_no_mod BEFORE UPDATE OR DELETE ON audit_log
--   FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
--   (also REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM the application role)
