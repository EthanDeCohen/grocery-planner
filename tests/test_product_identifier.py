"""GFP-111: each source's own product identifier, as a real NAMESPACED column.

Every source already hands us a stable per-product id, but two of the three
kept it inside the free-text ``notes`` blob (``product_id=0020895500000`` for
the Kroger/Harris Teeter API, ``asin=B09439SKW3`` for Whole Foods) while Flipp
had real columns. Anything wanting a SKU -- the grocery list's SKU column, the
v2 online-order file -- would have had to string-parse ``notes`` for two
sources out of three.

What these tests pin, in order:

1. The pair is a PAIR. ``product_identifier`` alone is not self-describing: a
   Kroger productId, an ASIN and a Flipp item id are different kinds of thing
   that happen to be strings, and '12345' in one vocabulary has nothing to do
   with '12345' in another. Value and namespace are written together, cleared
   together, and no row is ever left holding one without the other.
2. Absent stays absent (``savings.py`` rule 1). A source that states no id
   yields NULL/NULL -- never '', never a stand-in derived from the item name
   or a row id, which would look exactly like a real SKU to the ordering API
   it would eventually be handed to.
3. The scrapers write it, from the field they already hold. No part of the
   read path parses ``notes``.
4. The ONE-TIME backfill in db_script/migration/0016_GFP-111.ddl recovers what
   history already has -- and refuses to guess where it cannot know.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from grocery_planner import db, importers, service
from grocery_planner.scrapers import base, kroger, wholefoods
from grocery_planner.service import ingest

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)

COLUMNS = ("product_identifier", "product_identifier_ns")

# The four vocabularies in play. Named after the SOURCE that issues the id,
# never after a store (GFP-32): `kroger.*` ids are what the Kroger API returns
# for Harris Teeter's shelves, and ONE Flipp namespace covers Food Lion,
# Harris Teeter and any store added to the registry later.
KROGER_NS = "kroger.product_id"
WHOLEFOODS_NS = "wholefoods.asin"
FLIPP_ITEM_NS = "flipp.item_id"
FLIPP_COUPON_NS = "flipp.coupon_id"


# --------------------------------------------------------------------------- #
# The shared helper: both or neither, verbatim, never invented.
# --------------------------------------------------------------------------- #
def test_helper_returns_the_pair_together():
    assert base.product_identifier("0020895500000", KROGER_NS) == (
        "0020895500000", KROGER_NS)


def test_helper_keeps_the_value_verbatim():
    """Kroger zero-pads its productIds and an ASIN is alphanumeric, so the
    value is stored as the source states it. Read as a number, '0020895500000'
    becomes 20895500000 and no longer matches anything Kroger will accept."""
    assert base.product_identifier("0020895500000", KROGER_NS)[0] == "0020895500000"
    assert base.product_identifier("B09439SKW3", WHOLEFOODS_NS)[0] == "B09439SKW3"


def test_helper_stringifies_a_numeric_id_without_losing_it():
    """Flipp states its item ids as integers; the column holds one kind of
    thing (TEXT) so that every namespace's values are comparable in shape."""
    assert base.product_identifier(1018281137, FLIPP_ITEM_NS) == (
        "1018281137", FLIPP_ITEM_NS)


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_helper_never_invents_an_identifier(missing):
    """No id means NULL/NULL -- not a namespace labelling nothing, and not a
    stand-in. A fabricated SKU handed to an ordering API is far worse than an
    absent one (savings.py rule 1)."""
    assert base.product_identifier(missing, KROGER_NS) == (None, None)


def test_namespaces_are_all_distinct():
    """The whole point of the second column: these must never collapse into
    one another, or a Flipp item id could be compared against a Kroger
    productId as though they described the same product."""
    namespaces = {
        kroger.PRODUCT_IDENTIFIER_NS,
        wholefoods.PRODUCT_IDENTIFIER_NS,
        base.PRODUCT_IDENTIFIER_NS_ITEM,
        base.PRODUCT_IDENTIFIER_NS_COUPON,
    }
    assert len(namespaces) == 4


