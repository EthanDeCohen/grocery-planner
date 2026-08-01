-- GFP-54: add deals.postal_code so a scraped deal records which ZIP it was
-- scraped for. Without this, GFP-55's DELETE (store, source) scoping can't
-- tell one ZIP's rows from another, and scraping a second postal code wipes
-- out the first one's prices.
--
-- Backfill: every row that predates this column was scraped (or CSV
-- imported) back when the app only ever used one postal code. Both
-- registered scrapers default to "27401" (see
-- grocery_planner/scrapers/base.py StoreConfig.default_postal_code for
-- foodlion and harristeeter), and that is the same single ZIP the
-- household has used for every store's data so far -- so backfilling every
-- existing NULL row to "27401" reflects reality rather than guessing.
--
-- SQLite has no `ADD COLUMN IF NOT EXISTS`; grocery_planner/db.py treats a
-- "duplicate column name" OperationalError from this ALTER as "already
-- applied" (see db_script/README.md), so this file is safe to re-run: on a
-- database that already has the column, the ALTER fails fast and the
-- backfill (which would be a no-op anyway -- it only touches rows where
-- postal_code IS NULL) is simply skipped.
ALTER TABLE deals ADD COLUMN postal_code TEXT;

UPDATE deals SET postal_code = '27401' WHERE postal_code IS NULL;
