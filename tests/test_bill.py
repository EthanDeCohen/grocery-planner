"""Tests for grocery_planner.bill (GFP-48): target grams -> foods -> daily bill.

Mirrors tests/test_cost_per_gram.py's approach -- real db.connect() fixtures
(migrations + the curated 32-item catalog are already seeded by conftest's
`conn` fixture), explicit food/deal/match rows built by hand rather than
mocked, and at least one hand-computed arithmetic check so a passing test
proves the actual number, not just that bill.py agrees with itself.
"""
from __future__ import annotations

import pytest

from grocery_planner import bill, preferences, savings
from grocery_planner.customers import KG_PER_LB, Customer, CustomerRepository

GRAMS_PER_OZ = savings.GRAMS_PER_OZ


def _insert_food(conn, name, category, protein_per_100g, source="usda"):
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) VALUES (?, ?, ?, ?)",
        (name, f"test-{name.lower().replace(' ', '-')}", category, source),
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)",
        (food_id, protein_per_100g),
    )
    return food_id


def _insert_priced_deal(conn, store, item_name, price, food_id, valid_to="2099-01-01"):
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES (?, ?, ?, 0.9, 'test')",
        (store, item_name, food_id),
    )
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES (?, ?, ?, ?, 'scrape')",
        (store, item_name, price, valid_to),
    )


#: GFP-132 changed the target formula from grams-per-KILOGRAM of current
#: weight to grams-per-POUND of desired weight. These tests are about the
#: BILL, not the target, so the factor is converted by exactly the unit
#: factor -- which leaves every client in this file on precisely the same
#: daily target as before (50 kg still gives 80 g/day) and keeps the fixture
#: deals sized correctly against it.
#:
#: Deliberately below GFP-133's 0.8 floor: that band is enforced by the UI,
#: not the model, and pinning these tests to a real clinical value would
#: change every expected gram figure for reasons unrelated to billing.
_LEGACY_EQUIVALENT_FACTOR = 1.6 * KG_PER_LB          # 0.72575 g/lb


def _make_customer(conn, weight_kg, protein_factor=_LEGACY_EQUIVALENT_FACTOR, save=False):
    customer = Customer.create(
        "Test Client", weight_kg=weight_kg, weight_unit="kg", protein_factor=protein_factor
    )
    return CustomerRepository.save(customer, conn=conn) if save else customer


# --------------------------------------------------------------------------- #
# Known arithmetic, end to end
# --------------------------------------------------------------------------- #
def test_known_arithmetic_end_to_end(conn):
    food_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    conn.commit()

    # 50 kg = 110.23 lb, x 0.72575 g/lb -> 80 g/day target (unchanged by
    # GFP-132; see _LEGACY_EQUIVALENT_FACTOR).
    customer = _make_customer(conn, weight_kg=50.0)
    result = bill.daily_bill_for(customer, conn=conn)

    assert result is not None
    assert result.target_grams == pytest.approx(80.0)

    # Hand computation, independent of bill.py: 16 oz -> grams, 25 g protein
    # per 100 g -> grams of protein in the package; the 80 g target is well
    # under that package's own protein, so one line covers it in full.
    size_grams = 16 * GRAMS_PER_OZ
    protein_grams = size_grams * 0.25
    cost_per_gram = 5.00 / protein_grams
    expected_cost = 80.0 * cost_per_gram

    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.item_name == "Chicken Breast 16 oz"
    assert line.store == "foodlion"
    assert line.grams_protein == pytest.approx(80.0)
    # grams_food = grams_protein / (protein_per_100g / 100) -- exact, the
    # GRAMS_PER_OZ constant cancels out of the ratio.
    assert line.grams_food == pytest.approx(320.0)
    assert line.cost == pytest.approx(expected_cost, rel=1e-9)
    assert result.total_cost == pytest.approx(expected_cost, rel=1e-9)
    assert result.covered_grams == pytest.approx(80.0)
    assert result.is_complete is True
    assert result.shortfall_grams == pytest.approx(0.0)
    assert result.excluded_deals == 0
    assert result.considered_deals == 1


# --------------------------------------------------------------------------- #
# None, never a guessed weight
# --------------------------------------------------------------------------- #
def test_none_when_customer_has_no_weight_kg(conn):
    customer = Customer.create("No Weight Yet")
    assert customer.weight_kg is None
    assert bill.daily_bill_for(customer, conn=conn) is None


def test_daily_bill_by_id_returns_none_for_unknown_customer(conn):
    assert bill.daily_bill(999999, conn=conn) is None


