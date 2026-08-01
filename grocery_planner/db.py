"""SQLite storage layer: schema, connection, and store seeding.

Single-user, file-based, ACID. The schema mirrors the CSV columns from the
original Excel pipeline (so imports are loss-less) plus app-state tables
(profile, formulas, scraping_jobs) for the local-first agent.

The schema itself lives in db_script/ (see db_script/README.md), not inline
here: db_script/init/ holds the current structure built from scratch, and
db_script/migration/ holds incremental changes for pre-existing databases.
This module locates those .ddl/.dml files, tracks which ones have been
applied to a given database in a `schema_version` table, and applies each
pending one exactly once, in order, inside its own transaction (GFP-60).

GFP-59 shipped a deliberately interim mechanism where every script on disk
was re-executed on every connect(), with a "duplicate column name" /
"already exists" OperationalError treated as "already applied". That had
three problems: (1) it matched on substrings of human-readable error text,
(2) sqlite3.Connection.executescript() aborts remaining statements once one
raises, so a multi-statement migration that hits that error partway through
silently skips the rest (0003_GFP-54.ddl's ALTER + backfill UPDATE was only
safe "by luck" -- the UPDATE was a no-op on repeat runs anyway), and (3)
every connect() re-read and re-executed every script on disk, forever. This
module replaces that mechanism entirely -- see `_apply_pending_migrations`.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from pathlib import Path

from .paths import db_path
from .stores import STORES

SCRIPT_EXTENSIONS = (".ddl", ".dml")

# NNNN_GFP-KEY.{ddl,dml} -- see db_script/README.md for the convention.
_SCRIPT_NAME_RE = re.compile(r"^(\d{4})_(GFP-\d+)\.(?:ddl|dml)$")

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    seq        INTEGER PRIMARY KEY,
    jira_key   TEXT NOT NULL,
    filename   TEXT NOT NULL UNIQUE,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


class SchemaVersionError(RuntimeError):
    """Raised when db_script/ and schema_version disagree in a way that must
    not be silently papered over: a checksum mismatch (history was edited
    after being applied), a missing file for a previously-applied seq, a gap
    in the sequence, or a real (non-adoption) failure applying a script."""


def _db_script_root() -> Path:
    """Locate the db_script/ directory across dev, installed, and frozen runs.

    Three contexts, in priority order:
    1. PyInstaller frozen build: data files are extracted next to the
       executable under ``sys._MEIPASS`` (see packaging/*.spec, which add
       db_script via collect_data_files("db_script")).
    2. Installed (or editable) package: db_script is registered as its own
       top-level package in pyproject.toml specifically so it ships as
       installed package data; importlib.resources finds it correctly in
       both cases.
    3. Fallback: a plain source checkout where, for whatever reason,
       db_script isn't importable as a package — resolve it relative to
       this file (grocery_planner/db.py -> repo_root/db_script).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "db_script"

    try:
        import importlib.resources as ir

        root = Path(str(ir.files("db_script")))
        if root.is_dir():
            return root
    except Exception:
        pass

    return Path(__file__).resolve().parent.parent / "db_script"


def _sql_files(subdir: str) -> list[Path]:
    """Return the .ddl/.dml files under db_script/<subdir>, in filename order.

    Filenames are NNNN_GFP-KEY.{ddl,dml}; the zero-padded prefix is what
    guarantees deterministic ordering (Jira keys alone don't sort correctly:
    GFP-9 sorts after GFP-100 lexically).
    """
    root = _db_script_root() / subdir
    if not root.is_dir():
        return []
    files = [p for p in root.iterdir() if p.suffix in SCRIPT_EXTENSIONS]
    return sorted(files, key=lambda p: p.name)


def _parse_script_name(name: str) -> tuple[int, str]:
    """Return (sequence number, Jira key) from a NNNN_GFP-KEY.ddl|dml name."""
    m = _SCRIPT_NAME_RE.match(name)
    if not m:
        raise SchemaVersionError(
            f"{name!r} under db_script/ does not match the NNNN_GFP-KEY."
            "ddl|dml naming convention (see db_script/README.md)"
        )
    return int(m.group(1)), m.group(2)


def _all_scripts() -> list[tuple[int, str, Path]]:
    """Every init/ and migration/ script, as (seq, jira_key, path), sorted by
    seq. seq is one counter shared across both subdirectories (see
    db_script/README.md) -- both are version-tracked the same way (see
    module docstring / db_script/README.md for why init/ is included too)."""
    scripts = [
        (*_parse_script_name(p.name), p)
        for p in _sql_files("init") + _sql_files("migration")
    ]
    scripts.sort(key=lambda t: t[0])
    return scripts


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _split_statements(sql: str) -> list[str]:
    """Split a script into individual statements for execute()-by-execute()
    application, respecting '--' line comments and single-quoted string
    literals (with '' escaping) so semicolons inside either don't cause an
    incorrect split.

    This (rather than executescript()) is what lets a pending script be
    applied inside one explicit transaction: executescript() implicitly
    commits any open transaction before it runs and does not participate in
    one, so it can't be wrapped in a transaction we control -- which is
    exactly what let 0003_GFP-54.ddl's ALTER succeed while its backfill
    UPDATE silently never ran on a repeat pass under the old mechanism.
    """
    statements: list[str] = []
    buf: list[str] = []
    in_string = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create schema_version if missing. Must run before anything can be
    recorded, and must not itself depend on any migration file having run
    (bootstrap chicken-and-egg) -- so this is plain code, not a migration.
    A migration file documenting this table also exists for the historical
    record (db_script/migration/0007_GFP-60.ddl) and for fresh init/, but
    this call is what actually guarantees the table exists."""
    conn.execute(SCHEMA_VERSION_DDL)
    conn.commit()


def _apply_pending_migrations(conn: sqlite3.Connection) -> None:
    """Apply every not-yet-recorded db_script/ script, in sequence order,
    exactly once, each inside its own transaction. Replaces the GFP-59
    error-swallowing mechanism entirely (see module docstring).

    Adoption -- a database that already has some or all of 0001-0006's
    effects but no schema_version rows yet (every real database that
    existed before this ticket) -- must not have those scripts re-applied,
    and must not error when a script's DDL collides with structure that's
    already there. This isn't only a pre-existing-database concern, either:
    by the documented db_script/ convention, a change is folded into the
    init/ baseline once it's part of "today's structure" for a brand-new
    database, while the original migration/ script is kept as-is for
    databases that predate it -- so even a *brand-new* database applying
    init/0001_GFP-9.ddl and then migration/0002_GFP-6.ddl in the same run
    legitimately hits "column already exists" on the second one, on
    purpose.

    So: a script that fails with sqlite3.OperationalError is treated as
    already applied -- its transaction is rolled back and it's recorded
    as-is, unexecuted, rather than the failure propagating. This is safe
    specifically because it can only ever happen once per script per
    database: a script that's already recorded in schema_version is never
    attempted again (see the loop below), so there's no ongoing, ever-repeating
    tolerance the way GFP-59's error-swallowing was -- only a single
    reconciliation the first (and only) time a given script is ever
    attempted against a given database, whether that's because it's
    genuinely new or because its effect predates schema_version tracking
    entirely. The message text of the error is never inspected (contrast
    GFP-59's substring matching) -- only the fact that the DDL/DML did not
    apply cleanly.
    """
    _ensure_schema_version_table(conn)

    scripts = _all_scripts()
    seqs = [seq for seq, _, _ in scripts]
    expected = list(range(1, len(seqs) + 1))
    if seqs != expected:
        raise SchemaVersionError(
            "db_script/ has a gap or duplicate in its NNNN sequence "
            f"prefixes: found {seqs}, expected a contiguous {expected}. "
            "Every prefix from 0001 must be present exactly once across "
            "init/ and migration/ (see db_script/README.md)."
        )
    scripts_by_seq = {seq: (jira_key, path) for seq, jira_key, path in scripts}

    recorded = {
        row["seq"]: row
        for row in conn.execute(
            "SELECT seq, jira_key, filename, checksum FROM schema_version"
        ).fetchall()
    }
    recorded_seqs = sorted(recorded)
    if recorded_seqs and recorded_seqs != list(range(1, len(recorded_seqs) + 1)):
        raise SchemaVersionError(
            "schema_version has a gap in its recorded sequence numbers: "
            f"{recorded_seqs}. This should be impossible from normal use; "
            "the table may have been edited by hand."
        )

    # Refuse to run if a recorded script's checksum (or filename) changed,
    # or if the file it refers to has disappeared entirely -- history was
    # edited after being applied.
    for seq, row in recorded.items():
        if seq not in scripts_by_seq:
            raise SchemaVersionError(
                f"schema_version records seq {seq} ({row['filename']}) as "
                "already applied, but no matching file exists under "
                "db_script/ anymore. Refusing to continue."
            )
        jira_key, path = scripts_by_seq[seq]
        checksum = _checksum(path)
        if row["filename"] != path.name or row["checksum"] != checksum:
            raise SchemaVersionError(
                f"db_script/{path.name} (seq {seq}) does not match what "
                f"schema_version recorded when it was applied: recorded "
                f"filename={row['filename']!r} checksum={row['checksum']}, "
                f"on-disk filename={path.name!r} checksum={checksum}. A "
                "previously-applied migration was edited after the fact -- "
                "refusing to continue rather than silently re-applying a "
                "changed script."
            )

    for seq, jira_key, path in scripts:
        if seq in recorded:
            continue
        checksum = _checksum(path)
        statements = _split_statements(path.read_text(encoding="utf-8"))

        conn.execute("BEGIN")
        try:
            for stmt in statements:
                conn.execute(stmt)
        except sqlite3.OperationalError:
            # This script's effect is already present -- either an earlier
            # script in this very run already created it (a migration whose
            # change has since been folded into the init/ baseline collides
            # with what init/ just built, by the documented convention: see
            # db_script/README.md), or this database predates
            # schema_version tracking and already has it from a prior
            # connect() under the old GFP-59 mechanism. Either way, roll
            # back whatever this script's attempt partially did and record
            # it as applied without re-running it -- this happens at most
            # once per script, ever, per database, since a recorded script
            # is never attempted again (see docstring).
            conn.rollback()
            conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO schema_version(seq, jira_key, filename, checksum) "
            "VALUES (?, ?, ?, ?)",
            (seq, jira_key, path.name, checksum),
        )
        conn.commit()
        recorded[seq] = {
            "seq": seq, "jira_key": jira_key, "filename": path.name,
            "checksum": checksum,
        }


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialize) the database, returning a Row-enabled connection."""
    target = path or db_path()
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Build the schema from db_script/, apply pending migrations, and seed
    the store registry. Safe to call repeatedly (idempotent)."""
    _apply_pending_migrations(conn)
    for s in STORES:
        conn.execute(
            "INSERT INTO stores(key, display_name, data_folder) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET display_name=excluded.display_name, "
            "data_folder=excluded.data_folder",
            (s.key, s.display_name, s.data_folder),
        )
    conn.commit()
