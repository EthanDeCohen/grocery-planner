"""GFP-248: a sale price must be able to reach a cost per gram of protein.

The defect. A store can have two feeds -- a Flipp weekly ad carrying
promotional prices, and a catalogue feed carrying sizes and nutrition the ad
never has. Nothing joined them. Measured on the live database for the one store
that already had both: 406 Flipp names, 977 catalogue names, ZERO exact
overlap, because the ad says "Gatorade" and the catalogue says "Harris Teeter
Boneless Chicken Breast Value Pack, 1 lb".

``savings.cost_per_gram_protein`` reads a weight-based size out of the item
NAME, so a promotional name could never be priced per gram of protein -- while
the catalogue row for the same product had the size, at full price. The product
was systematically ranking regular prices above sale prices on the one metric
it exists to compute.
"""
from __future__ import annotations

import pytest

from grocery_planner import matching, savings, sourcelink

# The catalogue side: a real Kroger-shaped name, size in the name.
CATALOGUE = "Harris Teeter Boneless Skinless Chicken Breasts Small Pack, 1 lb"
# The promo side: what a weekly ad calls the same thing. No size anywhere.
PROMO = "Boneless Chicken Breast"
STORE = "harristeeter"


def _deal(conn, store, item_name, price, source):
    conn.execute(
        "INSERT INTO deals(store, item_name, sub_category, deal_type, "
        "dollar_price, source) VALUES (?, ?, 'Meat & Seafood', 'Weekly Ad', ?, ?)",
        (store, item_name, price, source),
    )
    conn.commit()


def _both_feeds(conn, store=STORE, promo=PROMO, catalogue=CATALOGUE):
    _deal(conn, store, catalogue, 4.99, "kroger-api")
    _deal(conn, store, promo, 1.99, "scrape")
    matching.match_deals(conn=conn)
    return sourcelink.build_links(conn=conn)


# --------------------------------------------------------------------------- #
# The regression
# --------------------------------------------------------------------------- #
def test_a_promo_price_reaches_cost_per_gram_protein(conn):
    """THE POINT OF THE TICKET."""
    before = savings.cost_per_gram_protein(1.99, PROMO, STORE, conn=conn)
    assert before is None, "precondition: a promo name has no size of its own"

    _both_feeds(conn)

    after = savings.cost_per_gram_protein(1.99, PROMO, STORE, conn=conn)
    assert after is not None, "the sale price still cannot be priced per gram"
    assert after.size_grams == pytest.approx(453.59237, rel=1e-4)  # the borrowed 1 lb


def test_the_sale_price_now_beats_the_catalogue_price(conn):
    """The whole business case: a promotion should rank ahead of full price."""
    _both_feeds(conn)
    promo = savings.cost_per_gram_protein(1.99, PROMO, STORE, conn=conn)
    full = savings.cost_per_gram_protein(4.99, CATALOGUE, STORE, conn=conn)
    assert promo.cost_per_gram_protein < full.cost_per_gram_protein


def test_a_borrowed_size_does_not_claim_a_read_size_confidence(conn):
    """One inference removed from the row being priced, and it must say so."""
    _both_feeds(conn)
    promo = savings.cost_per_gram_protein(1.99, PROMO, STORE, conn=conn)
    direct = savings.cost_per_gram_protein(4.99, CATALOGUE, STORE, conn=conn)

    assert promo.match_confidence <= sourcelink.LINK_CONFIDENCE
    assert promo.match_confidence <= direct.match_confidence
    assert sourcelink.LINK_METHOD in promo.match_method, (
        "an auditor must be able to see the package weight was borrowed")


# --------------------------------------------------------------------------- #
# Honesty: a wrong size is worse than a missing one
# --------------------------------------------------------------------------- #
def test_no_catalogue_feed_means_no_link_and_no_change(conn):
    """A store with only an ad behaves exactly as it did before this feature."""
    _deal(conn, "foodlion", PROMO, 1.99, "scrape")
    matching.match_deals(conn=conn)
    stats = sourcelink.build_links(conn=conn)

    assert stats["linked"] == 0
    assert savings.cost_per_gram_protein(1.99, PROMO, "foodlion", conn=conn) is None


