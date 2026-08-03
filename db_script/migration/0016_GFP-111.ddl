-- GFP-111: each source's OWN product identifier, promoted out of the free-text
-- `notes` blob into a real, namespaced column.
--
-- Every source already hands us a stable per-product id, but before this they
-- were stored three different ways and two of the three were buried in a
-- semicolon-joined debug string:
--
--   Kroger / Harris Teeter API : notes "... ; product_id=0020895500000; ..."
--   Whole Foods                : notes "... ; asin=B09439SKW3; ..."
--   Flipp (Food Lion, HT ad)   : flipp_item_id / flipp_coupon_id, real columns (GFP-15)
--
-- So anything wanting to print or order by a SKU -- the grocery list's SKU
-- column (GFP-112), the v2 online-order file (GFP-113) -- would have had to
-- string-parse `notes` for two sources out of three. `notes` is a debugging
-- aid whose shape no test pins; the day a scraper reorders or renames a key,
-- that parse silently produces nothing at all rather than failing loudly.
--
-- TWO COLUMNS, NOT ONE. A bare '0020895500000' is not self-describing. A
-- Kroger productId, an Amazon ASIN and a Flipp item id are different kinds of
-- thing that happen to be strings, and they must never be compared, joined or
-- deduplicated as if they were interchangeable -- '12345' as a Flipp item id
-- and '12345' as a Kroger productId denote two unrelated products. So the
-- namespace travels WITH the value, in the same row, and every read is
-- expected to consider the pair:
--
--   product_identifier     the identifier exactly as the source states it,
--                          kept as TEXT and verbatim -- Kroger productIds are
--                          zero-padded ('0020895500000') and an ASIN is
--                          alphanumeric ('B09439SKW3'), so neither survives
--                          being treated as a number.
--   product_identifier_ns  which vocabulary that value belongs to. Written by
--                          the scraper module that produced the row, from its
--                          own module-level constant:
--                            'kroger.product_id'   scrapers/kroger.py
--                            'wholefoods.asin'     scrapers/wholefoods.py
--                            'flipp.item_id'       scrapers/base.py, weekly ad
--                            'flipp.coupon_id'     scrapers/base.py, coupon
--                          Namespaced by SOURCE, never by store: `kroger.*`
--                          rows are Harris Teeter's, and one Flipp namespace
--                          serves Food Lion and Harris Teeter alike. Adding a
--                          store still touches nothing but its own module
--                          (GFP-32).
--
-- The two are set and cleared together: a row has both or neither. A value
-- with no namespace would be unusable (unidentifiable vocabulary) and a
-- namespace with no value says nothing, so the backfill below ends by
-- enforcing that invariant in both directions.
--
-- NULLABLE, AND NULL IS A REAL ANSWER. A csv-import row (the legacy Excel
-- export) has no source identifier at all, and neither does a Flipp weekly-ad
-- item whose flyer/item ids Flipp omitted. Inventing one -- hashing the item
-- name, reusing a row id -- would produce something that looks like a SKU and
-- would be handed to an ordering API, which is far worse than an absent value:
-- savings.py rule 1, absent stays absent, never a guess.
--
-- WHY IT MATTERS BEYOND A PRINTOUT. The stated v2 direction is generating an
-- ONLINE ORDER from a list. An order needs the retailer's own product id --
-- 'Harris Teeter Boneless Chicken Breast Value Pack, 1 lb' identifies nothing
-- to an ordering API. Capturing the identifier now is what makes that possible
-- later without re-scraping history that no longer exists upstream.
ALTER TABLE deals ADD COLUMN product_identifier TEXT;
ALTER TABLE deals ADD COLUMN product_identifier_ns TEXT;

-- Both on price_history too, for the same reason GFP-98 put sold_by on both:
-- `deals` is a current snapshot that run_scrape DELETEs and reinserts wholesale
-- on every scrape, so an identifier stored only there survives exactly one
-- week. History is where "what did this product cost in March" lives, and
-- answering that for an ordering API needs the id attached to the observation
-- itself, not to a snapshot that has since been overwritten.
ALTER TABLE price_history ADD COLUMN product_identifier TEXT;
ALTER TABLE price_history ADD COLUMN product_identifier_ns TEXT;