# --------------------------------------------------------------------------- #
# Unpriceable deals are excluded, and the exclusion is counted, not hidden
# --------------------------------------------------------------------------- #
def test_unpriceable_deal_is_excluded_and_counted(conn):
    food_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    # No parsable size in the name at all -- savings.parse_size returns None,
    # so this deal can never enter the bill regardless of any food match.
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', 'Discount Chicken Value Pack', 3.00, '2099-01-01', 'scrape')"
    )
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0)
    result = bill.daily_bill_for(customer, conn=conn)

    assert result is not None
    assert result.excluded_deals == 1
    assert result.considered_deals == 1
    assert [line.item_name for line in result.lines] == ["Chicken Breast 16 oz"]


# --------------------------------------------------------------------------- #
# Zero preferences means unconstrained -- every category considered
# --------------------------------------------------------------------------- #
def test_zero_preferences_considers_every_category_not_an_empty_bill(conn):
    beef_id = _insert_food(conn, "Test Beef", "beef", 20.0)
    chicken_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    # Beef deliberately the CHEAPER $/g-protein option, so an unconstrained
    # bill preferring it (over the pricier chicken) proves beef's category
    # was actually considered, not just present.
    _insert_priced_deal(conn, "foodlion", "Beef Roast 8 oz", 2.00, beef_id)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 10.00, chicken_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    assert preferences.list_preferences(customer.id, conn=conn) == []

    result = bill.daily_bill(customer.id, conn=conn)

    assert result is not None
    assert result.categories == []  # unconstrained, echoed back as such
    assert result.considered_deals == 2
    assert result.excluded_deals == 0
    # Cheapest ($/g protein) first -- beef's own 8 oz package protein caps
    # its line, so the pricier chicken fills the remainder.
    assert [line.item_name for line in result.lines] == [
        "Beef Roast 8 oz", "Chicken Breast 16 oz",
    ]
    assert result.is_complete is True


# --------------------------------------------------------------------------- #
# A preference filter narrows to the chosen categories
# --------------------------------------------------------------------------- #
def test_preference_filter_narrows_to_chosen_categories(conn):
    beef_id = _insert_food(conn, "Test Beef", "beef", 20.0)
    chicken_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Beef Roast 8 oz", 2.00, beef_id)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 10.00, chicken_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    result = bill.daily_bill(customer.id, categories=["chicken"], conn=conn)

    assert result is not None
    assert result.categories == ["chicken"]
    assert result.considered_deals == 1
    assert result.excluded_deals == 0
    assert [line.item_name for line in result.lines] == ["Chicken Breast 16 oz"]
    # Full 80 g target covered from chicken alone (package protein > 80 g).
    assert result.covered_grams == pytest.approx(80.0)
    assert result.is_complete is True


# --------------------------------------------------------------------------- #
# A preference naming a category with no deals -- honest shortfall, no crash
# --------------------------------------------------------------------------- #
def test_preference_for_category_with_no_deals_yields_honest_shortfall(conn):
    chicken_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 10.00, chicken_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    result = bill.daily_bill(customer.id, categories=["tofu"], conn=conn)

    assert result is not None
    assert result.categories == ["tofu"]
    assert result.considered_deals == 0
    assert result.excluded_deals == 0  # the chicken deal is priceable, just filtered out
    assert result.lines == []
    assert result.total_cost == 0.0
    assert result.covered_grams == 0.0
    assert result.is_complete is False
    assert result.shortfall_grams == pytest.approx(80.0)


# --------------------------------------------------------------------------- #
# Cheapest source preferred over a more expensive one
# --------------------------------------------------------------------------- #
def test_cheapest_source_is_preferred_over_a_more_expensive_one(conn):
    cheap_id = _insert_food(conn, "Cheap Protein", "chicken", 25.0)
    pricey_id = _insert_food(conn, "Pricey Protein", "beef", 25.0)
    # Same size and protein content, different price -- isolates the choice
    # to cost_per_gram_protein alone.
    _insert_priced_deal(conn, "foodlion", "Cheap Chicken 16 oz", 2.00, cheap_id)
    _insert_priced_deal(conn, "foodlion", "Pricey Beef 16 oz", 12.00, pricey_id)
    conn.commit()

    # Small target, well under either package's own protein -- one line only.
    customer = _make_customer(conn, weight_kg=10.0)  # 16 g/day target
    result = bill.daily_bill_for(customer, conn=conn)

    assert result is not None
    assert len(result.lines) == 1
    assert result.lines[0].item_name == "Cheap Chicken 16 oz"
    assert result.considered_deals == 2  # both were priceable and in the pool


