-- GFP-60: proper migration runner -- schema_version table.
--
-- This documents the table in the db_script/ convention/history, but
-- grocery_planner/db.py does NOT depend on this file having run: it
-- creates schema_version in code (idempotently, CREATE TABLE IF NOT
-- EXISTS) before applying any script, because schema_version has to exist
-- before anything -- including this file -- can be recorded into it. See
-- _ensure_schema_version_table in grocery_planner/db.py.
--
-- Once schema_version exists (which it always does by the time this file
-- would be considered), running this again is a safe no-op, and this file
-- itself gets recorded as seq 7 like any other script.
CREATE TABLE IF NOT EXISTS schema_version (
    seq        INTEGER PRIMARY KEY,
    jira_key   TEXT NOT NULL,
    filename   TEXT NOT NULL UNIQUE,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
