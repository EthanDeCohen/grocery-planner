"""Savings engine tests (GFP-8): size parsing, unit cost, ranking, scoring.

The size fixtures are real item names taken from Food Lion and Harris Teeter
weekly ads, because that is where the hard cases actually live.
"""
import pytest

from grocery_planner import formulas, savings, service


# --------------------------------------------------------------------------- #
# Size parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,quantity,base_unit", [
    ("16 oz. Simple Truth Organic Peanut Butter", 16.0, "oz"),
    ("12 OZ. Frigo CheeseHeads String Cheese", 12.0, "oz"),
    ("5 lb. All Purpose Flour", 80.0, "oz"),          # 5 lb -> 80 oz
    ("2 Liter Coca-Cola", 67.628, "fl oz"),
    ("Large Eggs dozen", 12.0, "each"),
    ("8 ct. Pillsbury Toaster Strudel", 8.0, "each"),
])
def test_parse_size_reads_common_formats(name, quantity, base_unit):
    size = savings.parse_size(name)
    assert size is not None, name
    assert size.base_quantity == pytest.approx(quantity, rel=1e-3)
    assert size.base_unit == base_unit


def test_parse_size_takes_the_low_end_of_a_range():
    """'5.4-5.5 Oz.' -> 5.4, so the cost per ounce is never flattering."""
    size = savings.parse_size("5.4-5.5 Oz. Select Varieties")
    assert size.base_quantity == pytest.approx(5.4)


def test_parse_size_multiplies_a_multipack():
    size = savings.parse_size("6 pk 500 ml Acqua Panna")
    assert size.base_quantity == pytest.approx(6 * 500 * 0.033814, rel=1e-3)


def test_parse_size_uses_only_the_headline_product():
    """An 'A or B' promo is one price; the size of B is not what it buys."""
    name = "11 oz. HT Traders Bag Coffee or 8 - 10 Ct. Green Mountain K-Cup Coffee"
    size = savings.parse_size(name)
    assert size.base_quantity == pytest.approx(11.0)
    assert size.base_unit == "oz"


def test_fl_oz_is_volume_not_weight():
    assert savings.parse_size("12 fl oz can").base_unit == "fl oz"
    assert savings.parse_size("12 oz bag").base_unit == "oz"


@pytest.mark.parametrize("name", [
    "Eggo Frozen Waffles",           # no size anywhere
    "Kevin's Frozen Bowls",
    "",
    None,
])
def test_parse_size_admits_when_it_cannot_tell(name):
    """A guessed size would be confidently wrong — None is the honest answer."""
    assert savings.parse_size(name) is None


# --------------------------------------------------------------------------- #
# Cost per unit and savings
# --------------------------------------------------------------------------- #
def test_cost_per_unit():
    cost, unit = savings.cost_per_unit(4.00, "16 oz. Peanut Butter")
    assert cost == pytest.approx(0.25)
    assert unit == "oz"


@pytest.mark.parametrize("price,name", [
    (None, "16 oz. Peanut Butter"),   # no price
    (0.0, "16 oz. Peanut Butter"),    # nonsense price
    (4.00, "Frozen Waffles"),         # no readable size
])
def test_cost_per_unit_returns_none_when_incomparable(price, name):
    assert savings.cost_per_unit(price, name) is None


def test_savings_vs_regular_needs_a_regular_price():
    assert savings.savings_vs_regular({"regular_price": 5.00, "sale_price": 4.00}) == (
        pytest.approx(1.0), pytest.approx(20.0)
    )
    # Scraped rows carry no regular_price — this is the GFP-5 gap.
    assert savings.savings_vs_regular({"regular_price": None, "sale_price": 4.00}) is None
    # A "sale" that isn't cheaper is not a saving.
    assert savings.savings_vs_regular({"regular_price": 4.00, "sale_price": 4.50}) is None


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
DEALS = [
    {"store": "foodlion", "item_name": "16 oz. Peanut Butter", "dollar_price": 4.00,
     "sale_price": 4.00, "regular_price": None},
    {"store": "harristeeter", "item_name": "28 oz. Peanut Butter", "dollar_price": 5.60,
     "sale_price": 5.60, "regular_price": None},
    {"store": "foodlion", "item_name": "Mystery Feature", "dollar_price": 1.00,
     "sale_price": 1.00, "regular_price": None},
]


