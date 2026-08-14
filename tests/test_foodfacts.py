"""The one shared nutrition write (GFP-302).

Consolidating six copies is only safe if the thing that differed between them is
still expressible. These tests pin the two differences that actually mattered
and that a future tidy-up would be tempted to collapse.
"""
import pytest

# NB: `foods` is not empty in a fresh database -- the 32-food curated catalog
# is seeded by the init script. Every query here is scoped to the source it
# just wrote, or it would be counting the catalog.

from grocery_planner import matching
from grocery_planner.scrapers import foodfacts, kroger, traderjoes, wholefoods
from grocery_planner.scrapers import wegmans_api


def _fact(**over):
    kwargs = dict(
        source_ref="123", name="Boneless Skinless Chicken Breast",
        category="Meat & Seafood", protein_per_100g=23.0,
        item_name="Chicken Breast, 1 lb",
    )
    kwargs.update(over)
    return foodfacts.FoodFact(**kwargs)


# --------------------------------------------------------------------------- #
# THE TRAP: the vendor is not always the banner
# --------------------------------------------------------------------------- #
def test_source_and_store_are_recorded_separately(conn):
    """foods.source is the API; deal_food_match.store must match deals.store.

    Kroger is the live case: it reads from the Kroger API (`source='kroger'`)
    for products sold as Harris Teeter (`store='harristeeter'`). Collapsing
    these to one argument writes a match against a store key that appears in no
    `deals` row, so the join silently yields nothing -- Harris Teeter would have
    lost all 537 of its retailer-direct matches while kroger's own tests, which
    only look at kroger rows, still passed.
    """
    foodfacts.upsert_food_fact(
        conn, "kroger", "harristeeter", "kroger_api_direct", _fact()
    )
    conn.commit()

    food = conn.execute(
        "SELECT source, slug FROM foods WHERE source='kroger'"
    ).fetchone()
    assert food["source"] == "kroger", "foods.source is the API"
    assert food["slug"] == "kroger-123", "the slug follows the source, not the store"

    match = conn.execute("SELECT store, method FROM deal_food_match").fetchone()
    assert match["store"] == "harristeeter", (
        "the match must key on the BANNER or it joins to no deals row"
    )
    assert match["method"] == "kroger_api_direct"


def test_the_live_scrapers_declare_the_pair_they_actually_use():
    """Each scraper's constants, pinned so a rename cannot quietly swap them."""
    assert (kroger.FOOD_SOURCE, kroger.STORE_KEY) == ("kroger", "harristeeter")
    assert kroger.FOOD_SOURCE != kroger.STORE_KEY, (
        "kroger is the case that proves these are two concepts"
    )
    for module, source in (
        (wholefoods, "wholefoods"), (traderjoes, "traderjoes"),
        (wegmans_api, "wegmans"),
    ):
        assert module.FOOD_SOURCE == source
        assert module.FOOD_SOURCE == module.STORE_KEY, (
            "these three coincide today -- if that changes, the pair above is "
            "what expresses it"
        )


# --------------------------------------------------------------------------- #
# The write itself
# --------------------------------------------------------------------------- #
def test_the_match_is_manual_so_the_keyword_matcher_cannot_downgrade_it(conn):
    """The load-bearing flag, previously re-argued in all six copies.

    The figure came off the retailer's own label for this exact product. MANUAL
    is what stops match_deals' keyword auto-matcher overwriting a measurement
    with a guess about a similarly-named food.
    """
    foodfacts.upsert_food_fact(conn, "wholefoods", "wholefoods",
                               "wholefoods_direct", _fact())
    conn.commit()
    row = conn.execute("SELECT confidence, match_source FROM deal_food_match").fetchone()
    assert row["confidence"] == 1.0
    assert row["match_source"] == matching.MANUAL


def test_re_running_updates_rather_than_duplicating(conn):
    """Every scrape re-writes these; the upserts must be idempotent."""
    foodfacts.upsert_food_fact(conn, "wegmans", "wegmans", "wegmans_api_direct",
                               _fact(protein_per_100g=20.0))
    foodfacts.upsert_food_fact(conn, "wegmans", "wegmans", "wegmans_api_direct",
                               _fact(protein_per_100g=25.0))
    conn.commit()

    assert conn.execute(
        "SELECT COUNT(*) c FROM foods WHERE source='wegmans'"
    ).fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM deal_food_match").fetchone()["c"] == 1
    density = conn.execute(
        "SELECT fn.amount_per_100g a FROM food_nutrients fn "
        "JOIN foods f ON f.id = fn.food_id "
        "WHERE f.source='wegmans' AND fn.nutrient='protein'"
    ).fetchone()["a"]
    assert density == 25.0, "the later figure must win"


def test_two_sources_can_hold_the_same_product_ref_without_colliding(conn):
    """foods is unique on (source, source_ref), not on source_ref alone.

    '123' as a Kroger product id and '123' as a Trader Joe's SKU are unrelated
    strings denoting different products -- the same reasoning as GFP-111's
    product_identifier_ns.
    """
    foodfacts.upsert_food_fact(conn, "kroger", "harristeeter",
                               "kroger_api_direct", _fact(item_name="A"))
    foodfacts.upsert_food_fact(conn, "traderjoes", "traderjoes",
                               "traderjoes_label_direct", _fact(item_name="B"))
    conn.commit()

    sources = [
        r["source"] for r in conn.execute(
            "SELECT source FROM foods WHERE source IN ('kroger','traderjoes') "
            "ORDER BY source"
        )
    ]
    assert sources == ["kroger", "traderjoes"], "one product ref, two real foods"


# --------------------------------------------------------------------------- #
# The asymmetry the consolidation had to preserve
# --------------------------------------------------------------------------- #
def test_wegmans_has_no_separate_product_title_and_says_so(conn):
    """Its feed carries only one name, so foods.name == the deals name.

    That is the fallback the other three take conditionally
    (`name=description or item_name`), taken unconditionally. Asserted rather
    than left implicit, because the shared FoodFact has both fields and a
    future edit could 'fix' this into something the feed cannot supply.
    """
    assert not hasattr(wegmans_api.FoodFact, "name"), (
        "wegmans_api.FoodFact gained a title its feed does not provide"
    )
    fields = wegmans_api.FoodFact.__dataclass_fields__
    assert "item_name" in fields and "name" not in fields
