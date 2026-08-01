-- GFP-28: the customers table -- the client (nutritionist-customer) record.
--
-- This is the first HAND-ENTERED, IRREPLACEABLE data the schema holds.
-- Every other table so far (deals, prices, foods, ...) is re-scrapable or
-- re-importable; a customer row is not -- if it is lost, a nutritionist has
-- to re-interview their client. That drives two decisions below.
--
-- 1. Weight units (the "2.2x bug"). The existing protein formula
--    (weight * 1.6, grams per KILOGRAM -- grocery_planner/formulas.py) is
--    correct only if `weight` is in kg. A nutritionist typing "90" meaning
--    pounds, stored as-is and later fed into that formula, silently produces
--    144 g/day of protein instead of ~65g/day -- a 2.2x dosing error in a
--    health tool. So weight is split into two columns instead of one:
--      weight_kg    -- canonical, ALWAYS kilograms; this is what GFP-29's
--                      protein-target math reads. Naming it *_kg (rather
--                      than the bare "weight" a first pass might reach for)
--                      is deliberate: a column that cannot be misread as
--                      "whatever unit the user typed" is what makes it
--                      impossible for GFP-29 to repeat this mistake.
--      weight_unit  -- 'kg' or 'lb', exactly as the user chose when they
--                      entered the value. Never used for math -- only so
--                      the UI can redisplay in the unit the user actually
--                      thinks in, instead of silently normalizing their
--                      input to kg on screen. See grocery_planner/customers.py
--                      (kg_to_lb/lb_to_kg) for the conversion, applied once
--                      at save time; nothing downstream re-derives kg from
--                      pounds.
--
-- 2. Deletion safety. An accidental delete of a customer cannot be undone by
--    re-scraping or re-importing anything, unlike every other table here.
--    customers.deleted_at makes delete() a soft delete (NULL = active, a
--    timestamp = deleted-but-still-on-disk) rather than destroying the row,
--    so a misclick is recoverable. See grocery_planner/customers.py for the
--    full rationale (soft-delete vs. a confirm flag).
--
-- Out of scope here (see GFP-28 ticket): protein target calculation
-- (GFP-29), per-client protein preferences (GFP-30), postal_code (GFP-57),
-- any GUI/CLI wiring (GFP-33), photos (GFP-47).
CREATE TABLE IF NOT EXISTS customers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    weight_kg      REAL,               -- canonical; see note above
    weight_unit    TEXT,               -- 'kg' or 'lb' as originally entered; display only
    height_cm      REAL,
    age            INTEGER,
    sex            TEXT,
    activity_level TEXT,
    goal           TEXT,
    protein_factor REAL NOT NULL DEFAULT 1.6,
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at     TEXT                -- soft delete; NULL = active
);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_customers_deleted_at ON customers(deleted_at);
