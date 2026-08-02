"""Tests for GFP-66: Customer.weight_display must echo back whole numbers a
nutritionist typed, not a float round-trip artifact like
``149.99999999999997``.

grocery_planner/customers.py's lb<->kg conversion (KG_PER_LB =
0.45359237) is exact *math* but not exact *float* arithmetic: some inputs
(150 lb, 175.5 lb, ...) come back from a kg->lb round trip a few ULPs off
whole/expected values. tests/test_customers.py already covers that the
underlying math is correct to within pytest.approx tolerances and must
keep doing so unmodified; this file covers the separate, narrower concern
that the *display* value shown to a user is clean, since a health tool
echoing "149.99999999999997" back to someone who typed "150" is not
acceptable regardless of whether the stored math is right.
"""
from __future__ import annotations

from grocery_planner.customers import Customer


def test_weight_display_echoes_a_whole_number_exactly():
    # The bug as reported: 150 lb stores correctly as ~68.0388 kg, but the
    # naive kg->lb round trip for the display value came back as
    # 149.99999999999997 instead of 150.
    c = Customer.create("Jamie", weight=150, weight_unit="lb")
    assert c.weight_display == 150
    assert repr(c.weight_display) == "150.0"


def test_weight_display_echoes_other_round_trip_lossy_values():
    # A handful of other inputs that are also known to pick up float noise
    # on the kg->lb round trip (see the module comment above).
    for pounds in (150, 175.5, 220, 8):
        c = Customer.create("Jamie", weight=pounds, weight_unit="lb")
        assert c.weight_display == pounds


def test_weight_display_still_agrees_with_the_math_within_tolerance():
    # Rounding for display must not silently change *which* value is shown
    # -- it should still be the same number, just without float noise.
    import pytest

    c = Customer.create("Jamie", weight=150, weight_unit="lb")
    assert c.weight_display == pytest.approx(150, abs=1e-6)


def test_weight_display_kg_unit_is_unaffected():
    # A customer entered in kg has no lb<->kg round trip at all -- make
    # sure the rounding doesn't introduce noise where there was none.
    c = Customer.create("Alex", weight=65, weight_unit="kg")
    assert c.weight_display == 65


def test_weight_display_preserves_a_deliberately_entered_fraction():
    # Rounding for display must not be so coarse it eats a real fraction a
    # user actually typed.
    c = Customer.create("Jamie", weight=150.25, weight_unit="lb")
    assert c.weight_display == 150.25
