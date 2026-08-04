"""The budget is visible when set, and the week is priced honestly (GFP-155).

Two defects, one root cause, found when the user asked where the budget input
was:

1. **The budget engine was built and never displayed.** ``budget.weekly_plan``,
   ``relaxations`` and ``advise`` shipped under GFP-127/128/131. The GUI
   imported exactly one thing from that module: the constant ``DAYS_PER_WEEK``.

2. **``budget.weekly_plan`` priced ``daily * 7``**, which stopped describing
   the plan once Mix It Up became the default (GFP-142). The flat week is
   $22.17 where the varied one is $50.42, so a budget verdict built on the
   multiplication could be **wrong by more than 2x** -- reporting a client
   comfortably under while the plan on their screen is far over.

The rule the user set: no budget, no problem; a budget, and it must be clear.
"""
from __future__ import annotations

import dataclasses

import pytest

from grocery_planner import bill, budget, preferences
from grocery_planner.customers import Customer, CustomerRepository, lb_to_kg


@pytest.fixture
def conn(env_db):
    """Redirects the DEFAULT connection, so a widget calling db.connect()
    internally reaches this database rather than the developer's real one."""
    from grocery_planner import db as db_module

    connection = db_module.connect()
    yield connection
    connection.close()


def _client(conn, **kwargs):
    fields = {
        "name": "Budget Client",
        "weight_kg": lb_to_kg(150),
        "desired_weight_kg": lb_to_kg(150),
    }
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


def _deal(conn, name, price, category, kind, protein_per_100g=40.0):
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
        "VALUES ('harristeeter',?,?, '2099-01-01', 'scrape')", (item, price),
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES ('harristeeter',?,?,1.0,'test')", (item, food_id),
    )
    conn.commit()


@pytest.fixture
def market(conn):
    """Rising prices, so varying the week is meaningfully dearer than
    repeating -- which is the gap the old multiplication hid."""
    for i, price in enumerate((1.00, 4.00, 7.00, 10.00), start=1):
        _deal(conn, f"Chicken {i}", price, "chicken", "chicken")
    return conn


# --------------------------------------------------------------------------- #
# THE BUG: a budget verdict from a multiplied day
# --------------------------------------------------------------------------- #
def test_the_week_is_priced_from_the_real_seven_days(market, conn):
    client = _client(conn, weekly_budget=30.0)
    varied = budget.weekly_plan(
        client, conn=conn, selection=bill.Selection(vary_week=True)
    )
    flat = budget.weekly_plan(
        client, conn=conn, selection=bill.Selection(vary_week=False)
    )
    assert varied.weekly_cost > flat.weekly_cost, (
        "a varied week costs more; if these match, the week is still being "
        "multiplied from one day"
    )


def test_the_varied_week_is_not_seven_times_the_day(market, conn):
    """THE HEART OF THE DEFECT. daily * 7 is exact only while every day is
    identical, which stopped being true the moment Mix It Up shipped."""
    client = _client(conn, weekly_budget=30.0)
    plan = budget.weekly_plan(
        client, conn=conn, selection=bill.Selection(vary_week=True)
    )
    multiplied = plan.daily.total_cost * budget.DAYS_PER_WEEK
    assert plan.weekly_cost != pytest.approx(multiplied)


def test_a_budget_verdict_can_flip_between_the_two_modes(market, conn):
    """The user-visible consequence: the same client and the same budget, told
    'under' by the multiplication and 'over' by the real week."""
    client = _client(conn, weekly_budget=None)
    flat = budget.weekly_plan(client, conn=conn,
                              selection=bill.Selection(vary_week=False))
    varied = budget.weekly_plan(client, conn=conn,
                                selection=bill.Selection(vary_week=True))
    between = (flat.weekly_cost + varied.weekly_cost) / 2

    client = _client(conn, weekly_budget=between)
    assert not budget.weekly_plan(
        client, conn=conn, selection=bill.Selection(vary_week=False)).is_over
    assert budget.weekly_plan(
        client, conn=conn, selection=bill.Selection(vary_week=True)).is_over


def test_a_plan_with_no_week_still_multiplies(market, conn):
    """Callers not given a week keep the old behaviour, so nothing silently
    changes meaning underneath them."""
    client = _client(conn)
    daily = bill.daily_bill_for(client, conn=conn)
    plan = budget.WeeklyPlan(daily=daily, budget=50.0)
    assert plan.weekly_cost == pytest.approx(daily.total_cost * budget.DAYS_PER_WEEK)


# --------------------------------------------------------------------------- #
# No budget, no problem
# --------------------------------------------------------------------------- #
def test_no_budget_means_no_verdict(market, conn):
    """A client with no budget is UNMEASURED, not permanently under."""
    client = _client(conn, weekly_budget=None)
    plan = budget.weekly_plan(client, conn=conn)
    assert plan.has_budget is False
    assert plan.over_by is None
    assert plan.is_over is False


