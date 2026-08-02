-- GFP-98: how a price is DENOMINATED, carried as data rather than inferred.
--
-- The Kroger/Harris Teeter source (GFP-77) marks every item `soldBy` as either
-- UNIT or WEIGHT, and the difference is not cosmetic:
--
--   soldBy=UNIT    "$4.99" buys the package.
--   soldBy=WEIGHT  "$2.49" buys ONE POUND. The package is whatever the cut
--                  happens to weigh.
--
-- Rendered in the same column with no distinction, a $2.49/lb pork loin looks
-- cheaper than a $4.99 packet of chicken when it is very often not -- the
-- shopper pays $2.49 times however many pounds the cut weighs. That is a wrong
-- buying decision produced from entirely correct data, so the denomination has
-- to reach the UI, which means it has to be stored (the GUI tickets GFP-36/37/
-- 38/48/50/52 now require the tag).
--
-- Nullable and NULL everywhere else on purpose: Flipp ad copy does not say how
-- a price is denominated, and a guess would be worse than an absent value --
-- rule 1 in savings.py. NULL means "not stated by the source", which is the
-- honest reading for every Flipp and CSV row.
ALTER TABLE deals ADD COLUMN sold_by TEXT;

-- The source's own per-unit price, kept verbatim rather than recomputed.
-- Kroger publishes regularPerUnitEstimate/promoPerUnitEstimate alongside the
-- headline price; Whole Foods publishes offerDetails.unitPrice. Both are the
-- retailer's own arithmetic over the retailer's own package size, so storing
-- it beats deriving it from a size string we had to parse out of a product
-- name -- and it is the comparison a nutritionist actually wants when choosing
-- between two cuts.
--
-- price_per_unit is the number; price_per_unit_uom is its denominator ("lb",
-- "oz", "each"), never assumed.
ALTER TABLE deals ADD COLUMN price_per_unit REAL;
ALTER TABLE deals ADD COLUMN price_per_unit_uom TEXT;

-- Same three on price_history, so a per-weight price stays identifiable after
-- the fact. Without this a historical row would be a bare number whose
-- denominator has been forgotten -- and comparing a $/lb observation against a
-- package-price observation is exactly the error the columns above exist to
-- prevent, only now invisible because it happened months ago.
ALTER TABLE price_history ADD COLUMN sold_by TEXT;
ALTER TABLE price_history ADD COLUMN price_per_unit REAL;
ALTER TABLE price_history ADD COLUMN price_per_unit_uom TEXT;
