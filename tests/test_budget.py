"""The week, and the budget it is measured against (GFP-127, 128, 131).

The property under test throughout is a NEGATIVE one, and it is the reason
these tickets were rewritten before any code was written: **the budget never
enters the optimisation.** It is a line the plan is measured against, and the
plan is whatever minimising cost per gram of protein produced.

A budget-constrained solver can quietly under-deliver protein to make a number
fit. On a tool that computes what somebody eats that is the worst failure
available, so several tests here exist purely to prove it cannot happen.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from grocery_planner import budget, nutrition, preferences
from grocery_planner.customers import Customer, CustomerRepository, lb_to_kg


def _client(conn, budget_dollars=None, **kwargs):
    fields = {
        "name": "Test Client",
        "weight_kg": lb_to_kg(150),
        "desired_weight_kg": lb_to_kg(150),
        "weekly_budget": budget_dollars,
    }
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


def _deal(conn, name, price, category, protein_per_100g=25.0):
    """A priced, protein-resolvable deal in a known category.

    The SIZE lives in the item name, not a column -- savings.parse_size reads
    it from there, which is how every real scraped row works too.
    """
    food_id = conn.execute(
        "INSERT INTO foods(name, category, source, slug) VALUES (?,?,?,?)",
        (name, category, "test", name.lower().replace(" ", "-")),
    ).lastrowid
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g, unit) "
        "VALUES (?,?,?,?)", (food_id, "protein", protein_per_100g, "g"),
    )
    item = f"{name} 16 oz"
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES (?,?,?,?,?)",
        ("harristeeter", item, price, "2099-01-01", "scrape"),
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?,?,?,?,?)", ("harristeeter", item, food_id, 1.0, "test"),
    )
    conn.commit()


@pytest.fixture
def market(conn):
    """A small market with a cheap category and an expensive one."""
    _deal(conn, "Cheap Chicken", 2.00, "chicken")
    _deal(conn, "Pricey Beef", 12.00, "beef")
    return conn


# --------------------------------------------------------------------------- #
# The week
# --------------------------------------------------------------------------- #
def test_the_week_is_seven_days_of_the_daily_plan(market, conn):
    client = _client(conn)
    plan = budget.weekly_plan(client, conn=conn)
    assert plan.weekly_cost == pytest.approx(plan.daily.total_cost * 7)
    assert plan.weekly_target_grams == pytest.approx(plan.daily.target_grams * 7)


def test_seven_is_a_definition_not_a_setting(market, conn):
    """GFP-89's rule. A 'week' is not a threshold anyone should tune."""
    assert budget.DAYS_PER_WEEK == 7


def test_no_target_means_no_plan(conn):
    """Absent stays absent: a client with no weight has no target, so there is
    no week to price."""
    client = _client(conn, weight_kg=None, desired_weight_kg=None)
    assert budget.weekly_plan(client, conn=conn) is None


# --------------------------------------------------------------------------- #
# The budget is a LINE, not a constraint -- the point of these tickets
# --------------------------------------------------------------------------- #
def test_the_plan_is_identical_with_and_without_a_budget(market, conn):
    """THE CENTRAL TEST. If a budget changed what the optimiser chose, this
    would fail -- and a budget-constrained solve is exactly what was rejected.

    Same client, same market, wildly different budgets: the lines, the cost
    and the covered grams must be byte-identical.
    """
    generous = budget.weekly_plan(_client(conn, budget_dollars=10_000.0), conn=conn)
    stingy = budget.weekly_plan(_client(conn, budget_dollars=0.01), conn=conn)
    none_at_all = budget.weekly_plan(_client(conn, budget_dollars=None), conn=conn)

    assert generous.weekly_cost == stingy.weekly_cost == none_at_all.weekly_cost
    assert generous.daily.covered_grams == stingy.daily.covered_grams
    assert [line.item_name for line in generous.daily.lines] == \
           [line.item_name for line in stingy.daily.lines]


def test_an_impossible_budget_never_reduces_the_protein_target(market, conn):
    """Cost gives way, or a preference gives way. The nutrition never does."""
    client = _client(conn, budget_dollars=0.01)
    plan = budget.weekly_plan(client, conn=conn)
    unbudgeted = budget.weekly_plan(_client(conn), conn=conn)
    assert plan.daily.target_grams == unbudgeted.daily.target_grams


