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

`grocery_planner/db.py` applies every script in sequence order, exactly once
per database, each inside its own transaction. Which scripts have run is
tracked in a `schema_version` table (seq, Jira key, filename, checksum, mode,
applied_at).

**A script that fails is rolled back, left unrecorded, and raises.** It is
never swallowed, so it surfaces again on the next connect rather than being
silently marked done.

### init/ is FROZEN — do not fold changes back into it

`init/0001_GFP-9.ddl` is the original baseline and does not change. Every
change made since lives **only** in `migration/`.

    fresh database  ==  init/0001  +  every migration replayed in order

This is the rule that makes "a failure is always a real failure" possible.

The earlier convention (GFP-59) folded each change into `init/` *and* kept its
migration script, describing the same change twice. Replaying both against one
database then collided by design — and SQLite reports that collision with the
same `SQLITE_ERROR` it uses for a genuine typo:

| statement | error |
|---|---|
| `ALTER TABLE t ADD COLUMN a` (already there) | `SQLITE_ERROR` |
| `UPDATE t SET nosuch = 1` (real bug) | `SQLITE_ERROR` |

Because the two are indistinguishable, any tolerance for the first also
silently hides the second. Freezing `init/` removes the collision instead of
trying to classify it.

**To see today's structure, build a database and introspect it — don't read
`init/`.** GFP-61 automates that comparison in CI.

### Adopting a database that predates schema_version

Such a database has no record of what it has run, so `db.py` compares its
actual structure against what the scripts produce:

- **already matches** → scripts are recorded as `baselined` (never executed
  against it), because replaying them would collide
- **genuinely behind** → pending scripts are executed normally, which repairs it

The comparison is structural, never based on error text. `mode` in
`schema_version` records which happened, so it is clear later which scripts
actually ran against a given database.

### Refuses to run when

- a recorded script's checksum changed (history was edited after being applied)
- a recorded script's file has disappeared
- the `NNNN` sequence has a gap or a duplicate

## Adding a schema change

1. Add a new `migration/NNNN_GFP-KEY.ddl` (structure) or `.dml` (data), taking
   the next free sequence number.
2. **Do not touch `init/`.**
3. Write it so it applies cleanly to a database that does not yet have the
   change — that is the only database it will ever run against.
4. Ship tests with it, as with any change.
