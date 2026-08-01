-- GFP-39: stop losing price history.
--
-- `deals` stays the current-snapshot table it always was (GFP-55 now scopes
-- its scrape DELETE by store+source+postal_code, but it is still a "replace
-- what's there" table, not an append-only log). This adds a NEW table,
-- `price_history`, that a scrape appends to instead, so a price observed
-- today is never overwritten -- only ever superseded by a later day's row.
--
-- Grain: one row per (store, postal_code, item_name, deal_type) per
-- calendar day. `captured_at` is a date, not a timestamp, and the
-- ON CONFLICT clause in grocery_planner/service/ingest.py upserts into that
-- same day's row rather than inserting a new one -- re-running a scrape
-- twice in one day updates today's price, it does not fabricate a second
-- "price change" out of nothing.
--
-- CREATE TABLE IF NOT EXISTS is naturally idempotent, so unlike the
-- ALTER TABLE migrations in this directory this needs no special error
-- handling to be safe to re-run.
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
