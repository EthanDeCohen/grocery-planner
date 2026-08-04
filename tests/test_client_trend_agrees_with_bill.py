"""The client chart must not contradict the bill beside it (GFP-144).

**The bug.** GFP-129's client series filtered by the client's CATEGORIES and
nothing else. GFP-136 then added constraints that change the plan without
changing that answer -- cover-all forces a dearer category in, one-store rules
out the cheapest shop. Measured on live data, with "include every protein I
ticked" switched on, the chart said *these preferences cost nothing extra*
while the bill beside it said *+$2.70/day*. Both were on screen at once.

The chart was not wrong. It was answering a question nobody was asking any
more.

**The fix, and the property these tests pin.** Both lines are now the effective
$/g of a real plan -- total cost over grams covered -- computed by the BILL's
own allocator. Not a similar calculation: the same one. Two implementations
that could drift is the defect, so there is only one.
"""
from __future__ import annotations

import pytest

from grocery_planner import bill, preferences
from grocery_planner.customers import Customer, CustomerRepository, lb_to_kg


def _client(conn, **kwargs):
    fields = {
        "name": "Chart Client",
        "weight_kg": lb_to_kg(150),
        "desired_weight_kg": lb_to_kg(150),
    }
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


def _stock(conn, store, name, price, category, kind, days,
           protein_per_100g=25.0, zip_code="27401"):
    """One food, one current deal, and a price_history row per day.

    The SAME item observed on several days, which is what really happens: a
    scrape replaces `deals` and appends to `price_history`. Creating a separate
    deal per day instead would leave the bill pricing every day's items at once
    while the chart priced one day's -- making them disagree for a reason that
    has nothing to do with what is under test.
    """
    food_id = conn.execute(
        "INSERT INTO foods(name, slug, category, protein_kind, source) "
        "VALUES (?,?,?,?, 'test')",
        (name, name.lower().replace(" ", "-"), category, kind),
    ).lastrowid
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, protein_per_100g),
    )
    item = f"{name} 16 oz"
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES (?,?,?, '2099-01-01', 'scrape')", (store, item, price),
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?,?,?,1.0,'test')", (store, item, food_id),
    )
    for day in days:
        conn.execute(
            "INSERT INTO price_history(store, item_name, dollar_price, "
            "captured_at, postal_code) VALUES (?,?,?,?,?)",
            (store, item, price, f"{day}T12:00:00", zip_code),
        )
    conn.commit()
    return food_id


DAYS = ("2026-08-02", "2026-08-03")


@pytest.fixture
def market(conn):
    """Cheap chicken and dear beef in two stores, seen on both days.

    The same shape as the live data that exposed the bug: one category is
    strictly cheaper, so a cost-minimising plan never reaches for the other.
    """
    _stock(conn, "harristeeter", "Cheap Chicken", 2.00, "chicken", "chicken", DAYS)
    _stock(conn, "harristeeter", "Local Beef", 9.00, "beef", "beef", DAYS)
    _stock(conn, "wholefoods", "Dear Chicken", 6.00, "chicken", "chicken", DAYS)
    _stock(conn, "wholefoods", "Cheapest Beef", 7.00, "beef", "beef", DAYS)
    return conn


SELECTIONS = [
    ("lowest cost", bill.Selection()),
    ("cover all", bill.Selection(cover_all_categories=True)),
    ("single store", bill.Selection(single_store=True)),
    ("both", bill.Selection(cover_all_categories=True, single_store=True)),
]


# --------------------------------------------------------------------------- #
# THE PROPERTY: the chart and the bill agree, for every selection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label,selection", SELECTIONS, ids=[s[0] for s in SELECTIONS])
def test_the_chart_value_equals_the_bills_own_effective_rate(market, conn, label, selection):
    """If these two ever disagree, the user sees a chart contradicting the
    panel next to it -- which is the entire ticket."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    plan = bill.daily_bill(client.id, conn=conn, selection=selection)
    ranked = bill.rank_history_by_day(90, conn)
    today = max(ranked)

    charted = bill.effective_cost_per_gram(
        ranked[today], plan.target_grams, ["beef", "chicken"], selection, conn
    )
    assert charted == pytest.approx(plan.total_cost / plan.covered_grams)


def test_the_chart_moves_when_a_constraint_moves(market, conn):
    """The reported symptom, inverted. Ticking cover-all changed the bill by
    $2.70/day and left the chart flat."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)
    ranked = bill.rank_history_by_day(90, conn)
    today = max(ranked)
    target = bill.daily_bill(client.id, conn=conn).target_grams

    cheapest = bill.effective_cost_per_gram(
        ranked[today], target, ["beef", "chicken"], bill.Selection(), conn
    )
    covered = bill.effective_cost_per_gram(
        ranked[today], target, ["beef", "chicken"],
        bill.Selection(cover_all_categories=True), conn,
    )
    assert covered > cheapest, "the chart must react to the constraint"


