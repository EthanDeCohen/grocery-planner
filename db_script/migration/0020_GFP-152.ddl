-- GFP-152: which KIND of by-weight item a deal is.
--
-- sold_by already records HOW a price is denominated (GFP-98). It cannot
-- record the retail format, because soldBy=WEIGHT covers both a deli counter
-- cutting to order and a shrink-wrapped random-weight package -- two different
-- promises to a shopper, priced the same way.
--
-- Values, per grocery_planner/weight_basis.py:
--   'deli'         confirmed counter-cut
--   'prepackaged'  confirmed pre-packaged random weight
--   'unknown'      priced by weight, kind not established
--   NULL           the question does not apply (fixed-price package), or the
--                  source never stated a denomination at all -- every Flipp
--                  and csv-import row
--
-- NULL and 'unknown' are deliberately different: one is "not applicable", the
-- other is "applicable and we could not tell you". Collapsing them would put a
-- caveat on items that do not need one and hide it on items that do.
--
-- Derived at scrape time rather than stored raw, because the signal is the
-- Kroger `categories` array, which is not otherwise persisted. Same shape as
-- protein_kind (GFP-106): computed once where the evidence exists.

ALTER TABLE deals ADD COLUMN weight_basis TEXT;

ALTER TABLE price_history ADD COLUMN weight_basis TEXT;
