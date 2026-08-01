"""Schema creation and store seeding.

The schema itself is defined in db_script/ (GFP-59), not inline in db.py;
these tests also cover that the scripts on disk match the naming
convention documented in db_script/README.md, and that a fresh database
built purely from those scripts is complete.
"""
import re

import pytest

from grocery_planner.stores import STORES

# Every table/column the current baseline (db_script/init/0001_GFP-9.ddl)
# must produce in a brand-new database.
EXPECTED_TABLES = {"stores", "deals", "prices", "profile", "formulas",
                    "scraping_jobs", "schedules", "foods", "food_nutrients"}
EXPECTED_FOOD_COLUMNS = {
    "id", "name", "category", "source", "source_ref", "created_at", "updated_at",
}
EXPECTED_FOOD_NUTRIENT_COLUMNS = {
    "id", "food_id", "nutrient", "amount_per_100g", "unit",
}
# The v1 client UI offers exactly these six categories as checkboxes
# (GFP-23); the list itself is data-driven (grocery_planner.nutrition.
# list_categories), this constant just pins what the seed catalog covers.
CURATED_CATEGORIES = {"beef", "pork", "chicken", "fish", "tofu", "whey"}
EXPECTED_DEAL_COLUMNS = {
    "id", "store", "item_name", "sub_category", "deal_type",
    "deal_description", "regular_price", "sale_price", "dollar_price",
    "discount_amount", "discount_percent", "valid_from", "valid_to",
    "loyalty_required", "notes", "source", "imported_at",
}
EXPECTED_PRICE_COLUMNS = {
    "id", "store", "item_name", "brand", "category", "regular_price",
    "sale_price", "unit", "price_per_unit", "on_sale", "loyalty_required",
    "date_collected", "notes", "source", "imported_at",
}

SCRIPT_NAME_RE = re.compile(r"^\d{4}_GFP-\d+\.(ddl|dml)$")


def test_tables_created(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names


def test_stores_seeded(conn):
    rows = conn.execute("SELECT key FROM stores").fetchall()
    assert {r["key"] for r in rows} == {s.key for s in STORES}


def test_init_is_idempotent(conn):
    # connect() already ran init once; running again must not duplicate stores.
    from grocery_planner.db import init_db
    init_db(conn)
    count = conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
    assert count == len(STORES)


def test_deals_has_dollar_price_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "dollar_price" in cols


def test_migration_adds_dollar_price_to_old_db(tmp_path):
    # Simulate a DB created before the dollar_price column existed.
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, store TEXT, sale_price REAL)")
    raw.commit()
    raw.close()

    conn = db.connect(p)  # runs init_db -> applies db_script/migration/0002_GFP-6.ddl
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "dollar_price" in cols
    conn.close()


def test_fresh_db_built_from_scripts_has_every_expected_table_and_column(tmp_path):
    """A brand-new database, built purely from db_script/, is fully current --
    no reliance on any Python-inline schema."""
    from grocery_planner import db

    conn = db.connect(tmp_path / "fresh.sqlite3")
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert EXPECTED_TABLES <= tables

        deal_cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
        assert EXPECTED_DEAL_COLUMNS <= deal_cols

        price_cols = {r["name"] for r in conn.execute("PRAGMA table_info(prices)")}
        assert EXPECTED_PRICE_COLUMNS <= price_cols

        schedule_cols = {r["name"] for r in conn.execute("PRAGMA table_info(schedules)")}
        assert {"store", "kind", "expression", "enabled", "created_at",
                "updated_at"} <= schedule_cols

        food_cols = {r["name"] for r in conn.execute("PRAGMA table_info(foods)")}
        assert EXPECTED_FOOD_COLUMNS <= food_cols

        nutrient_cols = {r["name"] for r in
                          conn.execute("PRAGMA table_info(food_nutrients)")}
        assert EXPECTED_FOOD_NUTRIENT_COLUMNS <= nutrient_cols
    finally:
        conn.close()


def test_foods_and_food_nutrients_created(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"foods", "food_nutrients"} <= names


def test_curated_catalog_seeded_across_all_six_categories(conn):
    rows = conn.execute(
        "SELECT category FROM foods WHERE source='curated'"
    ).fetchall()
    assert 20 <= len(rows) <= 40
    assert {r["category"] for r in rows} == CURATED_CATEGORIES


def test_curated_foods_have_protein_values(conn):
    # Every curated food should carry a protein fact -- that's the whole
    # point of food_nutrients existing (cost-per-gram-of-protein downstream).
    missing = conn.execute(
        "SELECT f.name FROM foods f "
        "LEFT JOIN food_nutrients n ON n.food_id = f.id AND n.nutrient = 'protein' "
        "WHERE f.source = 'curated' AND n.id IS NULL"
    ).fetchall()
    assert missing == []

    proteins = conn.execute(
        "SELECT n.amount_per_100g FROM food_nutrients n "
        "JOIN foods f ON f.id = n.food_id "
        "WHERE f.source = 'curated' AND n.nutrient = 'protein'"
    ).fetchall()
    assert all(0 < r["amount_per_100g"] <= 100 for r in proteins)