def test_a_different_cut_never_lends_its_package(conn):
    """Both are chicken. Neither is the other."""
    _deal(conn, STORE, "Fresh Chicken Wings, 1 lb", 3.49, "kroger-api")
    _deal(conn, STORE, PROMO, 1.99, "scrape")
    matching.match_deals(conn=conn)
    sourcelink.build_links(conn=conn)

    link = sourcelink.get_link(STORE, PROMO, conn=conn)
    assert link is None or "Wings" not in link.linked_item_name


def test_a_promo_row_that_matched_no_food_is_not_linked(conn):
    """Without an agreed food there is no evidence beyond words, and words
    alone would link 'Gatorade' to 'Gatorade Protein Shake'."""
    _deal(conn, STORE, CATALOGUE, 4.99, "kroger-api")
    _deal(conn, STORE, "Gatorade", 1.00, "scrape")
    matching.match_deals(conn=conn)
    sourcelink.build_links(conn=conn)

    assert sourcelink.get_link(STORE, "Gatorade", conn=conn) is None


def test_links_are_rebuilt_not_accumulated(conn):
    """deals is replaced wholesale on every scrape; a surviving link would be
    a stale claim about a product that may no longer be on offer."""
    _both_feeds(conn)
    assert sourcelink.get_link(STORE, PROMO, conn=conn) is not None

    conn.execute("DELETE FROM deals")
    conn.commit()
    stats = sourcelink.build_links(conn=conn)

    assert stats["linked"] == 0
    assert sourcelink.get_link(STORE, PROMO, conn=conn) is None


# --------------------------------------------------------------------------- #
# The scoring rules, tested directly
# --------------------------------------------------------------------------- #
def test_overlap_is_asymmetric_on_purpose(conn):
    """A catalogue name is long and specific; a promo name is short. A
    symmetric measure would punish the catalogue for being descriptive, which
    is the property that makes it useful."""
    assert sourcelink.overlap(PROMO, CATALOGUE) == 1.0
    assert sourcelink.overlap(CATALOGUE, PROMO) < 1.0


def test_packaging_words_and_digits_do_not_count_as_identity(conn):
    """'Fresh', 'Value Pack' and '1' say nothing about which product this is --
    and digits are size, which is exactly what the promo side lacks."""
    assert sourcelink.overlap("Fresh Chicken Breast Value Pack", "Chicken Breast") == 1.0


def test_ties_break_toward_the_shortest_candidate(conn):
    """Among equally-good matches the least embellished is the more
    conservative borrow."""
    best = sourcelink.candidates_for(
        "Chicken Breast",
        ["Chicken Breast Strips Breaded Frozen, 2 lb", "Chicken Breast, 1 lb"],
    )
    assert best == "Chicken Breast, 1 lb"


def test_nothing_below_the_threshold_is_linked(conn):
    assert sourcelink.candidates_for("Chicken Breast", ["Ground Beef, 1 lb"]) is None


# --------------------------------------------------------------------------- #
# Store-agnostic (GFP-32)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("store", ["harristeeter", "foodlion", "invented-store-9000"])
def test_the_join_works_for_any_store_identity(conn, store):
    _both_feeds(conn, store=store)
    priced = savings.cost_per_gram_protein(1.99, PROMO, store, conn=conn)
    assert priced is not None


def test_a_contradiction_is_never_linked(conn):
    """Word overlap is blind to negation: 'Boneless Pork Loin' and 'Bone-in
    Center Cut Pork Loin Chops' share nearly every word and are opposites, and
    a bone-in package weight overstates the protein in a boneless price."""
    assert sourcelink.contradicts(
        "Boneless Pork Loin", "Niman Ranch Bone-in Center Cut Pork Loin Chops, 1 lb")
    assert sourcelink.contradicts("Cooked Shrimp", "Woods Fisheries Raw Shrimp, 1 lb")
    assert not sourcelink.contradicts(
        "Boneless Chicken Breast", "Harris Teeter Boneless Skinless Chicken Breasts, 1 lb")
    assert sourcelink.candidates_for(
        "Boneless Pork Loin", ["Niman Ranch Bone-in Center Cut Pork Loin Chops, 1 lb"]
    ) is None
