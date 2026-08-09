-- GFP-257: which supported stores actually serve a given ZIP.
--
-- Until now nothing gated a store by ZIP. run_scrape(store_key, postal_code)
-- took both and simply tried, so scraping Greensboro 27401 would happily call a
-- New York-only banner, and every ZIP scrape called every registered scraper.
-- At six scrapers that is waste; at the ~20 GFP-165 proposes across six markets
-- it is the arithmetic that decides whether expansion is affordable at all.
--
-- There was also a correctness consequence. GFP-67's replace-guard raises
-- EmptyScrapeError on a zero-row scrape, deliberately, because a silent empty
-- parse used to wipe good data. But a store that simply does not operate in a
-- ZIP also returns zero rows -- so a healthy scraper pointed at the wrong
-- market was indistinguishable from a broken parser. "Not in this market" and
-- "the parse broke" are different outcomes and now have different answers.
--
-- THREE STATES, NOT TWO. serves / does not serve / unknown. Collapsing
-- "we could not find out" into "no" would silently remove a store the client
-- may genuinely have, which is worse than showing it with a caveat -- the same
-- rule savings.py states as rule 1, applied to availability.
--
-- Keyed on SCRAPER key, not store key. The question being answered is "should
-- this scraper run for this ZIP", and two feeds for one shop can differ:
-- harristeeter (Flipp ad) and harristeeter-api (Kroger shelf prices) are the
-- same physical store but reach it by different routes with different
-- footprints.
--
-- DERIVED AND CACHED, never authoritative. Store footprints change on a
-- timescale of months, so this is resolved when a ZIP is registered and
-- refreshed on a long TTL. checked_at is what makes the TTL possible and what
-- makes a stale answer auditable.
--
-- NOTHING HERE IS READ ON THE SOLVE PATH. The optimiser's invariant is that
-- the same inputs always produce the same plan; availability is resolved at
-- registration and refresh time and read from this table thereafter, never
-- asked over the network while a plan is being built.

CREATE TABLE IF NOT EXISTS store_availability (
    scraper_key TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    -- 'serves' | 'does_not_serve' | 'unknown' -- see availability.py
    state       TEXT NOT NULL,
    -- How the answer was reached: 'location_api', 'service_area', 'observed',
    -- or 'no_capability'. Recorded so a surprising answer can be traced to the
    -- evidence behind it rather than re-derived.
    method      TEXT NOT NULL,
    checked_at  TEXT NOT NULL,
    PRIMARY KEY (scraper_key, postal_code)
);

CREATE INDEX IF NOT EXISTS idx_store_availability_zip
    ON store_availability(postal_code, state);