def test_rank_by_unit_price_orders_cheapest_first():
    ranked = savings.rank_by_unit_price(DEALS)
    # $5.60/28oz = $0.20/oz beats $4.00/16oz = $0.25/oz, despite costing more.
    assert [r["item_name"] for r in ranked] == [
        "28 oz. Peanut Butter", "16 oz. Peanut Butter"
    ]
    assert ranked[0]["unit_price"] == pytest.approx(0.20)
    assert [r["rank"] for r in ranked] == [1, 2]


def test_rank_drops_rows_it_cannot_compare():
    ranked = savings.rank_by_unit_price(DEALS)
    assert "Mystery Feature" not in {r["item_name"] for r in ranked}


def test_rank_groups_by_unit_so_oz_and_each_never_interleave():
    rows = DEALS + [
        {"store": "foodlion", "item_name": "12 ct. Eggs", "dollar_price": 3.00,
         "sale_price": 3.00, "regular_price": None},
    ]
    ranked = savings.rank_by_unit_price(rows)
    units = [r["unit"] for r in ranked]
    assert units == sorted(units)  # all of one unit before the next


def test_annotate_keeps_incomparable_rows_with_empty_fields():
    annotated = savings.annotate(DEALS)
    assert len(annotated) == 3
    mystery = next(r for r in annotated if r["item_name"] == "Mystery Feature")
    assert mystery["unit_price"] is None
    assert mystery["size"] == ""


# --------------------------------------------------------------------------- #
# Formula scoring
# --------------------------------------------------------------------------- #
def test_score_deals_ranks_by_a_user_formula(conn):
    formulas.set_formula(conn, "value", "1 / unit_price")
    scored = savings.score_deals(conn, "value", DEALS)
    # Cheapest per ounce scores highest.
    assert scored[0]["item_name"] == "28 oz. Peanut Butter"
    assert scored[0]["score"] == pytest.approx(5.0)


def test_score_deals_can_use_profile_values(conn):
    conn.execute("INSERT INTO profile(key, value) VALUES ('budget', '10')")
    conn.commit()
    formulas.set_formula(conn, "affordable", "budget / price")
    scored = savings.score_deals(conn, "affordable", DEALS)
    assert scored[0]["score"] == pytest.approx(10.0)  # the $1.00 row


def test_score_deals_skips_rows_the_formula_cannot_handle(conn):
    formulas.set_formula(conn, "per_oz", "10 / unit_price")
    scored = savings.score_deals(conn, "per_oz", DEALS)
    # "Mystery Feature" has unit_price 0 -> ZeroDivisionError -> skipped, not zeroed.
    assert "Mystery Feature" not in {r["item_name"] for r in scored}
    assert len(scored) == 2


def test_score_deals_unknown_formula_raises(conn):
    with pytest.raises(KeyError):
        savings.score_deals(conn, "nope", DEALS)


# --------------------------------------------------------------------------- #
# Service integration
# --------------------------------------------------------------------------- #
def test_best_deals_uses_the_shared_filters(conn):
    conn.executemany(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, dollar_price, "
        "sale_price, valid_to, source) VALUES (?, ?, ?, 'Weekly Ad', ?, ?, '2099-01-01', 'scrape')",
        [
            ("foodlion", "16 oz. Peanut Butter", "Pantry & Seasoning", 4.00, 4.00),
            ("harristeeter", "28 oz. Peanut Butter", "Pantry & Seasoning", 5.60, 5.60),
            ("foodlion", "12 oz. Coffee", "Beverages", 9.00, 9.00),
        ],
    )
    conn.commit()

    ranked = service.best_deals(conn=conn, search="peanut butter")
    assert [r["item_name"] for r in ranked] == [
        "28 oz. Peanut Butter", "16 oz. Peanut Butter"
    ]
    assert service.best_deals(conn=conn, store="harristeeter")[0]["store"] == "harristeeter"


def test_best_deals_ignores_expired_rows_by_default(conn):
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('foodlion', '16 oz. Old Peanut Butter', 1.00, '2020-01-01', 'scrape')"
    )
    conn.commit()
    assert service.best_deals(conn=conn) == []
    assert len(service.best_deals(conn=conn, hide_expired=False)) == 1
