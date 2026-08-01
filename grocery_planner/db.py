"""SQLite storage layer: schema, connection, and store seeding.

Single-user, file-based, ACID. The schema mirrors the CSV columns from the
original Excel pipeline (so imports are loss-less) plus app-state tables
(profile, formulas, scraping_jobs) for the local-first agent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .paths import db_path
from .stores import STORES

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    key          TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    data_folder  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    store            TEXT NOT NULL,
    item_name        TEXT,
    sub_category     TEXT,
    deal_type        TEXT,
    deal_description TEXT,
    regular_price    REAL,
    sale_price       REAL,
    dollar_price     REAL,
    discount_amount  REAL,
    discount_percent REAL,
    valid_from       TEXT,
    valid_to         TEXT,
    loyalty_required TEXT,
    notes            TEXT,
    source           TEXT,
    imported_at      TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    store            TEXT NOT NULL,
    item_name        TEXT,
    brand            TEXT,
    category         TEXT,
    regular_price    REAL,
    sale_price       REAL,
    unit             TEXT,
    price_per_unit   REAL,
    on_sale          TEXT,
    loyalty_required TEXT,
    date_collected   TEXT,
    notes            TEXT,
    source           TEXT,
    imported_at      TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS formulas (
    name        TEXT PRIMARY KEY,
    expression  TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS scraping_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,
    status          TEXT,
    last_checkpoint TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    message         TEXT
);

-- Refresh cadence per store (GFP-7). Kept in our own table rather than an
-- APScheduler job store so the schedule survives restarts without pulling in
-- SQLAlchemy; the scheduler is rebuilt from these rows on every start.
CREATE TABLE IF NOT EXISTS schedules (
    store      TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,   -- 'interval' or 'cron'
    expression TEXT NOT NULL,   -- '6h' / '0 6 * * *'
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_deals_store ON deals(store);
CREATE INDEX IF NOT EXISTS idx_prices_store ON prices(store);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialize) the database, returning a Row-enabled connection."""
    target = path or db_path()
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive, idempotent migrations for DBs created before a column existed."""
    deal_cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    if "dollar_price" not in deal_cols:
        conn.execute("ALTER TABLE deals ADD COLUMN dollar_price REAL")


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables (idempotent), migrate, and seed the store registry."""
    conn.executescript(SCHEMA)
    _migrate(conn)
    for s in STORES:
        conn.execute(
            "INSERT INTO stores(key, display_name, data_folder) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET display_name=excluded.display_name, "
            "data_folder=excluded.data_folder",
            (s.key, s.display_name, s.data_folder),
        )
    conn.commit()
