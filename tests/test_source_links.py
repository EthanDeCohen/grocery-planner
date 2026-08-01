"""GFP-15: per-deal source link + ad-clipping image.

Covers the row builders in grocery_planner/scrapers/base.py (source_url,
image_url, and the promoted flipp_flyer_id/flipp_item_id/flipp_coupon_id
columns), the DEAL_COLUMNS wiring in grocery_planner/importers.py, and a
full round trip through the real `deals` schema (db_script/migration/
0012_GFP-15.ddl) via grocery_planner.service.run_scrape.

Deliberately does NOT touch tests/test_db.py, tests/test_service.py, or
tests/test_scraper.py -- see this ticket's PR description for the two
small, assertion-preserving edits that WERE required in test_db.py and
test_service.py (both pre-existing tests broke on `deals` gaining new
columns, unrelated to anything specific to source_url/image_url).
"""
from __future__ import annotations

from grocery_planner import db, importers
from grocery_planner.scrapers import base

STORE = base.FOOD_LION

FLYER = {
    "id": 8035490,
    "valid_from": "2026-07-29T00:00:00-04:00",
    "valid_to": "2026-08-04T23:59:59-04:00",
}


# --------------------------------------------------------------------------- #
# Row builders: source_url / image_url / flipp_* columns
# --------------------------------------------------------------------------- #
def test_flyer_item_to_row_builds_a_view_ad_link_with_item_and_flyer_ids():
    item = {
        "id": 1027283333, "name": "Power Water or Dasani", "brand": "Power Water",
        "price": "3.99", "cutout_image_url":
            "https://f.wishabi.net/page_items/427771650/1784351736/extra_large.jpg",
    }
    row = base.flyer_item_to_row(item, FLYER, STORE, postal_code="27401")

    # Verified by hand against the live flipp.com site as part of GFP-15 (see
    # the FLIPP_WEB_FLYER_URL comment in scrapers/base.py) -- this is a real,
    # working "View ad" page, not a guess.
    assert row["source_url"] == (
        "https://flipp.com/en-us/flyer/8035490?item_id=1027283333&postal_code=27401"
    )
    assert row["image_url"] == (
        "https://f.wishabi.net/page_items/427771650/1784351736/extra_large.jpg"
    )
    assert row["flipp_flyer_id"] == 8035490
    assert row["flipp_item_id"] == 1027283333
    assert row["flipp_coupon_id"] is None


def test_flyer_item_to_row_defaults_postal_code_to_the_store_default():
    item = {"id": 1, "name": "Chips", "price": "1.99"}
    row = base.flyer_item_to_row(item, FLYER, STORE)  # no postal_code passed
    assert f"postal_code={STORE.default_postal_code}" in row["source_url"]


def test_flyer_item_to_row_missing_cutout_image_degrades_to_none_not_a_broken_link():
    # GFP-15 honesty requirement: a missing image/link must be None (the GUI
    # renders plain text), never an empty string or a dead URL.
    item = {"id": 2, "name": "Mystery Feature", "price": None}
    row = base.flyer_item_to_row(item, FLYER, STORE)
    assert row["image_url"] is None


def test_flyer_item_to_row_missing_ids_yields_no_source_url():
    item = {"name": "No id at all", "price": "1.00"}  # no "id" key
    flyer_without_id = {"valid_from": FLYER["valid_from"], "valid_to": FLYER["valid_to"]}
    row = base.flyer_item_to_row(item, flyer_without_id, STORE)
    assert row["source_url"] is None


def test_coupon_to_row_builds_a_view_ad_link_from_the_coupon_id():
    coupon = {
        "coupon_id": 4702258, "brand": "Borden", "coupon_type": "amountoff",
        "sale_story": "Save $1.00", "promotion_text": "Save $1 on Borden String Cheese",
        "dollars_off": "1.00",
        "coupon_image_url": "https://f.wishabi.net/coupon_translations/4527195/1785298839/medium",
    }
    row = base.coupon_to_row(coupon, STORE, postal_code="27401")

    # Verified by hand: this exact (coupon_id, promotion_text) pair loads a
    # real Flipp coupon page whose title/content matches this fixture.
    assert row["source_url"] == "https://flipp.com/en-us/coupon/4702258?postal_code=27401"
    assert row["image_url"] == (
        "https://f.wishabi.net/coupon_translations/4527195/1785298839/medium"
    )
    assert row["flipp_coupon_id"] == 4702258
    assert row["flipp_flyer_id"] is None
    assert row["flipp_item_id"] is None


def test_coupon_to_row_missing_coupon_id_yields_no_source_url():
    coupon = {"brand": "Store Brand", "sale_story": "Save $1.00"}  # no coupon_id
    row = base.coupon_to_row(coupon, STORE)
    assert row["source_url"] is None