def test_no_budget_is_not_a_budget_of_zero(market, conn):
    """A client whose money has never been discussed is unmeasured, not
    permanently over."""
    plan = budget.weekly_plan(_client(conn, budget_dollars=None), conn=conn)
    assert plan.has_budget is False
    assert plan.is_over is False
    assert plan.over_by is None
    assert plan.headroom is None


def test_under_budget_reports_headroom(market, conn):
    client = _client(conn, budget_dollars=10_000.0)
    plan = budget.weekly_plan(client, conn=conn)
    assert not plan.is_over
    assert plan.headroom == pytest.approx(10_000.0 - plan.weekly_cost)


def test_over_budget_reports_by_how_much(market, conn):
    client = _client(conn, budget_dollars=0.50)
    plan = budget.weekly_plan(client, conn=conn)
    assert plan.is_over
    assert plan.over_by == pytest.approx(plan.weekly_cost - 0.50)
    assert plan.headroom == 0.0


def test_a_fraction_of_a_cent_over_is_not_over(market, conn):
    """Float sums land either side of exact. Reporting a client as over budget
    by $0.004 would be technically true and practically noise."""
    plan = budget.weekly_plan(_client(conn), conn=conn)
    exact = _client(conn, budget_dollars=plan.weekly_cost - 0.001)
    assert budget.weekly_plan(exact, conn=conn).is_over is False


# --------------------------------------------------------------------------- #
# GFP-131: the two options, priced
# --------------------------------------------------------------------------- #
def test_relaxing_a_preference_is_priced_not_merely_suggested(market, conn):
    """'Relax a preference' is useless as a sentence and valuable as a
    number."""
    client = _client(conn, budget_dollars=5.0)
    preferences.set_preferences(client.id, ["beef"], conn=conn)      # the dear one

    options = budget.relaxations(client, conn=conn)
    chicken = [r for r in options if r.category == "chicken"]
    assert chicken, "allowing chicken was not offered"
    assert chicken[0].saves > 0
    assert chicken[0].weekly_cost < budget.weekly_plan(
        client, categories=["beef"], conn=conn
    ).weekly_cost


def test_the_best_option_comes_first(market, conn):
    client = _client(conn, budget_dollars=5.0)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    options = budget.relaxations(client, conn=conn)
    assert options == sorted(options, key=lambda r: r.weekly_cost)


def test_an_option_says_whether_it_is_enough_on_its_own(market, conn):
    client = _client(conn, budget_dollars=100.0)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    best = budget.relaxations(client, conn=conn)[0]
    assert best.brings_under_budget is True


def test_a_client_with_no_preferences_has_nothing_to_relax(market, conn):
    """An unconstrained plan is already the cheapest the optimiser can do."""
    client = _client(conn, budget_dollars=1.0)
    assert preferences.list_preferences(client.id, conn=conn) == []
    assert budget.relaxations(client, conn=conn) == []


def test_relaxing_does_not_change_the_optimiser(market, conn):
    """Each option is the EXISTING solve with one category added back -- not a
    new kind of solve, and never the budget entering the calculation."""
    client = _client(conn, budget_dollars=5.0)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    option = [r for r in budget.relaxations(client, conn=conn) if r.category == "chicken"][0]

    direct = budget.weekly_plan(client, categories=["beef", "chicken"], conn=conn)
    assert option.weekly_cost == pytest.approx(direct.weekly_cost)


# --------------------------------------------------------------------------- #
# advise(): the closed set of outcomes
# --------------------------------------------------------------------------- #
def test_no_budget_means_no_advice(market, conn):
    assert budget.advise(_client(conn, budget_dollars=None), conn=conn) is None


def test_under_budget_gives_advice_with_no_options(market, conn):
    """One thing to render, rather than two code paths in the caller."""
    advice = budget.advise(_client(conn, budget_dollars=10_000.0), conn=conn)
    assert advice is not None
    assert advice.is_over is False
    assert advice.options == []
    assert advice.best is None


