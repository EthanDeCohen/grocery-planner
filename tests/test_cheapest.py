"""GFP-107: the cheapest animal protein on offer at each store, right now.

Distinct from the trends chart in a way worth protecting: this reads ``deals``
(what is on the shelf this week), not ``price_history``, and needs no history at
all. It is the one panel with something useful to show on the day the app is
installed.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from grocery_planner import db, service
from grocery_planner.cli import app

runner = CliRunner()

FUTURE = (date.today() + timedelta(days=7)).isoformat()
PAST = (date.today() - timedelta(days=7)).isoformat()


def _food(conn, name: str, slug: str, category: str, protein=25.0) -> int:
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) VALUES (?, ?, ?, 'usda')",
        (name, slug, category),
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, protein)
    )
    conn.commit()
    return food_id


def _deal(conn, store, item, food_id, price, *, valid_to=FUTURE,
          sold_by=None, uom=None, postal_code="27401") -> None:
    if food_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO deal_food_match"
            "(store, item_name, food_id, confidence, method) VALUES (?, ?, ?, 0.9, 'test')",
            (store, item, food_id),
        )
    conn.execute(
        "INSERT INTO deals(store, item_name, deal_type, dollar_price, valid_to, "
        "source, postal_code, sold_by, price_per_unit_uom) "
        "VALUES (?, ?, 'Weekly Ad', ?, ?, 'test', ?, ?, ?)",
        (store, item, price, valid_to, postal_code, sold_by, uom),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# The core answer
# --------------------------------------------------------------------------- #
def test_one_row_per_store_cheapest_first(conn):
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "wholefoods", "Chicken Breast 16 oz", chicken, 9.00)
    _deal(conn, "harristeeter", "Chicken Thighs 16 oz", chicken, 4.00)

    items = service.cheapest_protein_by_store(conn=conn)
    assert [i.store for i in items] == ["harristeeter", "wholefoods"]
    assert items[0].cost_per_gram_protein < items[1].cost_per_gram_protein


def test_only_the_cheapest_item_per_store_survives(conn):
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Breast 16 oz", chicken, 9.00)
    _deal(conn, "harristeeter", "Chicken Thighs 16 oz", chicken, 4.00)

    items = service.cheapest_protein_by_store(conn=conn)
    assert len(items) == 1
    assert items[0].item_name == "Chicken Thighs 16 oz"


def test_the_kind_comes_through_for_the_parenthetical(conn):
    """The whole point of the request: say WHICH meat it is."""
    beef = _food(conn, "Ground Beef", "gfp107-beef", "Meat")
    _deal(conn, "harristeeter", "Ground Beef Chuck 16 oz", beef, 5.00)

    assert service.cheapest_protein_by_store(conn=conn)[0].kind == "beef"


def test_non_meat_is_excluded_by_default_but_reachable(conn):
    """A pancake mix winning a MEAT ranking is the bug this whole thread began at."""
    mix = _food(conn, "Protein Pancake Mix", "gfp107-mix", "Plant Protein", protein=30.0)
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "wholefoods", "Protein Pancake Mix 16 oz", mix, 3.00)
    _deal(conn, "wholefoods", "Chicken Breast 16 oz", chicken, 6.00)

    assert "Chicken" in service.cheapest_protein_by_store(conn=conn)[0].item_name
    unfiltered = service.cheapest_protein_by_store(meat_only=False, conn=conn)
    assert "Pancake" in unfiltered[0].item_name


# --------------------------------------------------------------------------- #
# Honesty rules
# --------------------------------------------------------------------------- #
def test_an_expired_offer_is_excluded_outright(conn):
    """Sending someone to a shop for an offer that ended is worse than nothing."""
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Thighs 16 oz", chicken, 2.00, valid_to=PAST)
    _deal(conn, "harristeeter", "Chicken Breast 16 oz", chicken, 8.00)

    items = service.cheapest_protein_by_store(conn=conn)
    assert items[0].item_name == "Chicken Breast 16 oz"     # not the dead bargain


def test_an_offer_with_no_end_date_is_kept(conn):
    """Shelf prices carry no valid_to; treating that as expired would empty this."""
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Thighs 16 oz", chicken, 4.00, valid_to=None)
    assert len(service.cheapest_protein_by_store(conn=conn)) == 1


def test_a_store_with_nothing_resolvable_is_absent_not_blank(conn):
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Breast 16 oz", chicken, 4.00)
    _deal(conn, "wholefoods", "Mystery Item", None, 1.00)   # no size, no match

    assert [i.store for i in service.cheapest_protein_by_store(conn=conn)] \
        == ["harristeeter"]


def test_a_zero_price_is_not_an_offer(conn):
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Breast 16 oz", chicken, 0.0)
    assert service.cheapest_protein_by_store(conn=conn) == []


def test_an_empty_database_returns_nothing_rather_than_raising(conn):
    assert service.cheapest_protein_by_store(conn=conn) == []


# --------------------------------------------------------------------------- #
# GFP-98 denomination, and GFP-32 store-agnosticism
# --------------------------------------------------------------------------- #
def test_the_denomination_is_carried_so_the_ui_can_tag_a_per_pound_price(conn):
    """A WEIGHT item's price buys one POUND; rendering it bare invites a wrong buy."""
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Thighs 16 oz", chicken, 1.49,
          sold_by="WEIGHT", uom="lb")

    item = service.cheapest_protein_by_store(conn=conn)[0]
    assert item.sold_by == "WEIGHT"
    assert item.price_per_unit_uom == "lb"


