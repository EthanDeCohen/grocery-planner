"""Selection types: how to choose, as distinct from what is eligible (GFP-136).

Reported: "it forces the selection of one type of meat even when beef and
chicken are selected."

That was not a bug. ``_build_bill`` fills the target greedily from the cheapest
$/g, so with beef and chicken both ticked, chicken is cheaper and chicken fills
the whole target. Correct for *minimise cost*, and wrong for what ticking two
boxes means -- "I want both", not "consider both and pick one".

**The design distinction these tests pin down:** lowest-price is an OBJECTIVE
(what to optimise) and include-all is a CONSTRAINT (what the answer must
contain). They compose. "Include all, at the lowest price" is a sentence, and
is almost certainly what was meant.
"""
from __future__ import annotations

import pytest

from grocery_planner import bill, preferences
from grocery_planner.customers import Customer, CustomerRepository, lb_to_kg


def _client(conn, **kwargs):
    fields = {
        "name": "Test Client",
        "weight_kg": lb_to_kg(150),
        "desired_weight_kg": lb_to_kg(150),
    }
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


def _deal(conn, store, name, price, category, kind=None, protein_per_100g=25.0):
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
    """Cheap chicken and dear beef, in two stores.

    Deliberately mirrors the real shape of the complaint: one category is
    strictly cheaper, so a cost-minimising plan never reaches for the other.
    """
    _deal(conn, "harristeeter", "Cheap Chicken", 2.00, "chicken", "chicken")
    _deal(conn, "harristeeter", "Local Beef", 9.00, "beef", "beef")
    _deal(conn, "wholefoods", "Dear Chicken", 6.00, "chicken", "chicken")
    _deal(conn, "wholefoods", "Cheapest Beef", 7.00, "beef", "beef")
    return conn


def _stores(plan) -> set[str]:
    return {line.store for line in plan.lines}


def _kinds(plan, conn) -> set[str]:
    out = set()
    for line in plan.lines:
        row = conn.execute(
            "SELECT protein_kind FROM foods WHERE id=?", (line.food_id,)
        ).fetchone()
        if row and row[0]:
            out.add(row[0])
    return out


# --------------------------------------------------------------------------- #
# The default is unchanged -- everything else is opt-in
# --------------------------------------------------------------------------- #
def test_the_default_is_todays_behaviour(market, conn):
    """Nothing about this ticket may change what an existing user sees until
    they choose something."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    default = bill.daily_bill(client.id, conn=conn)
    explicit = bill.daily_bill(
        client.id, conn=conn, selection=bill.Selection()
    )
    assert default.total_cost == pytest.approx(explicit.total_cost)
    assert [l.item_name for l in default.lines] == [l.item_name for l in explicit.lines]


def test_lowest_cost_still_fills_from_the_cheapest(market, conn):
    """The complaint, reproduced: chicken is cheaper, so chicken wins outright
    and beef never appears. This is CORRECT for this objective."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    plan = bill.daily_bill(client.id, conn=conn)
    assert _kinds(plan, conn) == {"chicken"}


# --------------------------------------------------------------------------- #
# cover_all_categories -- the actual complaint
# --------------------------------------------------------------------------- #
def test_cover_all_includes_every_ticked_category(market, conn):
    """Ticking beef AND chicken means "I want both"."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    assert _kinds(plan, conn) == {"beef", "chicken"}


def test_cover_all_costs_more_and_that_is_the_point(market, conn):
    """Including a dearer category is a trade the user is choosing to make.
    The figure must reflect it honestly rather than being massaged."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    cheapest = bill.daily_bill(client.id, conn=conn)
    covered = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    assert covered.total_cost > cheapest.total_cost


def test_cover_all_still_hits_the_target(market, conn):
    """A constraint on WHAT is included must not cost protein. GFP-131's
    invariant, restated for every mode."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    assert plan.covered_grams == pytest.approx(plan.target_grams)


def test_cover_all_with_one_category_changes_nothing(market, conn):
    """There is nothing to spread across."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["chicken"], conn=conn)

    plain = bill.daily_bill(client.id, conn=conn)
    covered = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    assert covered.total_cost == pytest.approx(plain.total_cost)