# --------------------------------------------------------------------------- #
# The scrapers write it at scrape time, from the field already in hand.
# --------------------------------------------------------------------------- #
def _kroger_product(product_id="0020895500000"):
    return {
        "productId": product_id,
        "description": "Boneless Chicken Breast Value Pack",
        "items": [{"size": "1 lb", "soldBy": "WEIGHT",
                   "price": {"regular": 4.99, "promo": 3.99}}],
    }


def test_kroger_row_carries_the_api_product_id():
    row, _ = kroger.product_to_row(_kroger_product(), "Meat", "27401", NOW)
    assert row["product_identifier"] == "0020895500000"
    assert row["product_identifier_ns"] == KROGER_NS


def test_kroger_row_without_a_product_id_stays_absent():
    row, _ = kroger.product_to_row(_kroger_product(product_id=""), "Meat", "27401", NOW)
    assert row["product_identifier"] is None
    assert row["product_identifier_ns"] is None


def _wholefoods_product(asin="B09439SKW3"):
    return {
        "asin": asin,
        "name": "Organic Boneless Skinless Chicken Breast",
        "offerDetails": {"price": {"priceAmount": 9.99},
                         "unitPrice": {"priceAmount": 9.99, "baseUnit": "pound"}},
    }


def test_wholefoods_row_carries_the_asin():
    row, _ = wholefoods.product_to_row(_wholefoods_product(), "chicken", "27401", NOW)
    assert row["product_identifier"] == "B09439SKW3"
    assert row["product_identifier_ns"] == WHOLEFOODS_NS


def test_wholefoods_row_without_an_asin_stays_absent():
    row, _ = wholefoods.product_to_row(_wholefoods_product(asin=""), "chicken", "27401", NOW)
    assert row["product_identifier"] is None
    assert row["product_identifier_ns"] is None


def test_flipp_weekly_ad_row_carries_the_item_id():
    item = {"id": 1018281137, "name": "Boneless Chicken Breast", "price": "1.99"}
    flyer = {"id": 7971624, "valid_from": "2026-06-10T00:00:00",
             "valid_to": "2026-06-16T00:00:00"}
    row = base.flyer_item_to_row(item, flyer, base.FOOD_LION)
    assert row["product_identifier"] == "1018281137"
    assert row["product_identifier_ns"] == FLIPP_ITEM_NS


def test_flipp_weekly_ad_row_without_an_item_id_stays_absent():
    item = {"name": "Mystery Ad Item", "price": "1.99"}
    flyer = {"id": 7971624, "valid_from": "2026-06-10T00:00:00",
             "valid_to": "2026-06-16T00:00:00"}
    row = base.flyer_item_to_row(item, flyer, base.FOOD_LION)
    assert row["product_identifier"] is None
    assert row["product_identifier_ns"] is None


def test_flipp_coupon_row_uses_its_own_namespace():
    """Flipp numbers its digital coupons independently of its flyer items, so
    the same digits can be both and mean different things. Deliberately NOT
    folded in with flipp.item_id."""
    coupon = {"coupon_id": "4475642", "brand": "Oscar Mayer",
              "coupon_type": "amountoff", "sale_story": "Save $2.00",
              "dollars_off": "2.00", "valid_from": "2026-06-10T00:00:00",
              "valid_to": "2026-06-16T00:00:00"}
    row = base.coupon_to_row(coupon, base.FOOD_LION)
    assert row["product_identifier"] == "4475642"
    assert row["product_identifier_ns"] == FLIPP_COUPON_NS


def test_one_flipp_namespace_serves_every_flipp_store():
    """Store-agnostic (GFP-32): the namespace describes the FEED the id came
    from, never the shop the flyer happens to be for. Adding a store must not
    add a namespace."""
    item = {"id": 42, "name": "Chicken", "price": "1.99"}
    flyer = {"id": 1, "valid_from": "2026-06-10T00:00:00",
             "valid_to": "2026-06-16T00:00:00"}
    food_lion = base.flyer_item_to_row(item, flyer, base.FOOD_LION)
    harris_teeter = base.flyer_item_to_row(item, flyer, base.HARRIS_TEETER)
    assert food_lion["product_identifier_ns"] == harris_teeter["product_identifier_ns"]


