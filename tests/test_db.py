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
                    "scraping_jobs", "schedules", "foods", "food_nutrients",
                    "customers"}
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
EXPECTED_CUSTOMER_COLUMNS = {
    "id", "name", "weight_kg", "weight_unit", "height_cm", "age", "sex",
    "activity_level", "goal", "protein_factor", "notes", "created_at",
    "updated_at", "deleted_at",
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

        customer_cols = {r["name"] for r in
                          conn.execute("PRAGMA table_info(customers)")}
        assert EXPECTED_CUSTOMER_COLUMNS <= customer_cols
    finally:
        conn.close()


def test_customers_table_created_with_default_protein_factor(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "customers" in names

    conn.execute("INSERT INTO customers(name) VALUES ('Jamie')")
    conn.commit()
    row = conn.execute(
        "SELECT protein_factor, deleted_at FROM customers WHERE name='Jamie'"
    ).fetchone()
    assert row["protein_factor"] == 1.6
    assert row["deleted_at"] is None


def test_customers_migration_adds_table_to_old_db(tmp_path):
    # Simulate a DB created before GFP-28's customers table existed.
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, store TEXT, sale_price REAL)")
    raw.commit()
    raw.close()

    conn = db.connect(p)  # runs init_db -> applies db_script/migration/0008_GFP-28.ddl
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "customers" in names
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(customers)")}
    assert EXPECTED_CUSTOMER_COLUMNS <= cols
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
    """Re-running the migration runner (GFP-60) against a database that was
    built from the current init/ baseline (and so already has the column
    via db_script/migration/0002_GFP-6.ddl too) must not raise.

    Pre-GFP-60 this asserted the same guarantee by calling the private
    `_apply_sql_file` helper directly against every migration file; GFP-60
    removed that helper's substring-matching error swallowing entirely in
    favor of schema_version-tracked apply-once semantics (see
    db_script/README.md), so this now exercises the same guarantee through
    the public db.init_db()/db.connect() surface instead.
    """
    from grocery_planner.db import init_db

    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "dollar_price" in cols_before  # already present via init/

    init_db(conn)  # must not raise -- 0002_GFP-6.ddl is already recorded

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
    """Re-running the migration runner (GFP-60) against a database that
    already has the column (built from the current init/ baseline, which
    also folds in db_script/migration/0003_GFP-54.ddl's change) must not
    raise.

    See test_dollar_price_migration_is_idempotent_on_a_current_db above for
    why this no longer calls the removed `_apply_sql_file` helper directly.
    """
    from grocery_planner.db import init_db

    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "postal_code" in cols_before  # already present via init/

    init_db(conn)  # must not raise -- 0003_GFP-54.ddl is already recorded

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


# --------------------------------------------------------------------------- #
# GFP-60 — proper migration runner / schema_version
# --------------------------------------------------------------------------- #
def test_fresh_db_records_every_script_in_schema_version_in_order(tmp_path):
    """A brand-new database must end up with schema_version fully populated:
    one row per db_script/ file (init/ then migration/, by seq), in order,
    each with a Jira key, a checksum, and an applied_at timestamp."""
    from grocery_planner import db
    from grocery_planner.db import _sql_files

    expected_names = [p.name for p in sorted(
        _sql_files("init") + _sql_files("migration"), key=lambda p: p.name)]

    conn = db.connect(tmp_path / "brand_new.sqlite3")
    try:
        rows = conn.execute(
            "SELECT seq, jira_key, filename, checksum, applied_at "
            "FROM schema_version ORDER BY seq"
        ).fetchall()
        assert [r["filename"] for r in rows] == expected_names
        assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
        assert all(r["jira_key"].startswith("GFP-") for r in rows)
        assert all(r["checksum"] for r in rows)
        assert all(r["applied_at"] for r in rows)
    finally:
        conn.close()


def test_reconnecting_does_not_re_apply_or_re_record_scripts(tmp_path):
    """Every connect() used to re-read and re-execute every script on disk
    (GFP-59). GFP-60 replaces that: a recorded script is never attempted
    again. Prove it by checking schema_version rows are byte-for-byte
    identical (including applied_at) across two connects -- if a script had
    been re-applied and re-recorded, applied_at (or the row) would differ."""
    from grocery_planner import db

    p = tmp_path / "stable.sqlite3"
    conn1 = db.connect(p)
    first = {
        r["seq"]: (r["filename"], r["checksum"], r["applied_at"])
        for r in conn1.execute(
            "SELECT seq, filename, checksum, applied_at FROM schema_version")
    }
    conn1.close()

    conn2 = db.connect(p)
    try:
        second = {
            r["seq"]: (r["filename"], r["checksum"], r["applied_at"])
            for r in conn2.execute(
                "SELECT seq, filename, checksum, applied_at FROM schema_version")
        }
        assert second == first
    finally:
        conn2.close()


def test_existing_database_without_schema_version_adopts_cleanly(tmp_path):
    """A real database created before GFP-60 already has every script's
    effect applied (dollar_price, postal_code, price_history,
    foods/food_nutrients, the curated seed data...) but no schema_version
    table at all -- this is the shape of the user's real database. connect()
    must not error, must not re-apply/duplicate anything, and must backfill
    schema_version to reflect reality so future connects are fully tracked."""
    from grocery_planner import db

    p = tmp_path / "legacy.sqlite3"
    conn = db.connect(p)  # build a normal, fully current database first
    foods_before = conn.execute(
        "SELECT COUNT(*) FROM foods WHERE source='curated'"
    ).fetchone()[0]
    conn.execute("DROP TABLE schema_version")  # simulate pre-GFP-60 history
    conn.commit()
    conn.close()

    conn2 = db.connect(p)  # adopt
    try:
        rows = conn2.execute(
            "SELECT seq FROM schema_version ORDER BY seq"
        ).fetchall()
        seqs = [r["seq"] for r in rows]
        assert seqs == list(range(1, len(seqs) + 1))  # every script backfilled

        foods_after = conn2.execute(
            "SELECT COUNT(*) FROM foods WHERE source='curated'"
        ).fetchone()[0]
        assert foods_after == foods_before  # curated seed not re-inserted

        cols = {r["name"] for r in conn2.execute("PRAGMA table_info(deals)")}
        assert {"dollar_price", "postal_code"} <= cols

        count = conn2.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        assert count == len(STORES)
    finally:
        conn2.close()


def test_checksum_mismatch_on_a_recorded_script_is_refused_loudly(tmp_path):
    """If a previously-applied script's recorded checksum no longer matches
    the file on disk (history was edited after being applied), refuse to
    continue rather than silently re-applying a changed script."""
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "tampered.sqlite3"
    conn = db.connect(p)
    conn.close()

    raw = sqlite3.connect(p)
    raw.execute("UPDATE schema_version SET checksum = 'deadbeef' WHERE seq = 1")
    raw.commit()
    raw.close()

    with pytest.raises(db.SchemaVersionError):
        db.connect(p)


def test_missing_file_for_a_recorded_script_is_refused_loudly(tmp_path):
    """If a script that schema_version says was applied no longer exists on
    disk at all, refuse to continue rather than silently ignoring it."""
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "missing_file.sqlite3"
    conn = db.connect(p)
    conn.close()

    raw = sqlite3.connect(p)
    raw.execute(
        "UPDATE schema_version SET filename = 'ghost.ddl' WHERE seq = 1"
    )
    raw.commit()
    raw.close()

    with pytest.raises(db.SchemaVersionError):
        db.connect(p)


def test_sequence_gap_in_db_script_is_refused_loudly(tmp_path, monkeypatch):
    """A gap in the NNNN sequence prefixes on disk (e.g. a file deleted or
    misnumbered) must fail loudly rather than silently skipping a script."""
    import sqlite3

    from grocery_planner import db

    root = tmp_path / "db_script"
    (root / "init").mkdir(parents=True)
    (root / "migration").mkdir(parents=True)
    (root / "init" / "0001_GFP-900.ddl").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    (root / "migration" / "0002_GFP-901.ddl").write_text(
        "ALTER TABLE t ADD COLUMN a TEXT;\n", encoding="utf-8")
    (root / "migration" / "0004_GFP-902.ddl").write_text(  # gap: 0003 missing
        "ALTER TABLE t ADD COLUMN b TEXT;\n", encoding="utf-8")
    monkeypatch.setattr(db, "_db_script_root", lambda: root)

    raw = sqlite3.connect(tmp_path / "gapdb.sqlite3")
    raw.row_factory = sqlite3.Row
    try:
        with pytest.raises(db.SchemaVersionError):
            db._apply_pending_migrations(raw)
    finally:
        raw.close()


def test_multi_statement_migration_applies_all_statements_together(
    tmp_path, monkeypatch
):
    """The GFP-59 mechanism executed scripts with executescript(), which
    aborts remaining statements once one raises -- so on a database that
    already had a migration's ALTER-added column, its accompanying backfill
    UPDATE silently never ran (0003_GFP-54.ddl's ALTER + UPDATE pair was
    safe only by luck: the UPDATE happened to be a no-op on repeat runs).
    GFP-60 applies each script inside a real transaction instead. Prove a
    two-statement migration (same ALTER + UPDATE shape as 0003_GFP-54.ddl)
    applies BOTH statements, not just the first."""
    import sqlite3

    from grocery_planner import db

    root = tmp_path / "db_script"
    (root / "init").mkdir(parents=True)
    (root / "migration").mkdir(parents=True)
    (root / "init" / "0001_GFP-900.ddl").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);\n"
        "INSERT INTO widgets (id, name) VALUES (1, 'a');\n",
        encoding="utf-8",
    )
    (root / "migration" / "0002_GFP-901.ddl").write_text(
        "-- mirrors 0003_GFP-54.ddl's shape: ALTER, then a backfill UPDATE.\n"
        "ALTER TABLE widgets ADD COLUMN flag TEXT;\n"
        "UPDATE widgets SET flag = 'backfilled' WHERE flag IS NULL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "_db_script_root", lambda: root)

    raw = sqlite3.connect(tmp_path / "widgets.sqlite3")
    raw.row_factory = sqlite3.Row
    try:
        db._apply_pending_migrations(raw)
        row = raw.execute("SELECT flag FROM widgets WHERE id = 1").fetchone()
        assert row["flag"] == "backfilled"  # both statements ran, not just ALTER

        recorded = {r["seq"] for r in
                    raw.execute("SELECT seq FROM schema_version")}
        assert recorded == {1, 2}
    finally:
        raw.close()


def test_failed_statement_in_a_script_rolls_back_the_whole_script(
    tmp_path, monkeypatch
):
    """A script is applied inside a transaction: if a later statement in the
    same script fails, an earlier statement's effect in that same script
    must not be left half-committed. (executescript(), used pre-GFP-60,
    gives no such guarantee -- each statement can auto-commit as it runs.)"""
    import sqlite3

    from grocery_planner import db

    root = tmp_path / "db_script"
    (root / "init").mkdir(parents=True)
    (root / "migration").mkdir(parents=True)
    (root / "init" / "0001_GFP-900.ddl").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    (root / "migration" / "0002_GFP-901.ddl").write_text(
        "ALTER TABLE widgets ADD COLUMN flag TEXT;\n"
        "UPDATE widgets SET nosuchcolumn = 1;\n",  # genuinely broken statement
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "_db_script_root", lambda: root)

    raw = sqlite3.connect(tmp_path / "rollback.sqlite3")
    raw.row_factory = sqlite3.Row
    try:
        # A genuinely broken migration must be loud, not swallowed.
        with pytest.raises(db.SchemaVersionError):
            db._apply_pending_migrations(raw)

        # The ALTER shared a transaction with the failing UPDATE, so it must
        # have rolled back with it rather than being left applied alone.
        cols = {r["name"] for r in raw.execute("PRAGMA table_info(widgets)")}
        assert "flag" not in cols

        # And it must NOT be recorded, or the bug would be permanently
        # marked done and never retried.
        applied = {
            r["seq"] for r in raw.execute("SELECT seq FROM schema_version")
        }
        assert 2 not in applied

        # Still broken on the next connect -- it surfaces again instead of
        # being silently skipped forever.
        with pytest.raises(db.SchemaVersionError):
            db._apply_pending_migrations(raw)

        # Once fixed, it applies normally.
        (root / "migration" / "0002_GFP-901.ddl").write_text(
            "ALTER TABLE widgets ADD COLUMN flag TEXT;\n", encoding="utf-8")
        db._apply_pending_migrations(raw)
        cols = {r["name"] for r in raw.execute("PRAGMA table_info(widgets)")}
        assert "flag" in cols
    finally:
        raw.close()


def test_a_database_predating_schema_version_is_baselined_not_replayed(tmp_path):
    """The real-world upgrade path: a database built before schema_version
    existed already has every script's effect. Replaying them would collide,
    so they must be recorded as baselined instead -- without touching data."""
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "legacy.sqlite3"
    db.connect(p).close()

    # Strip the tracking table to look like a pre-GFP-60 database.
    raw = sqlite3.connect(p)
    raw.execute("DROP TABLE schema_version")
    raw.commit()
    raw.close()

    conn = db.connect(p)  # must adopt rather than collide
    modes = {r["mode"] for r in conn.execute("SELECT mode FROM schema_version")}
    assert modes == {db.BASELINED}
    # Adoption is bookkeeping only -- seeded data must survive it.
    assert conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0] > 0
    conn.close()


def test_a_database_genuinely_behind_is_migrated_not_baselined(tmp_path):
    """The other half: a database that is actually missing changes must have
    them executed, not silently recorded as already-present."""
    import sqlite3

    from grocery_planner import db

    p = tmp_path / "behind.sqlite3"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE deals (id INTEGER PRIMARY KEY, store TEXT, sale_price REAL)")
    raw.commit()
    raw.close()

    conn = db.connect(p)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert {"dollar_price", "postal_code"} <= cols
    modes = {r["mode"] for r in conn.execute("SELECT mode FROM schema_version")}
    assert modes == {db.EXECUTED}
    conn.close()