def test_seed_catalog_is_idempotent_across_repeated_connects(tmp_path):
    # connect() re-applies every migration script (including the DML seed)
    # on every call; re-running must not duplicate curated rows.
    from grocery_planner import db

    p = tmp_path / "seed.sqlite3"
    conn1 = db.connect(p)
    before = conn1.execute(
        "SELECT COUNT(*) FROM foods WHERE source='curated'"
    ).fetchone()[0]
    conn1.close()

    conn2 = db.connect(p)  # second full init_db pass against the same file
    try:
        after = conn2.execute(
            "SELECT COUNT(*) FROM foods WHERE source='curated'"
        ).fetchone()[0]
        assert after == before

        nutrient_count = conn2.execute(
            "SELECT COUNT(*) FROM food_nutrients"
        ).fetchone()[0]
        assert nutrient_count == before  # one protein row per curated food
    finally:
        conn2.close()


def test_dollar_price_migration_is_idempotent_on_a_current_db(conn):
    """Re-applying db_script/migration/0002_GFP-6.ddl against a database that
    was built from the current init/ baseline (and so already has the
    column) must not raise."""
    from grocery_planner.db import _apply_sql_file, _sql_files

    migration_files = _sql_files("migration")
    assert migration_files, "expected at least the GFP-6 dollar_price migration"

    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "dollar_price" in cols_before  # already present via init/

    for f in migration_files:
        _apply_sql_file(conn, f)  # must not raise "duplicate column name"

    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert cols_after == cols_before


def test_migration_is_idempotent_across_repeated_connects(tmp_path):
    """connect() re-runs init + migration scripts every time; doing so twice
    in a row against the same file must not error or duplicate anything."""
    from grocery_planner import db

    p = tmp_path / "repeat.sqlite3"
    conn1 = db.connect(p)
    conn1.close()
    conn2 = db.connect(p)  # second full init_db pass against the same file
    try:
        cols = {r["name"] for r in conn2.execute("PRAGMA table_info(deals)")}
        assert "dollar_price" in cols
        count = conn2.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        assert count == len(STORES)
    finally:
        conn2.close()


@pytest.mark.parametrize("subdir", ["init", "migration"])
def test_script_filenames_match_the_naming_convention(subdir):
    """NNNN_GFP-KEY.{ddl,dml} -- required so the zero-padded prefix sorts
    scripts deterministically (Jira keys alone don't: GFP-9 > GFP-100 as
    text). See db_script/README.md."""
    from grocery_planner.db import _sql_files

    files = _sql_files(subdir)
    assert files, f"expected at least one script under db_script/{subdir}"
    for f in files:
        assert SCRIPT_NAME_RE.match(f.name), (
            f"{f.name} does not match NNNN_GFP-KEY.ddl|dml convention")


def test_script_sequence_prefixes_are_unique_across_init_and_migration():
    """The NNNN prefix is one counter shared across init/ and migration/ (per
    db_script/README.md), so duplicates across the two directories would
    defeat the point of a global apply order."""
    from grocery_planner.db import _sql_files

    prefixes = [f.name.split("_", 1)[0] for f in
                _sql_files("init") + _sql_files("migration")]
    assert len(prefixes) == len(set(prefixes))


# --------------------------------------------------------------------------- #
# GFP-54 — deals.postal_code
# --------------------------------------------------------------------------- #
def test_deals_has_postal_code_column(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "postal_code" in cols


def test_migration_backfills_postal_code_on_old_db(tmp_path):
    """A database from before GFP-54 has no postal_code column at all; every
    row it already has was scraped/imported back when the app only ever used
    one ZIP, so connect() must backfill those rows to that ZIP ("27401" --
    see db_script/migration/0003_GFP-54.ddl)."""
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(p)
    raw.execute(
        "CREATE TABLE deals (id INTEGER PRIMARY KEY, store TEXT, sale_price REAL, source TEXT)"
    )
    raw.execute(
        "INSERT INTO deals(store, sale_price, source) VALUES ('foodlion', 1.99, 'scrape')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(p)  # runs init_db -> applies db_script/migration/0003_GFP-54.ddl
    try:
        row = conn.execute(
            "SELECT postal_code FROM deals WHERE store='foodlion'"
        ).fetchone()
        assert row["postal_code"] == "27401"
    finally:
        conn.close()


def test_postal_code_migration_is_idempotent_on_a_current_db(conn):
    """Re-applying db_script/migration/0003_GFP-54.ddl against a database that
    already has the column (built from the current init/ baseline) must not
    raise."""
    from grocery_planner.db import _apply_sql_file, _sql_files

    migration_files = _sql_files("migration")
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "postal_code" in cols_before  # already present via init/

    for f in migration_files:
        _apply_sql_file(conn, f)  # must not raise "duplicate column name"

    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert cols_after == cols_before


# --------------------------------------------------------------------------- #
# GFP-39 — price_history
# --------------------------------------------------------------------------- #
def test_price_history_table_created(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "price_history" in names


def test_price_history_created_on_an_old_db_missing_it(tmp_path):
    """A pre-GFP-39 database has no price_history table at all; connect() must
    create it via db_script/migration/0004_GFP-39.ddl."""
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, store TEXT)")
    raw.commit()
    raw.close()

    conn = db.connect(p)
    try:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "price_history" in names
    finally:
        conn.close()
