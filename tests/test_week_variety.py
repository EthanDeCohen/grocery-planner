"""Mix It Up: vary the week instead of repeating one item (GFP-142).

**The week had to become a real object first.** GFP-128's week was literally
``daily * 7``, so if drumsticks were cheapest today they were cheapest all week
and the plan was drumsticks seven days running -- there was nothing to vary.
``bill.week_plan`` allocates each of the seven days separately, which is what
makes the control meaningful rather than cosmetic.

**THE INVARIANT, and it is the reason the fallback exists:** no mode may
under-deliver the protein target to look cheaper or more varied. Variety gives
way, or cost gives way. The nutrition never does (GFP-131/GFP-136).
"""
from __future__ import annotations

import pytest

from grocery_planner import bill, preferences
from grocery_planner.customers import Customer, CustomerRepository, lb_to_kg


def _client(conn, **kwargs):
    fields = {
        "name": "Week Client",
        "weight_kg": lb_to_kg(150),
        "desired_weight_kg": lb_to_kg(150),
    }
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


def _deal(conn, store, name, price, category, kind, protein_per_100g=40.0):
    """A 16 oz package at 40 g protein/100 g = ~181 g protein.

    Deliberately MORE than one day's target. A line is capped at the package's
    own protein (bill.py rule 1), so at 25 g/100 g a single package covers only
    113 g of a 120 g target and every day needs two items -- which would make
    "the same item all week" untestable for a reason that has nothing to do
    with variety.
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
    conn.commit()
    return food_id


@pytest.fixture
def market(conn):
    """Several distinct proteins at rising prices, so variety is possible and
    is visibly not free."""
    for i, price in enumerate((2.00, 3.00, 4.00, 5.00, 6.00), start=1):
        _deal(conn, "harristeeter", f"Chicken Option {i}", price, "chicken", "chicken")
    return conn


@pytest.fixture
def scarce(conn):
    """Exactly ONE priceable protein. Variety is impossible here, which is the
    case the invariant is about."""
    _deal(conn, "harristeeter", "Only Chicken", 2.00, "chicken", "chicken")
    return conn


# --------------------------------------------------------------------------- #
# The default does not change today's output
# --------------------------------------------------------------------------- #
def test_repeat_cheapest_is_the_default(market, conn):
    """Turning variety on by default would silently change every existing
    client's plan on upgrade, with no action from them."""
    assert bill.Selection().vary_week is False


def test_repeat_cheapest_repeats(market, conn):
    """The behaviour GFP-142 was raised about, still available on request."""
    client = _client(conn)
    week = bill.week_plan(client.id, selection=bill.Selection(), conn=conn)
    assert week.distinct_items == 1
    assert week.repeated_days == 6          # every day matches the one before


def test_the_week_is_seven_days(market, conn):
    week = bill.week_plan(client_id := _client(conn).id, conn=conn)
    assert len(week.days) == 7
    assert client_id


# --------------------------------------------------------------------------- #
# Mix It Up actually varies
# --------------------------------------------------------------------------- #
def test_mix_it_up_stops_the_same_item_running_all_week(market, conn):
    client = _client(conn)
    week = bill.week_plan(
        client.id, selection=bill.Selection(vary_week=True), conn=conn
    )
    assert week.distinct_items > 1
    assert week.repeated_days == 0


def test_mix_it_up_does_not_repeat_within_the_lookback(market, conn):
    """Consecutive days must differ; a rotation returning after the lookback
    window is the intended behaviour, not a failure."""
    client = _client(conn)
    week = bill.week_plan(
        client.id, selection=bill.Selection(vary_week=True), conn=conn
    )
    sets = [frozenset(l.item_name for l in d.lines) for d in week.days]
    for earlier, later in zip(sets, sets[1:]):
        assert earlier != later


