"""GFP-50: every bill line carries the deal it came from, and its denomination.

GFP-48 and GFP-52 already gave a line its food, quantity, price, store and the
provenance of its protein figure. What was missing was the SOURCE DEAL ID —
without it a recommendation is a dead end: you can read it but cannot get back
to the record that produced it, which blocks both tracing a wrong number and
referencing the exact offer from a grocery list (GFP-112).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from grocery_planner import bill, service
from grocery_planner.customers import Customer
from grocery_planner.gui.wheretobuy import _denomination_note

FUTURE = (date.today() + timedelta(days=7)).isoformat()


def _client(conn, weight_kg=80.0) -> Customer:
    """A customer with a weight, because a bill needs a real protein target."""
    return Customer.create("Gfp50 Client", weight=weight_kg, weight_unit="kg")


def _priced_deal(conn, item="Chicken Breast 16 oz", store="harristeeter",
                 price=4.00, sold_by=None, uom=None) -> int:
    """A deal that resolves to a real $/g protein, so it can reach a bill."""
    row = conn.execute("SELECT id FROM foods WHERE slug = 'gfp50-chicken'").fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO foods(name, slug, category, source) "
            "VALUES ('Gfp50 chicken', 'gfp50-chicken', 'Meat', 'usda')")
        food_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
            "VALUES (?, 'protein', 25.0)", (food_id,))
    else:
        food_id = int(row["id"])
    conn.execute(
        "INSERT OR IGNORE INTO deal_food_match"
        "(store, item_name, food_id, confidence, method) VALUES (?, ?, ?, 0.9, 'test')",
        (store, item, food_id))
    cur = conn.execute(
        "INSERT INTO deals(store, item_name, deal_type, dollar_price, valid_to, "
        "source, postal_code, sold_by, price_per_unit_uom) "
        "VALUES (?, ?, 'Weekly Ad', ?, ?, 'test', '27401', ?, ?)",
        (store, item, price, FUTURE, sold_by, uom))
    conn.commit()
    return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# The outstanding field: source deal id
# --------------------------------------------------------------------------- #
def test_a_bill_line_names_the_deal_row_it_came_from(conn):
    deal_id = _priced_deal(conn)
    plan = bill.daily_bill_for(_client(conn), conn=conn)

    assert plan.lines, "expected the seeded deal to reach the bill"
    assert plan.lines[0].deal_id == deal_id


def test_the_deal_id_round_trips_back_to_the_deal(conn):
    """The whole point: a line must be traceable to its record."""
    _priced_deal(conn)
    line = bill.daily_bill_for(_client(conn), conn=conn).lines[0]

    row = conn.execute(
        "SELECT item_name, store FROM deals WHERE id = ?", (line.deal_id,)
    ).fetchone()
    assert row["item_name"] == line.item_name
    assert row["store"] == line.store


def test_fetch_deals_exposes_the_id_without_disturbing_the_csv_export(conn):
    """EXPORT_COLUMNS is an allow-list, so widening the select must not leak in."""
    _priced_deal(conn)
    rows = service.fetch_deals(conn=conn)
    assert "deal_id" in dict(rows[0])
    assert "deal_id" not in service.EXPORT_COLUMNS


# --------------------------------------------------------------------------- #
# Denomination (GFP-98's rule, inherited by this ticket)
# --------------------------------------------------------------------------- #
def test_a_weight_sold_line_carries_its_denomination(conn):
    _priced_deal(conn, sold_by="WEIGHT", uom="lb")
    line = bill.daily_bill_for(_client(conn), conn=conn).lines[0]

    assert line.sold_by == "WEIGHT"
    assert line.price_per_unit_uom == "lb"
    assert "sold by weight" in _denomination_note(line)
    assert "per lb" in _denomination_note(line)


def test_a_source_that_states_no_denomination_says_nothing(conn):
    """'Sold by weight' and 'we were not told' are different facts.

    Every Flipp and CSV row is the second, so a default of "per package" would
    be a guess — savings.py rule 1.
    """
    _priced_deal(conn)          # no sold_by, like every Flipp row
    line = bill.daily_bill_for(_client(conn), conn=conn).lines[0]

    assert line.sold_by is None
    assert _denomination_note(line) == ""


def test_a_unit_sold_line_is_not_tagged_either(conn):
    _priced_deal(conn, sold_by="UNIT")
    line = bill.daily_bill_for(_client(conn), conn=conn).lines[0]
    assert _denomination_note(line) == ""


# --------------------------------------------------------------------------- #
# What was already true, asserted so a refactor cannot quietly drop it
# --------------------------------------------------------------------------- #
def test_a_line_still_carries_store_and_protein_provenance(conn):
    _priced_deal(conn)
    line = bill.daily_bill_for(_client(conn), conn=conn).lines[0]

    assert line.store == "harristeeter"
    assert line.protein_source in {"usda", "curated", "label", "kroger"}
    assert 0.0 <= line.match_confidence <= 1.0
    assert line.food_name is not None


def test_a_basket_may_mix_stores(conn):
    """The ticket says so explicitly -- comparing stores is the point."""
    _priced_deal(conn, store="harristeeter", price=4.00)
    _priced_deal(conn, store="wholefoods", item="Chicken Thighs 16 oz", price=5.00)

    plan = bill.daily_bill_for(_client(conn), conn=conn)   # big target: needs both
    assert len({line.store for line in plan.lines}) == 2


def test_a_line_built_without_a_deal_id_is_none_not_a_crash(conn):
    """BillLine is constructed directly in tests and by future non-deal sources."""
    line = bill.BillLine(
        item_name="x", store="s", grams_protein=1.0, grams_food=None,
        cost=1.0, cost_per_gram_protein=1.0, protein_source="usda",
        match_confidence=1.0, food_id=None, food_name=None)
    assert line.deal_id is None and line.sold_by is None
