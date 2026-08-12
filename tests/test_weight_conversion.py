"""One definition of a pound, asserted as a relationship.

Before this, the avoirdupois pound was written out by hand in three unrelated
modules. Nothing was wrong with any of the three -- which is exactly the
problem: a literal that is correct in three places is three chances for one of
them to be edited and the others not, with no test that would notice.

So these tests deliberately do not check ``453.59237``. They check that every
consumer resolves to the *same* pound, and that the pound is derived from the
ounce rather than stated alongside it. A change that keeps them consistent
keeps the tests; only a drift breaks them.
"""
from __future__ import annotations

import pytest

from grocery_planner import savings
from grocery_planner.scrapers import kroger
from grocery_planner.service import shopping


def test_the_pound_is_derived_from_the_ounce():
    """Not two constants that happen to agree -- one, computed from the other."""
    assert savings.GRAMS_PER_LB == savings.GRAMS_PER_OZ * savings.OUNCES_PER_LB


def test_every_module_resolves_to_the_same_pound():
    """The guard the old triplicate literals never had."""
    assert shopping.GRAMS_PER_POUND == savings.GRAMS_PER_LB
    assert kroger._GRAMS_PER_UNIT["pound"] == savings.GRAMS_PER_LB
    assert kroger._GRAMS_PER_UNIT["lb"] == savings.GRAMS_PER_LB
    assert kroger._GRAMS_PER_UNIT["ounce"] == savings.GRAMS_PER_OZ


def test_the_shared_size_grammar_agrees_with_the_constant():
    """``parse_size`` normalises weight to ounces; a pound must be 16 of them,
    or per-pound deals and per-package deals would be priced on different
    scales without anything failing."""
    size = savings.parse_size("Ground Beef, 1 lb")
    assert size is not None
    assert size.base_quantity * savings.GRAMS_PER_OZ == pytest.approx(
        savings.GRAMS_PER_LB
    )


# --------------------------------------------------------------------------- #
# The helpers themselves
# --------------------------------------------------------------------------- #
def test_pounds_and_grams_round_trip():
    for pounds in (0.25, 1.0, 3.5, 10.0):
        assert savings.grams_to_pounds(savings.pounds_to_grams(pounds)) == pytest.approx(
            pounds
        )


@pytest.mark.parametrize("fn", [savings.pounds_to_grams, savings.grams_to_pounds])
def test_none_in_none_out(fn):
    """This module's rule 1: a missing number is None, never a guess."""
    assert fn(None) is None


def test_price_per_gram_is_the_price_divided_by_a_pound_of_grams():
    """Asserted against the constant, not a precomputed decimal."""
    assert savings.price_per_gram_from_per_pound(4.99) == pytest.approx(
        4.99 / savings.GRAMS_PER_LB
    )


def test_price_per_100g_is_exactly_a_hundred_times_the_per_gram_figure():
    """Derived, so the two displays can never round apart."""
    for price in (1.99, 4.99, 11.19):
        assert savings.price_per_100g_from_per_pound(price) == pytest.approx(
            savings.price_per_gram_from_per_pound(price) * 100.0
        )


@pytest.mark.parametrize("price", [None, 0.0, -1.0])
def test_a_free_pound_is_a_data_error_not_a_bargain(price):
    """0.0 would sort straight to the top of every cheapest-protein list."""
    assert savings.price_per_gram_from_per_pound(price) is None
    assert savings.price_per_100g_from_per_pound(price) is None


def test_a_per_pound_price_beats_a_per_package_price_only_on_the_real_numbers():
    """A worked example, in the units a customer actually sees.

    $4.99/lb and a $2.49 / 7 oz package: the per-pound item is cheaper per
    gram, and the helper must say so. This is the comparison the customer-
    facing app exists to make, so it is pinned end to end rather than by
    checking the divisor.
    """
    per_lb = savings.price_per_gram_from_per_pound(4.99)
    package = 2.49 / (7 * savings.GRAMS_PER_OZ)
    assert per_lb < package
