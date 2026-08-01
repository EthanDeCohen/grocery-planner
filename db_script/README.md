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

`grocery_planner/db.py` tracks which scripts have been applied to a given
database in a `schema_version` table (one row per script: sequence number,
Jira key, filename, a sha256 checksum of the file, and when it was applied).
On every `connect()` call, `db.py`:

1. Ensures `schema_version` exists (`CREATE TABLE IF NOT EXISTS`, in code —
   see "Bootstrap" below).
2. Reads every `init/*.ddl`/`*.dml` file, then every `migration/*.ddl`/`*.dml`
   file, in filename order — one counter shared across both subdirectories,
   same as always (see "Filenames" above) — and checks the `NNNN` prefixes
   are a contiguous run starting at `0001`. A gap or duplicate prefix fails
   loudly rather than silently skipping a script.
3. For every script already recorded in `schema_version`, verifies its
   checksum still matches the file on disk. A mismatch (the file was edited
   after being applied) fails loudly rather than silently re-applying a
   changed script.
4. Applies every *not yet recorded* script exactly once, in sequence order,
   each inside its own transaction, then records it. A script is never
   re-applied once recorded — this is what replaces the old error-swallowing
   approach (below).

Both `init/` and `migration/` scripts are version-tracked the same way.
`init/` scripts' `CREATE TABLE`/`CREATE INDEX ... IF NOT EXISTS` idempotency
is no longer what makes repeat-`connect()` calls safe (the `schema_version`
apply-once check is), but it's kept as defense in depth and because it's
what makes a script safe to apply at all on a database that's missing only
*some* of what it creates (the adoption case below).

### Bootstrapping and adopting an existing database

`schema_version` has to exist before anything — including the migration
file that documents it (`migration/0007_GFP-60.ddl`) — can be recorded into
it, so `db.py` creates it in code before applying any script. Code does not
depend on `0007_GFP-60.ddl` (or the copy folded into `init/0001_GFP-9.ddl`)
having run.

A real database created before this tracking existed already has the
effects of every script that predates it (e.g. `deals.dollar_price`,
`deals.postal_code`, `price_history`, `foods`/`food_nutrients`) but no
`schema_version` rows. This isn't only a pre-existing-database concern,
either: per the "fold into the init/ baseline" convention above, a
*brand-new* database applying `init/0001_GFP-9.ddl` and then
`migration/0002_GFP-6.ddl` in the very same run legitimately hits "column
already exists" on the second one, on purpose — the column is already part
of the baseline.

So: a script that fails with `sqlite3.OperationalError` when applied is
treated as already applied — its transaction is rolled back and it's
recorded as-is, unexecuted, rather than the failure propagating. The
message text of the error is never inspected (contrast the GFP-59 approach
below) — only the fact that it didn't apply cleanly. This is safe because it
can only ever happen *once* per script per database: a script recorded in
`schema_version` is never attempted again, so unlike GFP-59's error
swallowing (which ran, and could silently swallow, on *every* `connect()`
forever), this is a single reconciliation the one time a script is first
attempted — whether that's because it's genuinely new, or because its
effect predates `schema_version` tracking, or because an earlier script in
this same run (the baseline-folding case above) already produced it. A gap
in the `NNNN` sequence or a checksum mismatch against an already-recorded
script still fails loudly (above) — those are structural problems no amount
of "maybe it's already applied" reasoning should paper over.

### Why not the GFP-59 approach

GFP-59 shipped a deliberately interim mechanism: every script on disk was
re-executed on every `connect()`, and a "duplicate column name" / "already
exists" `OperationalError` was treated as "already applied" via a substring
match on the exception's message. That had three problems this ticket
(GFP-60) fixes: (1) matching on substrings of human-readable error text is
brittle; (2) `executescript()` aborts remaining statements once one raises,
so a multi-statement migration that hits that error partway through
silently skips the rest — `migration/0003_GFP-54.ddl`'s `ALTER TABLE ADD
COLUMN` followed by a backfill `UPDATE` was only safe *by luck* (the
`UPDATE` is a no-op on repeat runs anyway); the next multi-statement
migration would not have been so lucky; (3) every `connect()` re-read and
re-executed every script on disk, forever, instead of exactly once.

## Adding a schema change

1. Update `init/` so a fresh database reflects the new structure.
2. Add a new `migration/NNNN_GFP-KEY.ddl` (or `.dml`) so existing databases
   pick up the change too. Each script is applied at most once per database
   (`schema_version`), so it no longer needs to survive being *re-run*
   against a database that already has the change — but keep it safe to
   apply against a database that's missing only *some* of what it creates
   (e.g. `CREATE TABLE IF NOT EXISTS`), since that's the normal case for any
   database that isn't brand new.
3. Add/adjust tests in `tests/test_db.py` covering the new structure.
4. Update the `README.md` data-model section if it's a user-visible change,
   per `CONTRIBUTING.md`.
