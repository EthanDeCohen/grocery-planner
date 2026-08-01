# db_script/

SQL scripts that define and evolve the Grocery Planner SQLite schema. This
directory is the source of truth for the schema — `grocery_planner/db.py`
reads and executes these files rather than embedding schema SQL inline.

## Layout

- **`init/`** — SQL that builds the CURRENT structure from scratch (a "blank
  database"). Kept up to date as the schema evolves: when you add a column
  or table, update the init script(s) so a brand-new database always starts
  fully current, in addition to adding a migration (below) for existing
  databases.
- **`migration/`** — incremental changes applied against an EXISTING
  database (e.g. adding a column that an older `init` baseline didn't have).

## Filenames

`NNNN_GFP-KEY.ddl` for structure changes, `NNNN_GFP-KEY.dml` for data
changes. Example: `0001_GFP-9.ddl`.

The zero-padded sequence prefix (`NNNN`) is **required**. Jira keys do not
sort correctly as text — `GFP-9` sorts after `GFP-100` lexically — so the
prefix is what guarantees scripts apply in a deterministic, intended order.
The prefix is a single counter shared across both `init/` and `migration/`
(it reflects when the change was authored, not which subdirectory it lives
in).

## How scripts are applied

`grocery_planner/db.py` runs every `init/*.ddl`/`*.dml` file in filename
order, then every `migration/*.ddl`/`*.dml` file in filename order, on every
`connect()` call:

- `init/` scripts use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT
  EXISTS`, so re-running them against a database that already matches is a
  safe no-op, and running them against an older database fills in any
  tables/indexes it's missing.
- `migration/` scripts are for changes `IF NOT EXISTS` can't express in
  SQLite (most commonly `ALTER TABLE ... ADD COLUMN`, which SQLite has no
  conditional form of). `db.py` applies these defensively: a "duplicate
  column name" or "already exists" error is treated as "already applied"
  rather than a failure, so re-running a migration against a database that
  already has the change is safe.

This intentionally stops short of a real migration runner or a
`schema_version` table — every script just needs to be safe to re-apply.
Ordered, tracked migrations (apply-once semantics, a version table) are
tracked separately as GFP-60.

## Adding a schema change

1. Update `init/` so a fresh database reflects the new structure.
2. Add a new `migration/NNNN_GFP-KEY.ddl` (or `.dml`) so existing databases
   pick up the change too. Make sure it's safe to re-run (see above).
3. Add/adjust tests in `tests/test_db.py` covering the new structure.
4. Update the `README.md` data-model section if it's a user-visible change,
   per `CONTRIBUTING.md`.