def test_notes_still_carries_the_identifier_too():
    """`notes` is left exactly as it was -- this adds a column, it does not
    replace a debugging aid (and nothing in the read path parses it)."""
    row, _ = kroger.product_to_row(_kroger_product(), "Meat", "27401", NOW)
    assert "product_id=0020895500000" in row["notes"]


# --------------------------------------------------------------------------- #
# Persistence: deals AND price_history, on the same scrape.
# --------------------------------------------------------------------------- #
class _FakeScraperModule:
    """Stands in for a grocery_planner.scrapers.<store> module, so run_scrape
    can be driven with no network (mirrors tests/test_ingest_guard.py)."""

    DEFAULT_POSTAL_CODE = "27401"

    def __init__(self, rows):
        self.rows = rows

    def scrape(self, postal_code=None, include_coupons=True):
        return list(self.rows), {"id": 1}, {"total": len(self.rows)}


def _scrape_row(item_name, identifier, namespace):
    return {
        "item_name": item_name, "sub_category": "Meat & Seafood",
        "deal_type": "Weekly Ad", "deal_description": "", "regular_price": None,
        "sale_price": 1.99, "dollar_price": 1.99, "discount_amount": None,
        "discount_percent": None, "valid_from": "2026-06-08",
        "valid_to": "2026-06-16", "loyalty_required": "Y", "notes": "",
        "product_identifier": identifier, "product_identifier_ns": namespace,
    }


def test_scrape_persists_the_pair_to_deals_and_history(conn, monkeypatch):
    """`deals` is a snapshot that run_scrape DELETEs and reinserts wholesale,
    so an identifier stored only there survives exactly one week. History is
    where "what did this product cost in March" lives, and answering that for
    an ordering API needs the id on the observation itself."""
    rows = [
        _scrape_row("Boneless Chicken Breast", "1018281137", FLIPP_ITEM_NS),
        _scrape_row("Mystery Ad Item", None, None),
    ]
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", _FakeScraperModule(rows))
    service.run_scrape("foodlion", conn=conn)

    for table in ("deals", "price_history"):
        stored = {
            r["item_name"]: (r["product_identifier"], r["product_identifier_ns"])
            for r in conn.execute(
                f"SELECT item_name, product_identifier, product_identifier_ns FROM {table}"
            )
        }
        assert stored["Boneless Chicken Breast"] == ("1018281137", FLIPP_ITEM_NS)
        # Absent stays absent all the way through the write path.
        assert stored["Mystery Ad Item"] == (None, None)


def test_rescraping_the_same_day_keeps_the_identifier_on_the_history_row(conn, monkeypatch):
    """The history upsert is keyed by calendar day, so a second scrape updates
    today's row rather than adding one. The namespace must not be dropped on
    the way through that UPDATE -- an id whose vocabulary has been lost cannot
    be looked up or compared safely."""
    rows = [_scrape_row("Boneless Chicken Breast", "1018281137", FLIPP_ITEM_NS)]
    monkeypatch.setitem(ingest.SCRAPERS, "foodlion", _FakeScraperModule(rows))
    service.run_scrape("foodlion", conn=conn)
    service.run_scrape("foodlion", conn=conn)

    history = conn.execute(
        "SELECT product_identifier, product_identifier_ns FROM price_history"
    ).fetchall()
    assert len(history) == 1
    assert history[0]["product_identifier"] == "1018281137"
    assert history[0]["product_identifier_ns"] == FLIPP_ITEM_NS


def test_csv_import_leaves_the_pair_null_not_blank(conn, sample_data):
    """A CSV-imported row (the legacy Excel export) states no source
    identifier, and '' is not absence -- it is an identifier that identifies
    nothing, and it would reach the SKU column looking like a real one."""
    importers.import_dir(conn, sample_data)
    rows = conn.execute(
        "SELECT product_identifier, product_identifier_ns FROM deals "
        "WHERE source=?", (importers.SOURCE_CSV,)
    ).fetchall()
    assert rows, "expected the sample CSVs to import some deals"
    assert all(r["product_identifier"] is None for r in rows)
    assert all(r["product_identifier_ns"] is None for r in rows)


