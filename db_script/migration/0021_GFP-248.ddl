-- GFP-248: link a store's PROMO feed row to its CATALOGUE feed row.
--
-- A store may have two sources (scrapers/__init__.py): a Flipp weekly ad that
-- carries promotional prices, and a catalogue feed -- Kroger's API for
-- harristeeter, the PRISM product pages for foodlion -- that carries sizes and
-- nutrition the ad never has. Neither may evict the other, so `deals` holds
-- both, scoped by (store, source, postal_code).
--
-- Nothing joined them. Measured 2026-08-08 on the live database, for the one
-- store that already had both feeds:
--
--     harristeeter, source='scrape'      406 distinct item_names
--     harristeeter, source='kroger-api'  977 distinct item_names
--     EXACT item_name overlap              0
--
-- Zero, because the two feeds name the same product differently: the ad says
-- "Gatorade", the catalogue says "Harris Teeter Boneless Chicken Breast Value
-- Pack, 1 lb". deal_food_match is keyed on (store, item_name), so they become
-- two unrelated rows.
--
-- The cost of that: savings.cost_per_gram_protein needs a weight-based size,
-- and it parses that size out of the ITEM NAME. A promotional name carries no
-- size, so the promotional price -- the single number a nutritionist opens this
-- app to find -- is the one price that cannot become a cost per gram of
-- protein. The catalogue row for the very same product has the size, at full
-- price. We were systematically ranking regular prices above sale prices on
-- the metric the product exists to compute.
--
-- This table is that missing edge. It is DERIVED, rebuilt by
-- grocery_planner/sourcelink.py after every ingest, and holds no fact that is
-- not recomputable from `deals` and `deal_food_match`.
--
-- Keyed on (store, item_name) to match deal_food_match's natural key -- a
-- product's stable identity in this schema is its name at a store, not a
-- deals.id, because deals rows are replaced wholesale on every scrape.
--
-- confidence and method are carried for the same reason deal_food_match
-- carries them: a join we are not sure of must be visible as such, and
-- suppressible, rather than silently folded into a price a client acts on.

CREATE TABLE IF NOT EXISTS deal_source_link (
    store            TEXT    NOT NULL,
    -- The row that LACKS a parseable size: the promotional one.
    item_name        TEXT    NOT NULL,
    -- The row that HAS one: the catalogue entry for the same product.
    linked_item_name TEXT    NOT NULL,
    -- Which food both sides independently matched to. Recorded rather than
    -- re-derived so a link can be audited without re-running the matcher,
    -- and so a later change to matching rules is visibly a change.
    food_id          INTEGER,
    confidence       REAL    NOT NULL,
    method           TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    PRIMARY KEY (store, item_name)
);

-- The reverse lookup: "what promotions point at this catalogue entry?" Needed
-- to audit a suspicious size, and cheap.
CREATE INDEX IF NOT EXISTS idx_deal_source_link_target
    ON deal_source_link(store, linked_item_name);
