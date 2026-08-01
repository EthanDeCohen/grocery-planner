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
    mode       TEXT NOT NULL DEFAULT 'executed',
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# How a script came to be recorded. Worth distinguishing when debugging a
# database later: "baselined" means it was never run against THIS database
# because its effect was already present when schema_version was introduced.
EXECUTED = "executed"
BASELINED = "baselined"


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


def _schema_fingerprint(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Every application table mapped to its column names.

    Deliberately structural only -- enough to answer "does this database
    already have the schema the scripts describe?" without caring about row
    contents. schema_version and SQLite's own bookkeeping are excluded.
    """
    names = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT IN ('schema_version', 'sqlite_sequence')"
        )
    ]
    return {
        name: {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
        for name in sorted(names)
    }


def _covers(expected: dict[str, set[str]], actual: dict[str, set[str]]) -> bool:
    """True when ``actual`` contains every table and column ``expected`` has.

    Deliberately a subset test, not equality: a database part-way through
    history legitimately has more than an early script produced.
    """
    return all(
        table in actual and columns <= actual[table]
        for table, columns in expected.items()
    )


def _adoption_point(conn: sqlite3.Connection, scripts: list[tuple[int, str, Path]]) -> int:
    """Highest sequence number this database already satisfies.

    Replays the scripts against an in-memory reference one at a time and
    compares after each. A real database is usually neither empty nor fully
    current -- it stopped wherever its owner last upgraded -- so the answer
    is a point in the sequence, not a yes/no. Everything up to that point is
    baselined; everything after it is executed for real.

    Migrations are cumulative, so once the reference outgrows the database it
    never fits again: the first miss ends the search.
    """
    actual = _schema_fingerprint(conn)
    reference = sqlite3.connect(":memory:")
    highest = 0
    try:
        for seq, _key, path in scripts:
            for stmt in _split_statements(path.read_text(encoding="utf-8")):
                reference.execute(stmt)
            if not _covers(_schema_fingerprint(reference), actual):
                break
            highest = seq
        return highest
    finally:
        reference.close()


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
    exactly once, each inside its own transaction.

    Nothing is ever swallowed. A script that fails is rolled back, left
    unrecorded, and raised -- so it surfaces again next connect instead of
    being silently marked done.

    That is only possible because db_script/init/0001 is a FROZEN baseline
    (GFP-60): every change after it lives solely in migration/, so a fresh
    database is "init + replay every migration" and a migration never
    legitimately collides with what is already there. The earlier convention
    folded each change into init/ as well, describing it twice, which made
    collisions inevitable -- and SQLite reports them with the same
    SQLITE_ERROR as a genuine typo, so they could not be told apart.

    The one exception is adoption: a database created before schema_version
    existed has no record of what it has run. If its structure already matches
    what the scripts produce, they are recorded as BASELINED rather than
    replayed; if it is genuinely behind, they are executed and bring it up to
    date. That comparison is structural, never based on error text.
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

    def _record(seq: int, jira_key: str, path: Path, checksum: str, mode: str) -> None:
        conn.execute(
            "INSERT INTO schema_version(seq, jira_key, filename, checksum, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (seq, jira_key, path.name, checksum, mode),
        )
        conn.commit()
        recorded[seq] = {
            "seq": seq, "jira_key": jira_key, "filename": path.name,
            "checksum": checksum,
        }

    # One-time adoption. A database with application tables but no
    # schema_version rows predates this tracking, and stopped wherever its
    # owner last upgraded -- so work out how far it actually got. Scripts up
    # to that point are recorded without being replayed (replaying them would
    # collide); everything after it is executed normally, which is what brings
    # the database up to date.
    adopt_through = (
        _adoption_point(conn, scripts)
        if not recorded and _schema_fingerprint(conn)
        else 0
    )

    for seq, jira_key, path in scripts:
        if seq in recorded:
            continue
        checksum = _checksum(path)

        if seq <= adopt_through:
            _record(seq, jira_key, path, checksum, BASELINED)
            continue
        statements = _split_statements(path.read_text(encoding="utf-8"))

        conn.execute("BEGIN")
        try:
            for stmt in statements:
                conn.execute(stmt)
        except sqlite3.Error as exc:
            # A real failure. Roll back, do NOT record, and raise -- so it is
            # attempted again (and reported) on the next connect rather than
            # being permanently marked done. Nothing is swallowed here: the
            # frozen init/ baseline means a migration never legitimately
            # collides with the schema it is applied to.
            conn.rollback()
            raise SchemaVersionError(
                f"db_script/{path.parent.name}/{path.name} (seq {seq}) failed to "
                f"apply: {exc}. It has NOT been recorded in schema_version, so it "
                "will be retried once fixed."
            ) from exc
        _record(seq, jira_key, path, checksum, EXECUTED)


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