# --------------------------------------------------------------------------- #
# The one-time backfill (db_script/migration/0016_GFP-111.ddl).
# --------------------------------------------------------------------------- #
MIGRATION = (Path(db.__file__).resolve().parent.parent
             / "db_script" / "migration" / "0016_GFP-111.ddl")


def _backfill_statements() -> list[str]:
    """The migration's backfill, read from the migration FILE itself.

    Deliberately not a copy of the SQL: a copy would keep passing after the
    real migration was changed. The ALTERs are skipped because the ``conn``
    fixture's database already has the columns (the migration ran on connect);
    everything else -- the parse, the namespacing, the invariant sweep -- is
    the shipped statement, executed verbatim.
    """
    statements = []
    for statement in db._split_statements(MIGRATION.read_text(encoding="utf-8")):
        body = "\n".join(
            line for line in statement.splitlines()
            if not line.strip().startswith("--")
        ).strip()
        if not body or body.upper().startswith("ALTER TABLE"):
            continue
        statements.append(body)
    return statements


def _seed(conn, rows):
    """Insert pre-GFP-111 `deals` rows: notes populated, the pair still NULL."""
    for row in rows:
        conn.execute(
            "INSERT INTO deals(store, source, postal_code, item_name, notes, "
            "flipp_item_id, flipp_coupon_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row.get("store", "harristeeter"), row.get("source", "scrape"),
             row.get("postal_code", "27401"), row["item_name"], row.get("notes"),
             row.get("flipp_item_id"), row.get("flipp_coupon_id")),
        )


def _run_backfill(conn):
    for statement in _backfill_statements():
        conn.execute(statement)


def _identifiers(conn):
    return {
        r["item_name"]: (r["product_identifier"], r["product_identifier_ns"])
        for r in conn.execute(
            "SELECT item_name, product_identifier, product_identifier_ns FROM deals")
    }


@pytest.fixture
def backfilled(conn):
    """A database seeded with one row per shape history actually contains,
    with the migration's backfill applied to it."""
    _seed(conn, [
        # Kroger / Harris Teeter API: id buried in notes.
        {"item_name": "Pork Loin Whole, 1 lb", "source": "kroger-api",
         "notes": "source=kroger_api; product_id=0020895500000; category=Meat; "
                  "postal_code=27401; size=1 lb; sold_by=WEIGHT"},
        # Whole Foods storefront: ASIN buried in notes.
        {"item_name": "Organic Chicken Breast", "store": "wholefoods",
         "notes": "source=wholefoods_storefront; asin=B09439SKW3; category=chicken; "
                  "postal_code=27401"},
        # Flipp weekly ad, scraped after GFP-15: real column, no parsing needed.
        {"item_name": "Boneless Chicken Breast", "flipp_item_id": 1018281137,
         "notes": "source=weekly_ad; flipp_flyer_id=7971624; "
                  "flipp_item_id=1018281137; brand=Tyson"},
        # Flipp digital coupon, scraped after GFP-15: real column.
        {"item_name": "Oscar Mayer", "flipp_coupon_id": 4475642,
         "notes": "source=digital_coupon; coupon_id=4475642; coupon_type=amountoff"},
        # The legacy Excel export: real columns NULL, but the id is sitting
        # verbatim in notes. Recovering it is not a guess.
        {"item_name": "Beech-Nut Baby Food", "source": "csv-import",
         "notes": "source=weekly_ad; engine=httpx; flipp_flyer_id=7971624; "
                  "flipp_item_id=1018281085; loyalty=MVP; brand=Beech-Nut"},
        {"item_name": "Legacy Coupon", "source": "csv-import",
         "notes": "source=digital_coupon; engine=httpx; coupon_id=4501112; "
                  "coupon_type=amountoff"},
        # Genuinely identifier-less: nothing to recover, nothing to invent.
        {"item_name": "Excel Row With No Id", "source": "csv-import",
         "notes": "brand=Store Brand"},
        # A weekly-ad row whose ad copy happens to contain 'product_id='. The
        # source marker is what decides, so this must NOT be read as a Kroger
        # productId.
        {"item_name": "Ad Copy Decoy", "source": "csv-import",
         "notes": "source=weekly_ad; brand=X; product_id=999999999"},
        # Present-but-empty: '' is not an identifier.
        {"item_name": "Blank Asin", "store": "wholefoods",
         "notes": "source=wholefoods_storefront; asin=; category=chicken"},
    ])
    _run_backfill(conn)
    return conn


