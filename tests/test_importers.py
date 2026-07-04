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


def test_dollar_price_column_imported(conn, tmp_path):
    from grocery_planner.importers import DEAL_COLUMNS

    folder = tmp_path / "data" / "foodlion"
    folder.mkdir(parents=True)
    header = ",".join(DEAL_COLUMNS)
    row = "Chips,Snacks & Chips,Weekly Ad,$3.49,,3.49,3.49,,,2026-06-10,2026-06-16,Y,"
    (folder / "deals.csv").write_text(header + "\n" + row + "\n", encoding="utf-8")

    import_dir(conn, tmp_path / "data")
    got = conn.execute("SELECT dollar_price FROM deals WHERE item_name='Chips'").fetchone()
    assert got["dollar_price"] == 3.49