def test_an_unreachable_budget_says_so(market, conn):
    """When even allowing EVERYTHING is over budget, the preferences are not
    the problem. Suggesting 'allow pork' there would be actively misleading."""
    client = _client(conn, budget_dollars=0.01)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    advice = budget.advise(client, conn=conn)

    assert advice.is_over
    assert advice.unreachable is True


def test_a_reachable_budget_is_not_flagged_unreachable(market, conn):
    client = _client(conn, budget_dollars=25.0)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    advice = budget.advise(client, conn=conn)
    if advice.is_over:
        assert advice.unreachable is False
        assert advice.any_single_change_is_enough


def test_options_that_save_nothing_are_not_offered(market, conn):
    """A category containing nothing cheaper is not an option, it is noise."""
    client = _client(conn, budget_dollars=0.50)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)   # already cheapest
    advice = budget.advise(client, conn=conn)
    assert all(option.saves > 0 for option in advice.options)


def test_advice_never_proposes_a_smaller_target(market, conn):
    """GFP-131's invariant, asserted structurally: every option carries the
    SAME protein target as the plan it is an alternative to."""
    client = _client(conn, budget_dollars=5.0)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    advice = budget.advise(client, conn=conn)

    for option in advice.options:
        assert option.plan.daily.target_grams == pytest.approx(
            advice.plan.daily.target_grams
        )


def test_the_module_never_passes_a_budget_into_the_bill(market, conn):
    """Structural guard on the thing that was rejected three times.

    If someone later threads the budget into bill.daily_bill_for, this fails
    -- and that is exactly the change that would let the app silently
    under-deliver protein to hit a number.
    """
    import inspect
    source = inspect.getsource(budget)
    for call in ("daily_bill_for(", "weekly_plan("):
        for line in source.splitlines():
            if call in line and "budget=" in line:
                pytest.fail(f"a budget is being passed into the solve: {line.strip()}")


# --------------------------------------------------------------------------- #
# Sharing the pool across relaxations (GFP-335)
# --------------------------------------------------------------------------- #
def test_a_shared_pool_gives_the_same_prices_as_solving_each_alone(market, conn):
    """Equivalence, not speed.

    relaxations() now solves every category against one pre-ranked pool and one
    resolved-protein cache instead of re-ranking per category. That is only
    legitimate if the numbers are untouched -- a faster wrong answer is worse
    than a slow right one, and this is advice a nutritionist acts on.
    """
    from grocery_planner import preferences

    customer = _client(conn)
    preferences.set_preferences(customer.id, ["chicken"], conn=conn)
    pooled = budget.relaxations(customer, conn=conn)
    if not pooled:
        pytest.skip("no relaxations to price in this fixture")

    allowed = list(preferences.list_preferences(customer.id, conn=conn))
    for entry in pooled:
        alone = budget.weekly_plan(          # ranked=None, cache=None
            customer, categories=allowed + [entry.category], conn=conn
        )
        assert alone is not None
        assert alone.weekly_cost == pytest.approx(entry.weekly_cost)


def test_a_category_with_nothing_to_buy_is_not_priced(market, conn):
    """The filter half: a category with no purchasable deal cannot change a
    plan, so solving a whole week for it is pure cost. Removing it must not
    remove anything that could have appeared."""
    customer = _client(conn)
    priced = {r.category.strip().lower() for r in budget.relaxations(customer, conn=conn)}
    have_candidates = budget._categories_with_candidates(conn)
    assert priced <= have_candidates


def test_only_the_ten_protein_kinds_are_ever_offered(market, conn):
    """GFP-335. The list used to be SELECT DISTINCT category FROM foods -- 245
    retailer strings including "Bread Flour" and "Baby Food Purees" -- and each
    one cost a complete week-plan solve before a client page could be drawn.

    Asserted as a subset of the curated vocabulary rather than as a count: the
    number may legitimately change when a kind is added, but a raw retailer
    category appearing here is always the bug coming back.
    """
    from grocery_planner import preferences, protein_kind

    customer = _client(conn)
    preferences.set_preferences(customer.id, ["chicken"], conn=conn)
    offered = {r.category for r in budget.relaxations(customer, conn=conn)}
    assert offered <= set(protein_kind.KINDS)
    assert "chicken" not in offered, "a ticked preference is not a relaxation"