# --------------------------------------------------------------------------- #
# Store-agnostic: the same data under two different store keys produces the
# same totals -- store is a label carried through, never a code branch.
# --------------------------------------------------------------------------- #
def test_a_bill_is_not_just_the_cheapest_single_item(conn):
    """The baseline must be a real optimum over the pool, not one lucky row."""
    cheap_id = _insert_food(conn, "Cheap Protein", "chicken", 25.0)
    next_id = _insert_food(conn, "Next Protein", "beef", 25.0)
    # The cheapest deal's own package holds ~113 g of protein; the target is
    # 160 g, so a correct bill must move on to the second-cheapest for the
    # remainder rather than stopping at one line.
    _insert_priced_deal(conn, "foodlion", "Cheap Chicken 16 oz", 2.00, cheap_id)
    _insert_priced_deal(conn, "foodlion", "Next Beef 16 oz", 4.00, next_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=100.0)  # 160 g/day
    result = bill.daily_bill_for(customer, conn=conn)

    assert [line.item_name for line in result.lines] == [
        "Cheap Chicken 16 oz", "Next Beef 16 oz",
    ]
    assert result.is_complete is True


# --------------------------------------------------------------------------- #
# GFP-49 — baseline vs preference-constrained, side by side
# --------------------------------------------------------------------------- #
def test_comparison_prices_what_a_preference_costs(conn):
    cheap_id = _insert_food(conn, "Cheap Beef", "beef", 25.0)
    pricey_id = _insert_food(conn, "Pricey Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Cheap Beef 16 oz", 2.00, cheap_id)
    _insert_priced_deal(conn, "foodlion", "Pricey Chicken 16 oz", 8.00, pricey_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)   # 80 g/day
    result = bill.compare_bills(customer.id, categories=["chicken"], conn=conn)

    assert result is not None
    # Baseline takes the cheaper beef; the preference forces the pricier chicken.
    assert [line.item_name for line in result.baseline.lines] == ["Cheap Beef 16 oz"]
    assert [line.item_name for line in result.constrained.lines] == ["Pricey Chicken 16 oz"]
    assert result.delta_cost > 0
    assert result.is_constrained is True
    assert result.is_comparable is True
    assert result.caveat == ""


def test_a_preference_that_costs_nothing_extra_has_a_zero_delta(conn):
    """A preference landing on the same deals is not a penalty. Do not assume a sign."""
    food_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    result = bill.compare_bills(customer.id, categories=["chicken"], conn=conn)

    assert result.delta_cost == pytest.approx(0.0)
    assert result.is_comparable is True


def test_no_stated_preference_is_not_reported_as_a_constrained_plan(conn):
    food_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    result = bill.compare_bills(customer.id, conn=conn)   # nothing on file

    assert result.is_constrained is False       # so a UI shows one figure, not "+$0.00"
    assert result.delta_cost == pytest.approx(0.0)
    assert result.constrained.categories == []


def test_a_starving_preference_is_cheaper_but_flagged_as_not_comparable(conn):
    """The trap: a preference that cannot feed the client produces a LOWER total.

    Read naively that says "this preference saves money", when it actually
    means "this preference buys less protein". The delta stays negative and
    honest; is_comparable/caveat are what stop a UI presenting it as a saving.
    """
    beef_id = _insert_food(conn, "Test Beef", "beef", 25.0)
    tofu_id = _insert_food(conn, "Test Tofu", "tofu", 8.0)
    _insert_priced_deal(conn, "foodlion", "Beef Roast 16 oz", 4.00, beef_id)
    # One small tofu package: far too little protein to cover the target alone.
    _insert_priced_deal(conn, "foodlion", "Tofu Block 4 oz", 2.00, tofu_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=60.0, save=True)   # 96 g/day
    result = bill.compare_bills(customer.id, categories=["tofu"], conn=conn)

    assert result.baseline.is_complete is True
    assert result.constrained.is_complete is False
    assert result.delta_cost < 0                  # cheaper, but only because it is short
    assert result.is_comparable is False
    assert "buys less protein" in result.caveat


def test_an_incomplete_baseline_says_the_deals_are_thin_not_the_preference(conn):
    """When even unconstrained falls short, the caveat must not blame the preference."""
    tofu_id = _insert_food(conn, "Test Tofu", "tofu", 8.0)
    _insert_priced_deal(conn, "foodlion", "Tofu Block 4 oz", 2.00, tofu_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=60.0, save=True)
    result = bill.compare_bills(customer.id, categories=["tofu"], conn=conn)

    assert result.baseline.is_complete is False
    assert result.is_comparable is False
    assert "even unconstrained" in result.caveat


