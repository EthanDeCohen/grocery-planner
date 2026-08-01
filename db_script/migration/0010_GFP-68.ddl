-- GFP-68: give foods a stable slug, separate from external provenance.
--
-- source_ref was carrying two different meanings:
--   * for curated rows, the app's stable identity ('beef-ribeye-steak')
--   * for USDA rows, external provenance (the FDC id, '2646172')
--
-- `gplan nutrition sync` supersedes a curated row in place and overwrites
-- source_ref with the FDC id, which silently destroyed the identity
-- grocery_planner/matching.py keys its whole vocabulary on. One sync made
-- eight foods unmatchable -- every beef cut plus chicken breast, the
-- highest-value protein items in the catalog.
--
-- slug is now the stable internal identity and never changes; source_ref is
-- purely "where this figure came from" and is free to change with provenance.
--
-- Backfill: every existing row's source_ref IS its slug today. `nutrition
-- sync` only shipped in the same release as this fix, so no database has yet
-- had a source_ref replaced by an FDC id at the point this migration runs.
ALTER TABLE foods ADD COLUMN slug TEXT;

UPDATE foods SET slug = source_ref WHERE slug IS NULL;

CREATE INDEX IF NOT EXISTS idx_foods_slug ON foods(slug);