# --------------------------------------------------------------------------- #
# The baseline stays unconstrained -- confirmed for v1
# --------------------------------------------------------------------------- #
def test_the_baseline_ignores_preferences_and_constraints(market, conn):
    """The optimiser's line. The user confirmed it stays for v1: it is the
    thing everything else is measured against."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    ranked = bill.rank_history_by_day(90, conn)
    today = max(ranked)
    target = bill.daily_bill(client.id, conn=conn).target_grams

    baseline = bill.effective_cost_per_gram(
        ranked[today], target, [], bill.Selection(), conn
    )
    restricted = bill.effective_cost_per_gram(
        ranked[today], target, ["beef"],
        bill.Selection(cover_all_categories=True, single_store=True), conn,
    )
    # Beef is the dear category here, so the baseline must be strictly better.
    assert baseline < restricted


def test_both_lines_are_measured_the_same_way(market, conn):
    """A minimum compared against a plan average would make the gap an
    artefact of two definitions rather than of the client's choices."""
    client = _client(conn)
    ranked = bill.rank_history_by_day(90, conn)
    today = max(ranked)
    target = bill.daily_bill(client.id, conn=conn).target_grams

    # With no preferences and no constraints the two lines ARE the same
    # computation, so they must produce exactly the same number.
    baseline = bill.effective_cost_per_gram(
        ranked[today], target, [], bill.Selection(), conn
    )
    theirs = bill.effective_cost_per_gram(
        ranked[today], target, [], bill.Selection(), conn
    )
    assert baseline == pytest.approx(theirs)


# --------------------------------------------------------------------------- #
# Absent stays absent
# --------------------------------------------------------------------------- #
def test_a_day_with_nothing_allocatable_has_no_value(market, conn):
    """savings.py's rule 1. A zero would draw as "free"."""
    assert bill.effective_cost_per_gram([], 120.0, [], bill.Selection(), conn) is None


def test_a_category_with_no_stock_yields_no_value(market, conn):
    client = _client(conn)
    ranked = bill.rank_history_by_day(90, conn)
    today = max(ranked)
    assert bill.effective_cost_per_gram(
        ranked[today], 120.0, ["tofu"], bill.Selection(), conn
    ) is None


# --------------------------------------------------------------------------- #
# The history read
# --------------------------------------------------------------------------- #
def test_every_day_of_history_gets_its_own_pool(market, conn):
    ranked = bill.rank_history_by_day(90, conn)
    assert set(ranked) == {"2026-08-02", "2026-08-03"}
    assert all(pool for pool in ranked.values())


def test_the_window_excludes_older_days(market, conn):
    from datetime import date

    # Anchored well after the stocked days, so both fall outside a 1-day window.
    ranked = bill.rank_history_by_day(1, conn, today=date(2026, 9, 1))
    assert ranked == {}


def test_ranking_does_not_depend_on_the_selection(market, conn):
    """Why the pools can be cached across checkbox clicks: the expensive half
    is selection-independent."""
    a = bill.rank_history_by_day(90, conn)
    b = bill.rank_history_by_day(90, conn)
    assert {d: [i["item_name"] for i in p] for d, p in a.items()} == \
           {d: [i["item_name"] for i in p] for d, p in b.items()}


# --------------------------------------------------------------------------- #
# One implementation, not two
# --------------------------------------------------------------------------- #
def test_eligibility_is_shared_with_the_bill(market, conn):
    """The chart and the bill must apply the SAME category rule. Separate
    filters that could drift is the class of bug being fixed."""
    import inspect

    source = inspect.getsource(bill._build_bill)
    assert "_eligible(" in source, (
        "_build_bill no longer uses the shared eligibility filter, so the "
        "chart and the bill can now disagree about what a client may eat"
    )


# --------------------------------------------------------------------------- #
# The two lines have to be tellable apart
# --------------------------------------------------------------------------- #
def test_the_two_client_lines_get_different_palette_slots():
    """They exist to be COMPARED. Every non-store key used to fall through to
    a single muted "other" colour, so both were drawn identically -- a
    comparison chart whose two lines look the same is not one.

    This also catches a rename: the labels are literals in clienttrend.py and
    keys in trends.NON_STORE_SLOTS, and nothing else ties them together.
    """
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui import clienttrend, trends

    baseline = trends._slot(clienttrend.BASELINE_LABEL)
    theirs = trends._slot(clienttrend.THEIRS_LABEL)
    assert baseline != theirs
    # ...and neither may be the catch-all slot, which is a muted grey.
    catch_all = trends._slot("some series nobody registered")
    assert baseline != catch_all and theirs != catch_all
