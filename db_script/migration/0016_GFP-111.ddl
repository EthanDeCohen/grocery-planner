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
-- column (GFP-112), the v2 online-order file -- would have had to string-parse
-- `notes` for two sources out of three. `notes` is a debugging aid whose shape
-- no test pins; the day a scraper reorders or renames a key, that parse
-- silently produces nothing at all rather than failing loudly.
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
-- NULLABLE, AND NULL IS A REAL ANSWER. Nothing here invents an identifier for
-- a row that has none. Hashing the item name or reusing a row id would produce
-- something that looks exactly like a SKU and would eventually be handed to an
-- ordering API -- far worse than an absent value: savings.py rule 1, absent
-- stays absent, never a guess.
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

-- No index on either pair, deliberately. Nothing reads these columns by value
-- yet -- GFP-112 prints the identifier it already has on the row, it does not
-- look rows up by it -- and `deals` is DELETEd and reinserted wholesale on
-- every scrape (service/ingest.py::run_scrape), so an index would be paid for
-- on every write to serve a query that does not exist. When something does
-- look products up by identifier, it should be indexed then, on
-- (product_identifier_ns, product_identifier) so the namespace leads: a lookup
-- is always for a specific vocabulary's id, and asking for '0020895500000'
-- without saying whose is the mistake these columns exist to make impossible.


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

-- STEP 1: Flipp, with no parsing at all -- GFP-15 already promoted these into
-- real columns. They are restated in the source-agnostic pair (as TEXT, so the
-- column holds one kind of thing) so a reader has ONE place to look for "this
-- row's product identifier" whatever produced it. flipp_item_id/flipp_coupon_id
-- stay exactly where they are; this adds a column, it does not replace one.
-- A weekly-ad row is never also a coupon (verified on the live database: 0 rows
-- carry both), so the two namespaces cannot fight over the same row.
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

-- STEP 2: everything still without one, recovered from `notes`.
--
-- Four (marker, key, namespace) triples rather than four near-identical blobs
-- of instr/substr sawing, so the parse itself is written once and a reviewer
-- compares four short rows instead of diffing SQL by eye. The temp tables are
-- scratch space inside this migration's own transaction and are dropped below;
-- they are never part of the schema (they live in the `temp` database, which is
-- why they do not appear in schema_snapshot.txt).
--
--   marker  the `source=` key the scraper itself writes FIRST in notes. Gating
--           on this rather than merely on the presence of 'product_id=' means a
--           coincidentally similar substring in some other source's ad copy can
--           never be read as that source's identifier. Compared with substr()
--           equality, not LIKE: 'source=kroger_api' contains an underscore, and
--           LIKE would treat it as a single-character wildcard.
--   key     the notes key holding the id, INCLUDING its '; ' separator, so a
--           key can never match as the tail of a longer one ('; item_id=' would
--           otherwise also hit 'flipp_item_id=').
--   ns      the namespace, identical to the constant the matching scraper
--           module now writes at scrape time.
CREATE TEMP TABLE gfp111_notes_key(marker TEXT NOT NULL, key TEXT NOT NULL, ns TEXT NOT NULL);
INSERT INTO gfp111_notes_key(marker, key, ns) VALUES
    ('source=kroger_api',           '; product_id=',    'kroger.product_id'),
    ('source=wholefoods_storefront', '; asin=',         'wholefoods.asin'),
    -- The Flipp rows reaching here are the legacy CSV-imported ones (the Excel
    -- export that predates GFP-15's real columns): their flipp_item_id /
    -- flipp_coupon_id are NULL, but the id itself is sitting verbatim in notes.
    -- Recovering it is not a guess -- the value is literally present, and it is
    -- the same Flipp vocabulary the scraped rows use -- so leaving those rows
    -- NULL would lose a real identifier, not protect us from a fabricated one.
    -- Note the coupon key is 'coupon_id', not 'flipp_coupon_id': that is what
    -- scrapers/base.py::coupon_to_row has always written into notes.
    ('source=weekly_ad',            '; flipp_item_id=', 'flipp.item_id'),
    ('source=digital_coupon',       '; coupon_id=',     'flipp.coupon_id');

-- Take everything after the key and cut it at the next ';' (or take the rest,
-- when the key is last) -- SQLite has no regex, hence the sawing. Written once,
-- over the join above. nullif(trim(...), '') turns a present-but-empty value
-- into NULL rather than storing '' as though it were an identifier.
-- The markers are anchored at position 1 and mutually exclusive, so a row can
-- match at most one triple.
CREATE TEMP TABLE gfp111_from_notes AS
SELECT deal_id,
       ns,
       nullif(trim(CASE WHEN instr(tail, ';') > 0
                        THEN substr(tail, 1, instr(tail, ';') - 1)
                        ELSE tail END), '') AS value
  FROM (
        SELECT d.id AS deal_id,
               k.ns AS ns,
               substr(d.notes, instr(d.notes, k.key) + length(k.key)) AS tail
          FROM deals d
          JOIN gfp111_notes_key k
            ON substr(d.notes, 1, length(k.marker) + 1) = k.marker || ';'
           AND instr(d.notes, k.key) > 0
         WHERE d.product_identifier IS NULL
       );

UPDATE deals
   SET product_identifier = (
           SELECT p.value FROM gfp111_from_notes p WHERE p.deal_id = deals.id
       ),
       product_identifier_ns = (
           SELECT p.ns FROM gfp111_from_notes p WHERE p.deal_id = deals.id
       )
 WHERE id IN (SELECT deal_id FROM gfp111_from_notes WHERE value IS NOT NULL);

DROP TABLE gfp111_from_notes;
DROP TABLE gfp111_notes_key;

-- The both-or-neither invariant, enforced rather than assumed: a source marker
-- present with an empty value (or vice versa) leaves nothing usable behind.
UPDATE deals SET product_identifier_ns = NULL WHERE product_identifier IS NULL;
UPDATE deals SET product_identifier = NULL WHERE product_identifier_ns IS NULL;

-- STEP 3: price_history carries no `notes` column of its own, so its historical
-- rows are recovered from the deals snapshot they were captured alongside,
-- matched on the same natural key price_history is already keyed by (store,
-- source, postal_code, item_name -- see service/ingest.py's _HISTORY_UPSERT).
--
-- STRICTLY ONE CANDIDATE, OR NOTHING. The WHERE clause requires the key to
-- resolve to exactly one distinct (namespace, value) pair. Where one name maps
-- to two different products -- repeated Flipp ad items carrying different item
-- ids under a single name -- the row keeps NULL, because picking either
-- candidate would be a guess and a guessed SKU is the exact failure mode these
-- columns exist to prevent (rule 1 again). Rows whose product has since left
-- the current snapshot keep NULL for the same reason: there is nothing to
-- recover from, and nothing is invented to fill the gap.
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
