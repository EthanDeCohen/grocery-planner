-- GFP-23: foundation tables for nutrition data.
--
-- The product is moving toward optimising cost per gram of protein for
-- nutritionist clients, but nothing in the schema stores nutrition data
-- today. This lays down two deliberately simple, general tables:
--
--   foods           -- one row per known food (name/category/provenance)
--   food_nutrients  -- one row per (food, nutrient) fact, e.g. protein
--
-- Nutrients are DATA, not columns. Protein is just the first
-- food_nutrients.nutrient value populated; fibre, carbs, fat, etc. scaffold
-- in later as more rows, not as a schema migration. See db_script/README.md
-- for why this lives in migration/ (for pre-existing databases) as well as
-- being folded into the init/ baseline (0001_GFP-9.ddl) for fresh ones.
--
-- foods.category is intentionally a free TEXT column, not an enum/FK to a
-- lookup table: the v1 client UI offers six checkboxes (beef, pork, chicken,
-- fish, tofu, whey), sourced from the data (SELECT DISTINCT category FROM
-- foods) rather than hard-coded in a UI layer, so a seventh category is a
-- row of data rather than a schema/UI change (see grocery_planner/nutrition.py).
--
-- foods.source/source_ref distinguish curated starter data (GFP-23,
-- source='curated') from a later USDA FoodData Central ingest (GFP-24,
-- source='usda', source_ref=fdc_id), so USDA data can supersede curated
-- rows without a schema change. UNIQUE(source, source_ref) is what makes
-- the GFP-23 seed catalog (0006_GFP-23.dml) idempotent to re-insert.
--
-- Deal-to-food matching (GFP-25) is explicitly out of scope here.
CREATE TABLE IF NOT EXISTS foods (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_ref)
);

-- One row per (food, nutrient). amount_per_100g/unit follow the nutrient:
-- protein is grams per 100g today; a future fibre/carbs/fat row is just
-- another insert with its own unit, no ALTER TABLE required.
CREATE TABLE IF NOT EXISTS food_nutrients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id         INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    nutrient        TEXT NOT NULL,
    amount_per_100g REAL,
    unit            TEXT,
    UNIQUE(food_id, nutrient)
);

CREATE INDEX IF NOT EXISTS idx_foods_category ON foods(category);
CREATE INDEX IF NOT EXISTS idx_food_nutrients_food_id ON food_nutrients(food_id);
CREATE INDEX IF NOT EXISTS idx_food_nutrients_nutrient ON food_nutrients(nutrient);