def _qt():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _panel(customer_id, selection=None):
    """A BillPanel over the test database.

    ``_qt()`` runs BEFORE the BillPanel import, and that order is the point:
    CI installs ``.[dev]`` without the gui extra, so importing the panel first
    raises ModuleNotFoundError instead of skipping. Every GUI test in this file
    failed on CI for exactly that reason while passing locally, where PySide6
    is present.

    Visibility is asserted with isHidden(), NOT isVisible(): a widget whose
    window has never been shown reports isVisible() False whatever its own
    flag says, so every "the row is hidden" assertion would have passed
    vacuously -- and two of them did, until a "the row is shown" assertion
    failed and exposed it.
    """
    _qt()                       # skips FIRST -- see below
    from grocery_planner.gui.billpanel import BillPanel

    panel = BillPanel()
    panel.set_selection(None, selection or bill.Selection())
    panel.set_client(customer_id)
    return panel


def test_the_panel_hides_the_budget_row_when_none_is_set(market, conn):
    """A row reading "no budget" on every client who does not use the feature
    is clutter. The user's rule: no budget, no problem."""
    client = _client(conn, weekly_budget=None)
    panel = _panel(client.id)
    assert panel.budget_label.isHidden() is True


def test_the_panel_shows_the_budget_row_when_one_is_set(market, conn):
    client = _client(conn, weekly_budget=1000.0)
    panel = _panel(client.id)
    assert panel.budget_label.isHidden() is False
    assert "1,000" in panel.budget_label.text() or "1000" in panel.budget_label.text()


def test_being_over_budget_says_over_and_by_how_much(market, conn):
    client = _client(conn, weekly_budget=1.0)
    panel = _panel(client.id, bill.Selection(vary_week=True))
    text = panel.budget_label.text()
    assert "over" in text.lower()
    assert "$" in text


def test_being_under_budget_says_what_is_left(market, conn):
    client = _client(conn, weekly_budget=100_000.0)
    panel = _panel(client.id)
    assert "left" in panel.budget_label.text()


# --------------------------------------------------------------------------- #
# 1 day and 7 days, both on screen
# --------------------------------------------------------------------------- #
def test_the_headline_carries_both_the_day_and_the_week(market, conn):
    """The user asked for "7 day total vs 1 day", then that the result was too
    wordy. Both figures share the headline, which also sidesteps having to
    pick a single meaning for "the daily bill" under a varied week."""
    client = _client(conn)
    panel = _panel(client.id)
    assert "/day" in panel.headline.text()
    assert "/week" in panel.headline.text()


def test_a_varied_week_explains_itself_in_the_tooltip_not_a_sentence(market, conn):
    """A reader who multiplies and gets a different number will assume one of
    the figures is wrong -- so the reason stays available, but as a tooltip
    rather than a line of prose in a panel the user called wordy."""
    client = _client(conn)
    panel = _panel(client.id, bill.Selection(vary_week=True))
    assert "varied" in panel.headline.toolTip()
    assert "varied" not in panel.headline.text()


def test_a_flat_week_carries_no_variety_caveat(market, conn):
    """Seven times the day IS the week here, so there is nothing to explain."""
    client = _client(conn)
    panel = _panel(client.id, bill.Selection(vary_week=False))
    assert "/week" in panel.headline.text()
    assert "varied" not in panel.headline.toolTip()


def test_the_week_figure_matches_the_engine(market, conn):
    """Same object, not a similar calculation -- GFP-144's rule."""
    client = _client(conn)
    selection = bill.Selection(vary_week=True)
    panel = _panel(client.id, selection)
    expected = budget.weekly_plan(client, conn=conn, selection=selection).weekly_cost
    assert f"{expected:.2f}" in panel.headline.text()


def test_a_client_with_no_target_shows_no_week_or_budget(market, conn):
    """Never a $0.00 week, which reads as free."""
    weightless = _client(conn, weight_kg=None, desired_weight_kg=None,
                         weekly_budget=50.0)
    panel = _panel(weightless.id)
    assert panel.headline.text() == "—"
    assert panel.budget_label.isHidden() is True


def test_switching_to_a_client_without_a_budget_hides_the_row(market, conn):
    """The label persisted across clients before the empty paths cleared it."""
    with_budget = _client(conn, weekly_budget=100.0)
    without = _client(conn, weekly_budget=None)

    panel = _panel(with_budget.id)
    assert panel.budget_label.isHidden() is False
    panel.set_client(without.id)
    assert panel.budget_label.isHidden() is True


# --------------------------------------------------------------------------- #
# The two options that follow the verdict (GFP-156)
# --------------------------------------------------------------------------- #
def _budget_reachable_by_relaxing(conn, client, selection):
    """A budget the CURRENT preferences miss but a relaxation reaches.

    Computed rather than hardcoded: picking a number by hand produced a budget
    so low that nothing reached it, so the panel correctly reported
    "unreachable" and the test was asserting against the wrong branch.
    """
    constrained = budget.weekly_plan(client, conn=conn, selection=selection)
    unconstrained = budget.weekly_plan(
        client, categories=[], conn=conn, selection=selection
    )
    assert unconstrained.weekly_cost < constrained.weekly_cost, (
        "fixture gives relaxing nothing to do"
    )
    return (constrained.weekly_cost + unconstrained.weekly_cost) / 2