def test_comparison_is_none_without_a_weight(conn):
    assert bill.compare_bills_for(Customer.create("No Weight"), conn=conn) is None


def test_comparison_categories_do_not_touch_stored_preferences(conn):
    """GFP-52's checkboxes are a filter, so they must not need a save step."""
    food_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, "foodlion", "Chicken Breast 16 oz", 5.00, food_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    bill.compare_bills(customer.id, categories=["chicken"], conn=conn)

    assert preferences.list_preferences(customer.id, conn=conn) == []


@pytest.mark.parametrize("store", ["foodlion", "harristeeter", "wholefoods"])
def test_result_does_not_depend_on_which_store_the_deal_came_from(conn, store):
    food_id = _insert_food(conn, "Test Chicken", "chicken", 25.0)
    _insert_priced_deal(conn, store, "Chicken Breast 16 oz", 5.00, food_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0)  # 80 g/day target
    result = bill.daily_bill_for(customer, conn=conn)

    assert result is not None
    assert len(result.lines) == 1
    assert result.lines[0].store == store
    assert result.covered_grams == pytest.approx(80.0)
    size_grams = 16 * GRAMS_PER_OZ
    protein_grams = size_grams * 0.25
    expected_cost = 80.0 * (5.00 / protein_grams)
    assert result.total_cost == pytest.approx(expected_cost, rel=1e-9)


# --------------------------------------------------------------------------- #
# GFP-271: a preference-less client must not be handed a day of stock.
#
# THE REPRO, from the live database on 2026-08-12. Asked for 180 g/day at the
# lowest cost, the optimiser returned ONE line:
#
#     lidl   $1.41   180.0 g   beef cooking stock 3G Protein, 32 oz
#
# Stock. The whole recommended day. `protein_kind` has disqualified broth and
# gravy all along -- which is exactly why the cheapest-meat strip never showed
# them -- but `bill._eligible` returned `ranked` untouched whenever the client
# had expressed no preferences, so it never asked.
# --------------------------------------------------------------------------- #
def test_stock_is_never_recommended_even_with_no_preferences(conn):
    beef_id = _insert_food(conn, "Test Beef", "beef", 20.0)
    # Stock is made the CHEAPEST option on purpose. A test where the real food
    # is also the cheapest would pass without the fix and prove nothing.
    _insert_priced_deal(conn, "lidl", "beef cooking stock 3G Protein, 32 oz", 0.50, beef_id)
    _insert_priced_deal(conn, "lidl", "Beef Roast 8 oz", 6.00, beef_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    assert preferences.list_preferences(customer.id, conn=conn) == []

    result = bill.daily_bill(customer.id, conn=conn)

    assert result is not None
    names = [line.item_name for line in result.lines]
    assert "beef cooking stock 3G Protein, 32 oz" not in names
    assert names == ["Beef Roast 8 oz"]


@pytest.mark.parametrize("item_name", [
    "Swanson Beef Cooking Stock, 32 oz",
    "Chicken Broth, Low Sodium, 48 oz",
    "Knorr Beef Bouillon Cubes",
    "Turkey Gravy, 12 oz",
])
def test_the_whole_not_a_protein_buy_family_is_excluded(conn, item_name):
    beef_id = _insert_food(conn, "Test Beef", "beef", 20.0)
    _insert_priced_deal(conn, "lidl", item_name, 0.50, beef_id)
    _insert_priced_deal(conn, "lidl", "Beef Roast 8 oz", 6.00, beef_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    result = bill.daily_bill(customer.id, conn=conn)

    assert result is not None
    assert item_name not in [line.item_name for line in result.lines]


def test_a_low_confidence_guess_does_not_outrank_a_measured_density(conn):
    """The floor itself, independent of the stock disqualifier.

    Asserts the RELATIONSHIP -- a sub-floor row is not recommended -- rather
    than the constant, so tuning MIN_MATCH_CONFIDENCE cannot silently void it.
    """
    beef_id = _insert_food(conn, "Test Beef", "beef", 20.0)
    _insert_priced_deal(conn, "lidl", "Mystery Protein Bar", 0.50, beef_id)
    conn.execute(
        "UPDATE deal_food_match SET confidence = ? WHERE item_name = ?",
        (bill.MIN_MATCH_CONFIDENCE - 0.5, "Mystery Protein Bar"),
    )
    _insert_priced_deal(conn, "lidl", "Beef Roast 8 oz", 6.00, beef_id)
    conn.commit()

    customer = _make_customer(conn, weight_kg=50.0, save=True)
    result = bill.daily_bill(customer.id, conn=conn)

    assert result is not None
    assert "Mystery Protein Bar" not in [line.item_name for line in result.lines]
