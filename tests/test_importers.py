"""CSV -> DB import: counts, numeric parsing, idempotency."""
from grocery_planner.importers import import_dir


def test_import_counts(conn, sample_data):
    results = {r.store: r for r in import_dir(conn, sample_data)}
    assert results["foodlion"].deals == 3
    assert results["foodlion"].prices == 1
    assert results["wholefoods"].deals == 1
    total_deals = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert total_deals == 4


def test_numeric_parsing(conn, sample_data):
    import_dir(conn, sample_data)
    row = conn.execute(
        "SELECT sale_price FROM deals WHERE item_name='Boneless Chicken Breast'").fetchone()
    assert row["sale_price"] == 1.99
    # missing price -> NULL, not 0
    none_row = conn.execute(
        "SELECT sale_price FROM deals WHERE item_name='Mystery Flyer Item'").fetchone()
    assert none_row["sale_price"] is None


def test_reimport_is_idempotent(conn, sample_data):
    import_dir(conn, sample_data)
    import_dir(conn, sample_data)  # second pass must replace, not append
    assert conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 4
