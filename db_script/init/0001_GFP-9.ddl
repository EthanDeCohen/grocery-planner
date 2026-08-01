-- GFP-9: baseline schema — builds a blank database from scratch.
--
-- This file is the CURRENT structure, kept up to date as the schema
-- evolves (see db_script/README.md for the convention). It is safe to run
-- against an existing database: every statement is idempotent
-- (IF NOT EXISTS), so re-running it on every `connect()` is a no-op once
-- the structure already matches.
--
-- Column/table additions after this baseline was first cut belong in
-- db_script/migration/ instead of being edited in here retroactively,
-- UNLESS the goal is specifically to keep this file describing "today's
-- structure" for a brand-new database (see GFP-59). The dollar_price
-- column below is one such case: GFP-6 added it after the original
-- GFP-9 schema shipped, but a fresh database should have it from the
-- start, so it is folded into this baseline rather than left to only be
-- added via the GFP-6 migration (db_script/migration/0002_GFP-6.ddl),
-- which exists for databases that predate this file.

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
    imported_at      TEXT,
    postal_code      TEXT  -- GFP-54: ZIP code the deal was scraped for
);

-- GFP-39: append-only price-history log. `deals` above stays a
-- current-snapshot table (a scrape replaces its prior rows); this is where
-- price movement over time actually lives. One row per (store, postal_code,
-- item_name, deal_type) per calendar day -- see
-- db_script/migration/0004_GFP-39.ddl for the upsert grain this depends on.
CREATE TABLE IF NOT EXISTS price_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    store            TEXT NOT NULL,
    postal_code      TEXT NOT NULL,
    item_name        TEXT NOT NULL,
    sub_category     TEXT,
    deal_type        TEXT,
    regular_price    REAL,
    sale_price       REAL,
    dollar_price     REAL,
    discount_amount  REAL,
    discount_percent REAL,
    source           TEXT,
    captured_at      TEXT NOT NULL,
    updated_at       TEXT,
    UNIQUE(store, postal_code, item_name, deal_type, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_price_history_lookup
    ON price_history(store, postal_code, item_name, captured_at);

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

-- Nutrition foundation (GFP-23): folded into the baseline so a fresh
-- database gets these tables immediately, in addition to
-- db_script/migration/0005_GFP-23.ddl which carries them to pre-existing
-- databases. See that file for the full rationale (nutrients are DATA, not
-- columns; category is data-driven, not a UI enum).
CREATE TABLE IF NOT EXISTS foods (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_ref)
);

CREATE TABLE IF NOT EXISTS food_nutrients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id         INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    nutrient        TEXT NOT NULL,
    amount_per_100g REAL,
    unit            TEXT,
    UNIQUE(food_id, nutrient)
);

CREATE INDEX IF NOT EXISTS idx_deals_store ON deals(store);
CREATE INDEX IF NOT EXISTS idx_prices_store ON prices(store);
CREATE INDEX IF NOT EXISTS idx_foods_category ON foods(category);
CREATE INDEX IF NOT EXISTS idx_food_nutrients_food_id ON food_nutrients(food_id);
CREATE INDEX IF NOT EXISTS idx_food_nutrients_nutrient ON food_nutrients(nutrient);
