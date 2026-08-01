-- GFP-15: give each deal a per-deal "View ad" link and its ad-clipping image,
-- so the client-facing "Where to buy" column (GFP-38/GFP-52) has something
-- to show instead of nothing. Both were already coming back from Flipp on
-- every scrape and being thrown away in grocery_planner/scrapers/base.py's
-- row builders.
--
-- source_url / image_url:
--   source_url is built from the flipp_flyer_id/flipp_item_id (weekly ad)
--   or flipp_coupon_id (digital coupon) below -- see the FLIPP_WEB_FLYER_URL
--   / FLIPP_WEB_COUPON_URL comment in grocery_planner/scrapers/base.py for
--   how that URL shape was verified (by hand, against the live flipp.com
--   site -- Flipp's *data* API never returns a browsable URL itself).
--   image_url is Flipp's own cutout_image_url (weekly ad) / coupon_image_url
--   (digital coupon) -- present on every deal observed while building this
--   ticket, but not a documented guarantee, so both columns are nullable
--   and a missing value is normal, not an error.
--
--   HONESTY REQUIREMENT: Flipp is a flyer aggregator, not a storefront.
--   source_url resolves to a weekly ad or a coupon page, NEVER a cart. Any
--   UI reading this column (GFP-38/GFP-52, out of scope here) must label it
--   "View ad", never "Buy now", and must render plain text -- not a dead
--   link/button -- when source_url is NULL.
--
-- flipp_flyer_id / flipp_item_id / flipp_coupon_id:
--   These already existed as free-text `key=value` pairs inside `notes`
--   (e.g. "flipp_flyer_id=7971624; flipp_item_id=1018281137"), which is fine
--   for provenance but means finding "every deal from flyer X" required a
--   `notes LIKE '%flipp_flyer_id=7971624%'` scan. Promoted here into real,
--   queryable columns. `notes` is left exactly as it was (still carries the
--   same key=value pairs) rather than having them stripped out, so nothing
--   that already reads `notes` (including existing tests) changes behavior --
--   these columns are additive.
--
--   A weekly-ad row has flipp_flyer_id + flipp_item_id and NULL
--   flipp_coupon_id; a digital-coupon row has flipp_coupon_id and NULL
--   flipp_flyer_id/flipp_item_id -- a deal is exactly one or the other,
--   never both, so no CHECK constraint is added for that (would only
--   complicate a table that's DELETEd and reinserted wholesale on every
--   scrape anyway -- see grocery_planner/service/ingest.py's run_scrape).
--
-- SQLite has no `ADD COLUMN IF NOT EXISTS`; grocery_planner/db.py tracks
-- which migrations have run in `schema_version` (GFP-60) rather than
-- relying on that, so this file runs exactly once per database regardless.
ALTER TABLE deals ADD COLUMN source_url TEXT;
ALTER TABLE deals ADD COLUMN image_url TEXT;
ALTER TABLE deals ADD COLUMN flipp_flyer_id INTEGER;
ALTER TABLE deals ADD COLUMN flipp_item_id INTEGER;
ALTER TABLE deals ADD COLUMN flipp_coupon_id INTEGER;