def test_a_category_with_nothing_priceable_does_not_break_it(market, conn):
    """"Best effort": a share nobody can fill rolls into the greedy pass
    rather than leaving the target short."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken", "tofu"], conn=conn)

    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    assert plan.covered_grams == pytest.approx(plan.target_grams)


def test_no_deal_is_bought_twice(market, conn):
    """cover-all runs several passes over overlapping pools, so the marker
    set is what stops one package being counted in two of them."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    names = [line.item_name for line in plan.lines]
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- #
# single_store
# --------------------------------------------------------------------------- #
def test_single_store_buys_from_one_shop(market, conn):
    """The optimiser will otherwise send somebody to three shops to save two
    dollars, which is a bad trade for a real person."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    spread = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True),
    )
    single = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True, single_store=True),
    )
    assert len(_stores(single)) == 1
    assert len(_stores(spread)) >= 1


def test_single_store_prefers_coverage_over_cheapness(market, conn):
    """A cheaper plan that misses the target is not a better answer to "what
    should this client eat"."""
    client = _client(conn)
    plan = bill.daily_bill(
        client.id, conn=conn, selection=bill.Selection(single_store=True)
    )
    assert plan.covered_grams == pytest.approx(plan.target_grams)


def test_single_store_composes_with_cover_all(market, conn):
    """Constraints compose -- that is the whole reason they are flags rather
    than rival modes."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef", "chicken"], conn=conn)

    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(cover_all_categories=True, single_store=True),
    )
    assert len(_stores(plan)) == 1
    assert _kinds(plan, conn) == {"beef", "chicken"}


# --------------------------------------------------------------------------- #
# The objective
# --------------------------------------------------------------------------- #
def test_a_budget_objective_stops_spending(market, conn):
    client = _client(conn)
    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(
            objective=bill.Objective.MOST_PROTEIN_WITHIN_BUDGET,
            daily_budget=0.50,
        ),
    )
    assert plan.total_cost <= 0.50 + 0.001


def test_a_budget_objective_reports_the_shortfall_rather_than_hiding_it(market, conn):
    """GFP-131'S INVARIANT, and the one mode that could violate it. Spending
    less must never mean quietly pretending the client needs less."""
    client = _client(conn)
    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(
            objective=bill.Objective.MOST_PROTEIN_WITHIN_BUDGET,
            daily_budget=0.50,
        ),
    )
    assert plan.target_grams == pytest.approx(
        bill.daily_bill(client.id, conn=conn).target_grams
    )
    assert not plan.is_complete
    assert plan.shortfall_grams > 0


def test_under_budget_the_two_objectives_agree(market, conn):
    """They only differ when the budget BINDS. The UI should say so rather
    than offer a control that usually changes nothing."""
    client = _client(conn)
    cheapest = bill.daily_bill(client.id, conn=conn)
    within = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(
            objective=bill.Objective.MOST_PROTEIN_WITHIN_BUDGET,
            daily_budget=cheapest.total_cost * 10,
        ),
    )
    assert within.total_cost == pytest.approx(cheapest.total_cost)
    assert within.covered_grams == pytest.approx(cheapest.covered_grams)


def test_a_budget_is_ignored_by_the_lowest_cost_objective(market, conn):
    """The budget belongs to ONE objective. Letting it leak into the other
    would make the optimiser budget-constrained by the back door, which is
    exactly what GFP-127 refused."""
    client = _client(conn)
    plan = bill.daily_bill(
        client.id, conn=conn,
        selection=bill.Selection(daily_budget=0.01),      # lowest-cost default
    )
    assert plan.covered_grams == pytest.approx(plan.target_grams)


# --------------------------------------------------------------------------- #
# The bill says how it was built
# --------------------------------------------------------------------------- #
def test_the_bill_carries_the_selection_it_was_built_under(market, conn):
    """So a caller can explain WHY the cheapest item is not in the plan,
    rather than leaving a user to wonder."""
    client = _client(conn)
    selection = bill.Selection(cover_all_categories=True, single_store=True)
    plan = bill.daily_bill(client.id, conn=conn, selection=selection)
    assert plan.selection == selection
