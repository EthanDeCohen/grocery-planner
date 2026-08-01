-- GFP-30: per-client protein category preferences.
--
-- A nutritionist checks off which protein categories a client should be
-- eating (v1: beef, pork, chicken, fish, tofu, whey -- see foods.category,
-- grocery_planner/nutrition.list_categories()). GFP-49 later ranks deals
-- against that set; the recommendation engine itself (GFP-31/48/49) is out
-- of scope here.
--
-- One row per (customer, category) they prefer -- a customer can prefer
-- several categories, so this is a join table, not a column on customers.
-- category is a plain TEXT copy of foods.category, not an FK to foods.id:
-- foods.category is a free-text label shared by many foods rows (not a
-- unique/PK column), so nothing in the schema to FK against exists except
-- the label itself. That also keeps the checkbox list fully data-driven
-- (SELECT DISTINCT category FROM foods, see nutrition.list_categories()):
-- a seventh category becomes selectable the moment a food row uses it, with
-- no schema change and no FK target to add.
--
-- PRIMARY KEY(customer_id, category) instead of a surrogate id: the pair
-- *is* the natural key of a many-to-many relation, and it makes a repeat
-- INSERT of the same preference a plain constraint violation rather than a
-- silent duplicate row -- grocery_planner/preferences.py additionally
-- guards this with INSERT OR IGNORE so re-checking an already-selected box
-- is a harmless no-op, not an error.
--
-- ON DELETE CASCADE: a preference has no meaning once its customer is gone.
-- Note this is about the *row* -- customers.delete() is a soft delete
-- (deleted_at stamped, row kept), so an ordinary client deletion does NOT
-- cascade here; only a hard removal of the customers row would.
--
-- Zero rows for a customer is a deliberate, meaningful state: "no
-- preference recorded" means rank across every category (unconstrained),
-- not "prefers nothing / exclude everything". See grocery_planner/
-- preferences.py's module docstring for why that distinction matters
-- (GFP-49 uses the zero-row case as its unconstrained baseline). This table
-- also only ever expresses a soft preference, never a hard exclusion
-- (e.g. an allergy) -- see the same docstring.
CREATE TABLE IF NOT EXISTS customer_protein_preferences (
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (customer_id, category)
);

CREATE INDEX IF NOT EXISTS idx_customer_protein_preferences_customer_id
    ON customer_protein_preferences(customer_id);