-- Queried by identifier ("has this exact product been seen before / at what
-- price"), which is the whole point of storing it. Namespace leads the index
-- because a lookup is always for a specific vocabulary's id -- asking for
-- '0020895500000' without saying whose is the mistake these columns exist to
-- make impossible.
CREATE INDEX IF NOT EXISTS idx_deals_product_identifier
    ON deals(product_identifier_ns, product_identifier);
CREATE INDEX IF NOT EXISTS idx_price_history_product_identifier
    ON price_history(product_identifier_ns, product_identifier);


-- --------------------------------------------------------------------------
-- ONE-TIME BACKFILL of rows captured before these columns existed.
-- --------------------------------------------------------------------------
-- The `notes` parsing below exists HERE AND ONLY HERE, deliberately. From this
-- migration forward the scrapers write the columns directly at scrape time
-- (scrapers/base.py, scrapers/kroger.py, scrapers/wholefoods.py), so nothing in
-- the read path ever parses `notes` -- which is what stops a `notes` reshuffle
-- from quietly emptying the column later. This runs exactly once per database
-- (db.py's schema_version bookkeeping, GFP-60) against rows whose `notes` shape
-- is already fixed and in the past, so it cannot rot.
--
-- Each statement is gated on the `source=` marker the scraper itself writes as
-- notes' first key, not merely on the presence of 'product_id=' / 'asin=', so a
-- coincidentally similar substring in some other source's ad copy can never be
-- read as that source's identifier.
--
-- SQLite has no regex, hence the instr/substr sawing: take everything after
-- '; <key>=' and cut it at the next ';' (or take the rest, when the key is
-- last). nullif(trim(...), '') turns a present-but-empty value into NULL rather
-- than storing '' as if it were an identifier.

-- Kroger / Harris Teeter API -> 'kroger.product_id'
UPDATE deals
   SET product_identifier_ns = 'kroger.product_id',
       product_identifier = nullif(trim(CASE
           WHEN instr(substr(notes, instr(notes, '; product_id=') + 13), ';') > 0
             THEN substr(substr(notes, instr(notes, '; product_id=') + 13), 1,
                         instr(substr(notes, instr(notes, '; product_id=') + 13), ';') - 1)
           ELSE substr(notes, instr(notes, '; product_id=') + 13)
       END), '')
 WHERE notes LIKE 'source=kroger_api;%'
   AND instr(notes, '; product_id=') > 0;

-- Whole Foods storefront -> 'wholefoods.asin'
UPDATE deals
   SET product_identifier_ns = 'wholefoods.asin',
       product_identifier = nullif(trim(CASE
           WHEN instr(substr(notes, instr(notes, '; asin=') + 7), ';') > 0
             THEN substr(substr(notes, instr(notes, '; asin=') + 7), 1,
                         instr(substr(notes, instr(notes, '; asin=') + 7), ';') - 1)
           ELSE substr(notes, instr(notes, '; asin=') + 7)
       END), '')
 WHERE notes LIKE 'source=wholefoods_storefront;%'
   AND instr(notes, '; asin=') > 0;

-- Flipp needs no parsing -- GFP-15 already promoted these into real columns.
-- They are copied across (as TEXT, so the column holds one kind of thing) so
-- that a reader has ONE place to look for "this row's source identifier"
-- regardless of source. flipp_item_id/flipp_coupon_id stay exactly where they
-- are; this does not replace them, and a weekly-ad row is never also a coupon.
UPDATE deals
   SET product_identifier_ns = 'flipp.item_id',
       product_identifier = CAST(flipp_item_id AS TEXT)
 WHERE product_identifier IS NULL
   AND flipp_item_id IS NOT NULL;

UPDATE deals
   SET product_identifier_ns = 'flipp.coupon_id',
       product_identifier = CAST(flipp_coupon_id AS TEXT)
 WHERE product_identifier IS NULL
   AND flipp_coupon_id IS NOT NULL;

-- The both-or-neither invariant, enforced rather than assumed: a source marker
-- present with an empty value (or vice versa) leaves nothing usable behind.
UPDATE deals SET product_identifier_ns = NULL WHERE product_identifier IS NULL;
UPDATE deals SET product_identifier = NULL WHERE product_identifier_ns IS NULL;

-- price_history carries no `notes` column of its own, so its historical rows
-- are recovered from the deals snapshot they were captured alongside, matched
-- on the natural key price_history is already keyed by (store, source,
-- postal_code, item_name -- see service/ingest.py's _HISTORY_UPSERT).
--
-- STRICTLY ONE CANDIDATE, OR NOTHING. The WHERE clause requires the key to
-- resolve to exactly one distinct (identifier, namespace) pair. Where a name
-- maps to two different products -- measured on the live database while
-- writing this: 64 of 3,178 history rows, mostly repeated Flipp ad items
-- carrying different item ids under one name -- the row keeps NULL. Picking
-- either candidate would be a guess, and a guessed SKU is the one failure mode
-- these columns exist to prevent (rule 1 again). Rows whose product has since
-- left the current snapshot keep NULL for the same reason: nothing to recover
-- from, and nothing invented.
UPDATE price_history
   SET product_identifier = (
           SELECT MIN(d.product_identifier) FROM deals d
            WHERE d.store = price_history.store
              AND d.source = price_history.source
              AND d.postal_code = price_history.postal_code
              AND d.item_name = price_history.item_name
              AND d.product_identifier IS NOT NULL
       ),
       product_identifier_ns = (
           SELECT MIN(d.product_identifier_ns) FROM deals d
            WHERE d.store = price_history.store
              AND d.source = price_history.source
              AND d.postal_code = price_history.postal_code
              AND d.item_name = price_history.item_name
              AND d.product_identifier IS NOT NULL
       )
 WHERE (
           SELECT COUNT(DISTINCT d.product_identifier_ns || ' ' || d.product_identifier)
             FROM deals d
            WHERE d.store = price_history.store
              AND d.source = price_history.source
              AND d.postal_code = price_history.postal_code
              AND d.item_name = price_history.item_name
              AND d.product_identifier IS NOT NULL
       ) = 1;