def test_over_budget_names_one_relaxation_and_the_alternative(market, conn):
    """GFP-131's framing, in the user's words: "keep to cost low or going
    above budget -- the only two options the nutritionist will have to make".

    So the panel names the BEST relaxation and states the alternative. Listing
    all eight would turn a decision into a table, on a panel the user has
    twice asked to be less wordy.
    """
    _deal(conn, "Cheap Pork", 0.50, "pork", "pork")
    selection = bill.Selection(vary_week=True)
    probe = _client(conn)
    preferences.set_preferences(probe.id, ["chicken"], conn=conn)
    cap = _budget_reachable_by_relaxing(conn, probe, selection)

    client = _client(conn, weekly_budget=cap)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)
    panel = _panel(client.id, selection)

    text = panel.options_label.text()
    assert panel.options_label.isHidden() is False
    assert "pork" in text
    assert "accept going over" in text


def test_the_other_options_stay_reachable_without_being_shown(market, conn):
    """Reachable, not deleted -- the nutritionist may want the second-best."""
    _deal(conn, "Cheap Pork", 0.50, "pork", "pork")
    _deal(conn, "Cheap Tofu", 0.60, "tofu", None)
    selection = bill.Selection(vary_week=True)
    probe = _client(conn)
    preferences.set_preferences(probe.id, ["chicken"], conn=conn)
    cap = _budget_reachable_by_relaxing(conn, probe, selection)

    client = _client(conn, weekly_budget=cap)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)
    panel = _panel(client.id, selection)
    assert panel.options_label.toolTip()


def test_under_budget_offers_nothing(market, conn):
    """There is no decision to make, so there is no line."""
    client = _client(conn, weekly_budget=100_000.0)
    panel = _panel(client.id)
    assert panel.options_label.isHidden() is True


def test_no_budget_offers_nothing(market, conn):
    client = _client(conn, weekly_budget=None)
    panel = _panel(client.id)
    assert panel.options_label.isHidden() is True


def test_an_unreachable_budget_says_so_rather_than_naming_a_preference(market, conn):
    """When allowing EVERYTHING is still over, the preferences are not the
    problem. Naming one to relax would be actively misleading."""
    client = _client(conn, weekly_budget=0.01)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)
    panel = _panel(client.id, bill.Selection(vary_week=True))
    text = panel.options_label.text()
    assert "No preference change" in text


# --------------------------------------------------------------------------- #
# The advice is priced under the SAME selection as the plan
# --------------------------------------------------------------------------- #
def test_advice_follows_the_selection(market, conn):
    """Advice priced under different constraints from the plan it advises
    about is the GFP-144 defect again -- two numbers that look comparable and
    are not."""
    _deal(conn, "Cheap Pork", 0.50, "pork", "pork")
    client = _client(conn, weekly_budget=1.0)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)

    flat = budget.advise(client, conn=conn, selection=bill.Selection(vary_week=False))
    varied = budget.advise(client, conn=conn, selection=bill.Selection(vary_week=True))
    assert flat.plan.weekly_cost != pytest.approx(varied.plan.weekly_cost)


def test_advice_never_offers_a_category_the_user_cannot_tick(market, conn):
    """GFP-139 removed the broad buckets ("Meat", "Seafood") from the
    preference checkboxes because they overlap the kinds beneath them.
    Advising "allow Meat" would name a control that does not exist and quote
    a saving nobody can act on."""
    from grocery_planner import nutrition

    _deal(conn, "Cheap Pork", 0.50, "pork", "pork")
    client = _client(conn, weekly_budget=1.0)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)

    options = budget.relaxations(client, conn=conn)
    buckets = set(nutrition.CATEGORY_MEMBERS)
    offered = {r.category.strip().lower() for r in options}
    assert not (offered & buckets), f"advice offers unofferable buckets: {offered & buckets}"


def test_relaxing_the_named_preference_really_reaches_the_budget(market, conn):
    """The claim is checkable, so check it: the option the panel names must
    actually produce the week it quotes."""
    _deal(conn, "Cheap Pork", 0.50, "pork", "pork")
    selection = bill.Selection(vary_week=True)
    probe = _client(conn)
    preferences.set_preferences(probe.id, ["chicken"], conn=conn)
    client = _client(conn, weekly_budget=_budget_reachable_by_relaxing(conn, probe, selection))
    preferences.set_preferences(client.id, ["chicken"], conn=conn)

    advice = budget.advise(client, conn=conn, selection=selection)
    best = advice.best
    assert best is not None

    actual = budget.weekly_plan(
        client, categories=["chicken", best.category], conn=conn, selection=selection
    )
    assert actual.weekly_cost == pytest.approx(best.weekly_cost)
