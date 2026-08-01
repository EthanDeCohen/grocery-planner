"""SQLite storage layer: schema, connection, and store seeding.

Single-user, file-based, ACID. The schema mirrors the CSV columns from the
original Excel pipeline (so imports are loss-less) plus app-state tables
(profile, formulas, scraping_jobs) for the local-first agent.

The schema itself lives in db_script/ (see db_script/README.md), not inline
here: db_script/init/ holds the current structure built from scratch, and
db_script/migration/ holds incremental changes for pre-existing databases.
This module just locates and executes those .ddl/.dml files in order.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .paths import db_path
from .stores import STORES

SCRIPT_EXTENSIONS = (".ddl", ".dml")


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


def _apply_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    """Execute one script, treating "already applied" as success.

    init/ scripts use CREATE TABLE/INDEX IF NOT EXISTS and are naturally
    idempotent. migration/ scripts (e.g. ALTER TABLE ADD COLUMN) have no
    such conditional form in SQLite, so a "duplicate column name" or
    "already exists" OperationalError means the change is already present
    rather than a real failure -- safe to ignore so re-running every
    connect() stays a no-op on a database that's already current.
    """
    try:
        conn.executescript(path.read_text(encoding="utf-8"))
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column name" in msg or "already exists" in msg:
            return
        raise


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
    for f in _sql_files("init"):
        _apply_sql_file(conn, f)
    for f in _sql_files("migration"):
        _apply_sql_file(conn, f)
    for s in STORES:
        conn.execute(
            "INSERT INTO stores(key, display_name, data_folder) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET display_name=excluded.display_name, "
            "data_folder=excluded.data_folder",
            (s.key, s.display_name, s.data_folder),
        )
    conn.commit()