def test_variety_costs_more_and_that_is_shown_honestly(market, conn):
    """A trade the user is choosing. The figure must reflect it rather than
    being massaged."""
    client = _client(conn)
    cheap = bill.week_plan(client.id, selection=bill.Selection(), conn=conn)
    varied = bill.week_plan(
        client.id, selection=bill.Selection(vary_week=True), conn=conn
    )
    assert varied.total_cost > cheap.total_cost


# --------------------------------------------------------------------------- #
# THE INVARIANT
# --------------------------------------------------------------------------- #
def test_variety_never_costs_protein(market, conn):
    client = _client(conn)
    for selection in (bill.Selection(), bill.Selection(vary_week=True)):
        week = bill.week_plan(client.id, selection=selection, conn=conn)
        assert week.is_complete
        assert week.covered_grams == pytest.approx(week.target_grams)


def test_variety_gives_way_when_it_would_starve_the_client(scarce, conn):
    """THE FALLBACK, and the reason it exists. With one protein available,
    withholding it would leave the target short. Variety yields; the nutrition
    does not."""
    client = _client(conn)
    week = bill.week_plan(
        client.id, selection=bill.Selection(vary_week=True), conn=conn
    )
    assert week.is_complete
    assert week.covered_grams == pytest.approx(week.target_grams)
    assert week.distinct_items == 1          # it repeated, because it had to


def test_the_daily_target_is_untouched_by_the_mode(market, conn):
    """No mode may reduce what the client needs -- only how it is met."""
    client = _client(conn)
    plain = bill.week_plan(client.id, selection=bill.Selection(), conn=conn)
    varied = bill.week_plan(
        client.id, selection=bill.Selection(vary_week=True), conn=conn
    )
    assert plain.target_grams == pytest.approx(varied.target_grams)
    for day in varied.days:
        assert day.target_grams == pytest.approx(plain.days[0].target_grams)


# --------------------------------------------------------------------------- #
# It composes with the other constraints rather than replacing them
# --------------------------------------------------------------------------- #
def test_variety_composes_with_cover_all(market, conn):
    """One model -- objective plus composable constraints -- not two."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)
    week = bill.week_plan(
        client.id,
        selection=bill.Selection(vary_week=True, cover_all_categories=True),
        conn=conn,
    )
    assert week.is_complete


def test_variety_composes_with_single_store(market, conn):
    client = _client(conn)
    week = bill.week_plan(
        client.id,
        selection=bill.Selection(vary_week=True, single_store=True),
        conn=conn,
    )
    assert week.is_complete
    assert {l.store for d in week.days for l in d.lines} == {"harristeeter"}


def test_the_week_carries_the_selection_it_was_built_under(market, conn):
    client = _client(conn)
    selection = bill.Selection(vary_week=True)
    assert bill.week_plan(client.id, selection=selection, conn=conn).selection == selection


def test_a_client_with_no_target_has_no_week(market, conn):
    weightless = _client(conn, weight_kg=None, desired_weight_kg=None)
    assert bill.week_plan(weightless.id, conn=conn) is None


# --------------------------------------------------------------------------- #
# The control
# --------------------------------------------------------------------------- #
def test_the_panel_defaults_to_repeat_cheapest():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from grocery_planner.gui.selectionpanel import SelectionPanel

    QApplication.instance() or QApplication([])
    panel = SelectionPanel()
    assert panel.repeat_cheapest.isChecked()
    assert panel.selection().vary_week is False


def test_the_panel_expresses_mix_it_up_as_a_constraint():
    """Not a rival objective: "lowest cost" still decides what to reach for."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from grocery_planner.gui.selectionpanel import SelectionPanel

    QApplication.instance() or QApplication([])
    panel = SelectionPanel()
    panel.mix_it_up.setChecked(True)
    selection = panel.selection()
    assert selection.vary_week is True
    assert selection.objective is bill.Objective.LOWEST_COST


def test_the_two_week_options_are_mutually_exclusive():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from grocery_planner.gui.selectionpanel import SelectionPanel

    QApplication.instance() or QApplication([])
    panel = SelectionPanel()
    panel.mix_it_up.setChecked(True)
    assert not panel.repeat_cheapest.isChecked()
