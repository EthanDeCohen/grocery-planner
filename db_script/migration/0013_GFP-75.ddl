-- GFP-75: durable record low/high per item, surviving history retention.
--
-- WHY A TABLE AND NOT A QUERY
--
-- price_history (GFP-39) accumulates raw observations, and GFP-42 will prune
-- it. An all-time record low computed by scanning that history therefore
-- disappears the moment retention runs, and -- this is the part that makes
-- ordering matter -- it cannot be recomputed afterwards from anything,
-- because the observation it was derived from is gone. Records are cheap to
-- maintain incrementally on write and IMPOSSIBLE to reconstruct later, which
-- is the whole argument for storing rather than deriving them.
--
-- This is why GFP-75 must land before or with GFP-42. If retention ships
-- first, every historic low is destroyed unrecoverably.
--
-- KEYING: (store, postal_code, item_name)
--
-- NOT deals.id, which does not survive a rescrape -- run_scrape() DELETEs a
-- store+source+postal_code's rows and reinserts with fresh ids every time, so
-- anything keyed on id is orphaned by the next scrape. This is the same
-- natural key GFP-25 fell back to for the same reason (see
-- migration/0009_GFP-25.ddl).
--
-- postal_code is part of the key, unlike deal_food_match's (store,
-- item_name): a record LOW is a price, and prices are per-location. The
-- GFP-76 spike measured the same Food Lion SKU at $5.39/lb and $4.79/lb in
-- different ZIPs. Pooling those into one "record low" would report a price
-- the customer's own store never offered.
--
-- deal_type is deliberately NOT in the key, although price_history has it
-- there. History records what was observed and must distinguish a coupon
-- observation from a weekly-ad one on the same day; a record answers "what is
-- the least this item has ever cost here", and the customer does not care
-- which mechanism produced it.
--
-- WHY BOTH PRICE AND COST-PER-GRAM-PROTEIN
--
-- $/g protein is the metric this product actually optimises, and the two can
-- move in opposite directions: a price can FALL while $/g protein RISES if
-- the package shrank. Tracking only price would make shrinkflation invisible
-- in exactly the tool meant to catch it. The cpgp columns are nullable
-- throughout -- most deals cannot resolve a $/g protein at all (89 of 246 for
-- the best source we have), and per savings.py's rule 1 a missing number is
-- NULL, never a guess.
--
-- WHAT IS NOT HERE: ROLLING WINDOWS
--
-- The ticket also asks for rolling 30/90-day records. Those are deliberately
-- NOT stored, because a rolling window cannot be maintained incrementally: as
-- the window slides, the current minimum can fall out of it, and recovering
-- the new minimum requires re-reading the observations inside the window.
-- A stored rolling value could only ever be a cache that silently goes stale
-- between scrapes, and a stale "30-day low" is worse than none.
--
-- They are computed on demand from price_history instead (see
-- grocery_planner/records.py::rolling_window), which is correct as long as
-- retention keeps at least the longest window. That is a real coupling and
-- GFP-42 must honour it -- records.RETENTION_FLOOR_DAYS states the floor in
-- code so the constraint is enforceable rather than remembered.
CREATE TABLE IF NOT EXISTS price_records (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    store                 TEXT NOT NULL,
    postal_code           TEXT NOT NULL,
    item_name             TEXT NOT NULL,

    -- All-time price extremes, and the day each was observed. The _at
    -- columns are what make a record actionable: "$3.99, first seen 8 months
    -- ago" and "$3.99, first seen last week" are very different signals.
    record_low_price      REAL,
    record_low_price_at   TEXT,
    record_high_price     REAL,
    record_high_price_at  TEXT,

    -- All-time cost-per-gram-of-protein extremes. Nullable and frequently
    -- NULL -- see the note above.
    record_low_cpgp       REAL,
    record_low_cpgp_at    TEXT,
    record_high_cpgp      REAL,
    record_high_cpgp_at   TEXT,

    -- Provenance for the whole row, not for either extreme: when this item
    -- was first and most recently seen at all. first_seen_at doubles as the
    -- honest caveat on a record -- an item first seen yesterday has an
    -- all-time low that means very little.
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    observations          INTEGER NOT NULL DEFAULT 0,

    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(store, postal_code, item_name)
);

CREATE INDEX IF NOT EXISTS idx_price_records_lookup
    ON price_records(store, postal_code, item_name);

-- Supports "cheapest protein ever recorded here", the query the
-- recommendation engine (GFP-31) will want.
CREATE INDEX IF NOT EXISTS idx_price_records_cpgp
    ON price_records(store, postal_code, record_low_cpgp);