def test_an_unregistered_store_still_gets_a_row(conn):
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "brand-new-store", "Chicken Breast 16 oz", chicken, 4.00)

    item = service.cheapest_protein_by_store(conn=conn)[0]
    assert item.store == "brand-new-store"
    assert item.label == "brand-new-store"      # labels as itself, no crash


def test_the_postal_code_filter_scopes_to_one_market(conn):
    chicken = _food(conn, "Chicken", "gfp107-chicken", "Meat")
    _deal(conn, "harristeeter", "Chicken Cheap 16 oz", chicken, 2.00, postal_code="10001")
    _deal(conn, "harristeeter", "Chicken Local 16 oz", chicken, 8.00, postal_code="27401")

    item = service.cheapest_protein_by_store(postal_code="27401", conn=conn)[0]
    assert item.item_name == "Chicken Local 16 oz"


# --------------------------------------------------------------------------- #
# The CLI, which must print what the strip shows
# --------------------------------------------------------------------------- #
def test_the_cli_prints_the_same_answer(env_db):
    live = db.connect()
    chicken = _food(live, "Chicken", "gfp107-chicken", "Meat")
    _deal(live, "harristeeter", "Chicken Thighs 16 oz", chicken, 4.00)

    result = runner.invoke(app, ["cheapest"])
    assert result.exit_code == 0, result.stdout
    assert "animal protein" in result.stdout
    assert "Chicken Thighs" in result.stdout
    assert "chicken" in result.stdout          # the kind column


def test_the_cli_says_so_plainly_when_there_is_nothing_to_rank(env_db):
    result = runner.invoke(app, ["cheapest"])
    assert result.exit_code == 0                # not an error, a normal early state
    assert "Nothing to rank yet" in result.stdout


def test_the_cli_can_include_non_meat(env_db):
    live = db.connect()
    mix = _food(live, "Protein Pancake Mix", "gfp107-mix", "Plant Protein", protein=30.0)
    _deal(live, "wholefoods", "Protein Pancake Mix 16 oz", mix, 3.00)

    assert "Nothing to rank yet" in runner.invoke(app, ["cheapest"]).stdout
    included = runner.invoke(app, ["cheapest", "--all-protein"])
    assert "Pancake" in included.stdout
    assert "all protein" in included.stdout