def test_backfill_recovers_the_kroger_product_id_from_notes(backfilled):
    assert _identifiers(backfilled)["Pork Loin Whole, 1 lb"] == (
        "0020895500000", KROGER_NS)


def test_backfill_keeps_kroger_zero_padding(backfilled):
    """Stored as TEXT and verbatim. Read as a number it would become
    20895500000, which Kroger will not accept."""
    assert _identifiers(backfilled)["Pork Loin Whole, 1 lb"][0].startswith("00")


def test_backfill_recovers_the_asin_from_notes(backfilled):
    assert _identifiers(backfilled)["Organic Chicken Breast"] == (
        "B09439SKW3", WHOLEFOODS_NS)


def test_backfill_restates_the_flipp_columns_without_parsing(backfilled):
    """GFP-15 already promoted these; they are restated in the source-agnostic
    pair so a reader has ONE place to look, whatever produced the row."""
    identifiers = _identifiers(backfilled)
    assert identifiers["Boneless Chicken Breast"] == ("1018281137", FLIPP_ITEM_NS)
    assert identifiers["Oscar Mayer"] == ("4475642", FLIPP_COUPON_NS)


def test_backfill_leaves_the_flipp_columns_themselves_untouched(backfilled):
    row = backfilled.execute(
        "SELECT flipp_item_id, flipp_coupon_id FROM deals WHERE item_name=?",
        ("Boneless Chicken Breast",),
    ).fetchone()
    assert row["flipp_item_id"] == 1018281137
    assert row["flipp_coupon_id"] is None


def test_backfill_recovers_legacy_csv_rows_from_notes(backfilled):
    """The Excel export predates GFP-15's real columns, so its Flipp ids only
    ever existed in notes -- but they ARE there, and in the same vocabulary the
    scraped rows use. Leaving them NULL would lose a real identifier, not
    protect anyone from a fabricated one."""
    identifiers = _identifiers(backfilled)
    assert identifiers["Beech-Nut Baby Food"] == ("1018281085", FLIPP_ITEM_NS)
    assert identifiers["Legacy Coupon"] == ("4501112", FLIPP_COUPON_NS)


def test_backfill_invents_nothing_for_a_row_with_no_identifier(backfilled):
    assert _identifiers(backfilled)["Excel Row With No Id"] == (None, None)


def test_backfill_is_gated_on_the_source_marker_not_a_stray_substring(backfilled):
    """'product_id=' appearing in some other source's ad copy must never be
    read as a Kroger productId -- the gate is the `source=` key the scraper
    itself writes first."""
    assert _identifiers(backfilled)["Ad Copy Decoy"] == (None, None)


def test_backfill_treats_a_present_but_empty_value_as_absent(backfilled):
    assert _identifiers(backfilled)["Blank Asin"] == (None, None)


def test_backfill_never_leaves_half_a_pair(backfilled):
    """A value with no namespace is unusable and a namespace with no value
    says nothing, so the migration enforces both-or-neither in both
    directions."""
    broken = backfilled.execute(
        "SELECT COUNT(*) FROM deals "
        "WHERE (product_identifier IS NULL) != (product_identifier_ns IS NULL)"
    ).fetchone()[0]
    assert broken == 0


def test_backfill_leaves_notes_exactly_as_it_was(backfilled):
    """`notes` is a debugging aid; this promotes a value out of it, it does not
    strip it out from under anything that already reads it."""
    notes = backfilled.execute(
        "SELECT notes FROM deals WHERE item_name=?", ("Pork Loin Whole, 1 lb",)
    ).fetchone()["notes"]
    assert notes.startswith("source=kroger_api; product_id=0020895500000;")


