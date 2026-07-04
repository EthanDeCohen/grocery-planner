"""Schema creation and store seeding."""
from grocery_planner.stores import STORES


def test_tables_created(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"stores", "deals", "prices", "profile", "formulas", "scraping_jobs"} <= names


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

    conn = db.connect(p)  # runs init_db -> _migrate
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert "dollar_price" in cols
    conn.close()
