-- GFP-9: the ORIGINAL baseline schema, frozen.
--
-- READ THIS BEFORE EDITING (GFP-60 changed the rules):
--
-- This file is a FROZEN historical baseline, not a living picture of today's
-- schema. It is executed only against a brand-new database, and every change
-- made after it lives solely in db_script/migration/, applied in sequence on
-- top of this file. Fresh database == this file + every migration replayed.
--
-- DO NOT fold a new table or column back into this file. That was the earlier
-- convention (GFP-59) and it described each change twice, so replaying both
-- against one database collided by design -- and SQLite reports that collision
-- with the same SQLITE_ERROR it uses for a genuine typo, making the two
-- impossible to tell apart. Keeping this file frozen means a migration failure
-- is always a real failure, and can always be raised rather than guessed at.
--
-- To see today's structure, build a database and introspect it -- do not read
-- this file (GFP-61 automates that comparison in CI).

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