def test_backfill_is_idempotent(backfilled):
    """It runs once per database via schema_version, but re-running it must
    not corrupt what it already wrote -- that is what makes a retried
    migration safe."""
    before = _identifiers(backfilled)
    _run_backfill(backfilled)
    assert _identifiers(backfilled) == before


# --------------------------------------------------------------------------- #
# price_history recovery: strictly one candidate, or nothing.
# --------------------------------------------------------------------------- #
def _history(conn, item_name, source="scrape"):
    conn.execute(
        "INSERT INTO price_history(store, postal_code, item_name, source, "
        "captured_at) VALUES ('harristeeter', '27401', ?, ?, '2026-06-10')",
        (item_name, source),
    )


def test_history_recovers_an_unambiguous_identifier(conn):
    _seed(conn, [{"item_name": "Boneless Chicken Breast", "flipp_item_id": 1018281137,
                  "notes": "source=weekly_ad; flipp_item_id=1018281137"}])
    _history(conn, "Boneless Chicken Breast")
    _run_backfill(conn)

    row = conn.execute(
        "SELECT product_identifier, product_identifier_ns FROM price_history"
    ).fetchone()
    assert row["product_identifier"] == "1018281137"
    assert row["product_identifier_ns"] == FLIPP_ITEM_NS


def test_history_refuses_to_pick_between_two_candidates(conn):
    """One display name, two different products -- repeated Flipp ad items
    carrying different item ids. Choosing either would be a guess, and a
    guessed SKU is precisely the failure mode these columns exist to prevent."""
    _seed(conn, [
        {"item_name": "Chicken Breast", "flipp_item_id": 111,
         "notes": "source=weekly_ad; flipp_item_id=111"},
        {"item_name": "Chicken Breast", "flipp_item_id": 222,
         "notes": "source=weekly_ad; flipp_item_id=222"},
    ])
    _history(conn, "Chicken Breast")
    _run_backfill(conn)

    row = conn.execute(
        "SELECT product_identifier, product_identifier_ns FROM price_history"
    ).fetchone()
    assert row["product_identifier"] is None
    assert row["product_identifier_ns"] is None


def test_history_for_a_product_no_longer_in_the_snapshot_stays_null(conn):
    """`deals` is replaced wholesale every scrape, so an old history row's
    product may simply no longer be there. Nothing to recover from, and
    nothing invented to fill the gap."""
    _history(conn, "Discontinued Item")
    _run_backfill(conn)

    row = conn.execute(
        "SELECT product_identifier, product_identifier_ns FROM price_history"
    ).fetchone()
    assert row["product_identifier"] is None
    assert row["product_identifier_ns"] is None


def test_history_does_not_borrow_an_identifier_across_sources(conn):
    """Two feeds share one store (the Flipp weekly ad and the Kroger shelf-price
    API, GFP-98). A history row from one must never take the other's id: the
    vocabularies are unrelated, so the match is keyed on source as well."""
    _seed(conn, [{"item_name": "Chicken Breast", "source": "kroger-api",
                  "notes": "source=kroger_api; product_id=0020895500000"}])
    _history(conn, "Chicken Breast", source="scrape")
    _run_backfill(conn)

    row = conn.execute(
        "SELECT product_identifier, product_identifier_ns FROM price_history"
    ).fetchone()
    assert row["product_identifier"] is None
    assert row["product_identifier_ns"] is None


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("table", ["deals", "price_history"])
def test_both_columns_exist_and_are_nullable(conn, table):
    """NULL is a legitimate answer here (a CSV-imported row has no source
    identifier), so neither column may be NOT NULL."""
    info = {r["name"]: r for r in conn.execute(f"PRAGMA table_info({table})")}
    for column in COLUMNS:
        assert column in info, f"{table}.{column} missing"
        assert info[column]["type"] == "TEXT"
        assert not info[column]["notnull"]


def test_deal_columns_carry_the_pair():
    """run_scrape builds its INSERT from importers.DEAL_COLUMNS, so the pair
    only reaches `deals` if it is named there."""
    for column in COLUMNS:
        assert column in importers.DEAL_COLUMNS