def test_notes_still_carries_the_flipp_identifiers_unchanged():
    """GFP-15 promotes flipp_flyer_id/flipp_item_id into real columns but
    deliberately leaves the existing `notes` free-text bag exactly as it
    was -- other code (and tests/test_scraper.py) already reads it, so this
    is additive, not a replacement."""
    item = {"id": 42, "name": "Whatever", "price": "1.00"}
    row = base.flyer_item_to_row(item, FLYER, STORE)
    assert "flipp_flyer_id=8035490" in row["notes"]
    assert "flipp_item_id=42" in row["notes"]


# --------------------------------------------------------------------------- #
# View-ad honesty: the label baked into the code comments, never "Buy now"
# --------------------------------------------------------------------------- #
def test_module_documents_view_ad_not_buy_now():
    """This is a documentation guardrail, not a UI test (GFP-38/GFP-52's GUI
    is out of scope) -- it fails loudly if someone strips the honesty-
    requirement comment out of scrapers/base.py without replacing it."""
    import inspect

    source = inspect.getsource(base)
    assert '"View ad"' in source
    assert "never a cart" in source or "not a storefront" in source


# --------------------------------------------------------------------------- #
# DEAL_COLUMNS wiring
# --------------------------------------------------------------------------- #
def test_deal_columns_includes_the_new_fields():
    for col in ("source_url", "image_url", "flipp_flyer_id", "flipp_item_id",
                "flipp_coupon_id"):
        assert col in importers.DEAL_COLUMNS


def test_flipp_ids_are_numeric_for_csv_import_purposes():
    for col in ("flipp_flyer_id", "flipp_item_id", "flipp_coupon_id"):
        assert col in importers.NUMERIC


# --------------------------------------------------------------------------- #
# Round trip through the real `deals` schema (0012_GFP-15.ddl)
# --------------------------------------------------------------------------- #
def test_deals_table_has_the_new_columns(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(deals)")}
    assert {"source_url", "image_url", "flipp_flyer_id", "flipp_item_id",
            "flipp_coupon_id"} <= cols


def test_scraped_row_persists_source_url_and_image_url(conn):
    """End-to-end: a row shaped exactly like scrape_store's output, inserted
    the same generic way grocery_planner/service/ingest.py's run_scrape does
    (DEAL_COLUMNS-driven), round-trips source_url/image_url/flipp_* through
    the real schema. Deliberately does not import service/ingest.py (out of
    scope per this ticket) -- it mirrors run_scrape's own INSERT shape so a
    schema/DEAL_COLUMNS mismatch is still caught here.
    """
    item = {
        "id": 555, "name": "Real Scrape Shape", "brand": "Acme", "price": "2.49",
        "cutout_image_url": "https://f.wishabi.net/page_items/1/1/extra_large.jpg",
    }
    row = base.flyer_item_to_row(item, FLYER, STORE, postal_code="27401")

    cols = importers.DEAL_COLUMNS
    conn.execute(
        f"INSERT INTO deals(store, postal_code, {', '.join(cols)}, source, imported_at) "
        f"VALUES (:store, :postal_code, {', '.join(':' + c for c in cols)}, "
        f":source, :imported_at)",
        {**row, "store": "foodlion", "postal_code": "27401", "source": "scrape",
         "imported_at": "2026-08-01T00:00:00+00:00"},
    )
    conn.commit()

    got = conn.execute(
        "SELECT source_url, image_url, flipp_flyer_id, flipp_item_id, flipp_coupon_id "
        "FROM deals WHERE item_name='Real Scrape Shape'"
    ).fetchone()
    assert got["source_url"] == row["source_url"]
    assert got["image_url"] == row["image_url"]
    assert got["flipp_flyer_id"] == 8035490
    assert got["flipp_item_id"] == 555
    assert got["flipp_coupon_id"] is None


def test_csv_import_leaves_new_columns_empty(conn, tmp_path):
    """A legacy csv-import row (no Flipp identifiers at all) must not error.
    Text columns follow the same "" convention every other optional text
    column in this import path already uses (e.g. notes); the promoted
    numeric identifier columns come out NULL, same as any other NUMERIC
    column here (see importers._to_float)."""
    folder = tmp_path / "data" / "foodlion"
    folder.mkdir(parents=True)
    header = ",".join(importers.DEAL_COLUMNS)
    row = "Chips,Snacks & Chips,Weekly Ad,$3.49,,3.49,3.49,,,2026-06-10,2026-06-16,Y,,,,,,"
    (folder / "deals.csv").write_text(header + "\n" + row + "\n", encoding="utf-8")

    from grocery_planner.importers import import_dir

    import_dir(conn, tmp_path / "data")
    got = conn.execute(
        "SELECT source_url, image_url, flipp_flyer_id FROM deals WHERE item_name='Chips'"
    ).fetchone()
    assert got["source_url"] == ""
    assert got["image_url"] == ""
    assert got["flipp_flyer_id"] is None
