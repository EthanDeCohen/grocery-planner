-- GFP-25: deal-to-food matching -- the missing link between the deals the
-- scrapers bring in and the protein-per-100g figures GFP-23 seeded, without
-- which cost-per-gram-of-protein (GFP-26) has nothing to compute.
--
-- One new table, deal_food_match: one row per matched (or manually
-- rejected) product, carrying a confidence and a method so "we think this
-- but aren't sure" is never silently indistinguishable from "we are sure"
-- (see grocery_planner/matching.py for the full matching-rule writeup).
--
-- Keying: (store, item_name), NOT deals.id.
--
-- deals.id is an AUTOINCREMENT surrogate that does not survive a rescrape:
-- service/ingest.run_scrape() DELETEs every row for a store+source+
-- postal_code and reinserts fresh ones with new ids every time it runs
-- (see grocery_planner/service/ingest.py). A match keyed on deals.id would
-- therefore be silently orphaned by the very next scrape. (store,
-- item_name), by contrast, is what actually identifies "the same product"
-- in this schema -- the real 1,573-row database has e.g. "foodlion" /
-- "Pork Chops" recurring, unchanged, across a csv-import row and multiple
-- later scrape rows, each under a different id. UNIQUE(store, item_name)
-- is what makes a nutritionist's correction (match_source='manual')
-- permanent: grocery_planner/matching.py's match_deals() never overwrites a
-- 'manual' row, so a correction survives every future rescrape and every
-- future re-run of the auto-matcher untouched.
--
-- food_id is nullable (not just the FK-required case): a nutritionist can
-- reject a match outright (matching.reject_match()) by pinning food_id to
-- NULL with match_source='manual', recording "a human looked at this and
-- it is not a protein deal" -- which is different information than no row
-- existing at all (nobody has looked yet).
--
-- confidence is a plain REAL in [0, 1] rather than an enum so a future
-- report can threshold or sort on it directly; method is free TEXT
-- recording *how* the match (or lack of one) was decided (e.g.
-- 'lean_pct_exact', 'category_fallback', 'manual_correction') -- useful for
-- auditing which rules are producing weak matches worth tightening.
CREATE TABLE IF NOT EXISTS deal_food_match (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    store        TEXT NOT NULL,
    item_name    TEXT NOT NULL,
    food_id      INTEGER REFERENCES foods(id) ON DELETE CASCADE,
    confidence   REAL,
    method       TEXT NOT NULL,
    match_source TEXT NOT NULL DEFAULT 'auto',   -- 'auto' or 'manual'
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(store, item_name)
);

CREATE INDEX IF NOT EXISTS idx_deal_food_match_food_id ON deal_food_match(food_id);
