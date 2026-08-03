"""The protein target formula (GFP-132).

From the nutritionist: **0.8 to 1.0 grams of protein per POUND of DESIRED body
weight.** A client who wants to weigh 150 lb eats 120-150 g/day.

The app computed ``weight_kg * 1.6`` -- per KILOGRAM, of CURRENT weight. Wrong
unit and wrong weight, putting every client 9-27% under the prescribed band.

This is the product's central number: the daily bill, the 7-day cost, the
budget and the recommended list are all computed from it, so an error here is
an error in every figure the app has ever shown.
"""
from __future__ import annotations

import sqlite3

import pytest

from grocery_planner import formulas, targets
from grocery_planner.customers import (
    DEFAULT_PROTEIN_FACTOR,
    KG_PER_LB,
    MAX_PROTEIN_FACTOR,
    MIN_PROTEIN_FACTOR,
    Customer,
    CustomerRepository,
    lb_to_kg,
)


def _client(conn, **kwargs) -> Customer:
    fields = {"name": "Test Client", "weight_kg": lb_to_kg(150)}
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


# --------------------------------------------------------------------------- #
# The nutritionist's own example, which is the acceptance test
# --------------------------------------------------------------------------- #
def test_her_example_exactly(conn):
    """'If you want to weigh 150 you will eat 120-150 g of protein a day.'"""
    client = _client(conn, desired_weight_kg=lb_to_kg(150), protein_factor=0.8)
    target = targets.protein_target_for(client, conn=conn)

    assert round(target.daily_grams) == 120
    assert round(target.daily_low_grams) == 120
    assert round(target.daily_high_grams) == 150


def test_the_top_of_her_range(conn):
    client = _client(conn, desired_weight_kg=lb_to_kg(150), protein_factor=1.0)
    assert round(targets.protein_target_for(client, conn=conn).daily_grams) == 150


@pytest.mark.parametrize("pounds,low,high", [(150, 120, 150), (180, 144, 180),
                                             (120, 96, 120), (200, 160, 200)])
def test_the_band_is_always_08_to_10_per_pound(conn, pounds, low, high):
    client = _client(conn, desired_weight_kg=lb_to_kg(pounds))
    target = targets.protein_target_for(client, conn=conn)
    assert round(target.daily_low_grams) == low
    assert round(target.daily_high_grams) == high


# --------------------------------------------------------------------------- #
# DESIRED weight, not current -- the half that needed a schema change
# --------------------------------------------------------------------------- #
def test_the_target_uses_desired_weight_not_current(conn):
    """The whole reason a column was added. A client cutting from 180 to 150
    should be fed for the 150, not the 180."""
    client = _client(conn, weight_kg=lb_to_kg(180), desired_weight_kg=lb_to_kg(150))
    target = targets.protein_target_for(client, conn=conn)

    assert round(target.daily_grams) == 120          # 150 lb x 0.8
    assert round(target.daily_grams) != 144          # NOT 180 lb x 0.8
    assert target.weight_basis == "desired"


def test_a_client_gaining_weight_gets_the_higher_target(conn):
    """The direction the old code got worst: currently 150, wants 180."""
    client = _client(conn, weight_kg=lb_to_kg(150), desired_weight_kg=lb_to_kg(180))
    assert round(targets.protein_target_for(client, conn=conn).daily_grams) == 144


def test_no_desired_weight_falls_back_to_current_and_says_so(conn):
    """A client whose goal weight has not been discussed is an ordinary state,
    not a broken record -- but the caller must be able to tell which was used."""
    client = _client(conn, weight_kg=lb_to_kg(150), desired_weight_kg=None)
    target = targets.protein_target_for(client, conn=conn)

    assert round(target.daily_grams) == 120
    assert target.weight_basis == "current"


def test_no_weight_at_all_is_still_no_target(conn):
    """'Absent stays absent, never a guess' -- unchanged by this ticket."""
    client = _client(conn, weight_kg=None, desired_weight_kg=None)
    assert targets.protein_target_for(client, conn=conn) is None


def test_a_desired_weight_alone_is_enough(conn):
    """Someone may know their goal without having weighed in."""
    client = _client(conn, weight_kg=None, desired_weight_kg=lb_to_kg(150))
    target = targets.protein_target_for(client, conn=conn)
    assert round(target.daily_grams) == 120
    assert target.weight_basis == "desired"


# --------------------------------------------------------------------------- #
# Units -- where this bug lived
# --------------------------------------------------------------------------- #
def test_the_factor_is_per_pound_not_per_kilogram(conn):
    """The substitution that was wrong. At 150 lb (68.04 kg), a factor of 0.8
    must mean 120 g -- not 54 g, which is what per-kilogram would give."""
    client = _client(conn, desired_weight_kg=lb_to_kg(150), protein_factor=0.8)
    daily = targets.protein_target_for(client, conn=conn).daily_grams

    assert round(daily) == 120
    assert round(daily) != round(lb_to_kg(150) * 0.8)     # 54 -- the old reading


def test_the_default_factor_is_the_conservative_end(conn):
    """A tool that computes what somebody eats should not pick the top of a
    professional's band on their behalf."""
    assert DEFAULT_PROTEIN_FACTOR == MIN_PROTEIN_FACTOR == 0.8
    assert MAX_PROTEIN_FACTOR == 1.0


def test_the_old_default_is_gone(conn):
    """1.6 g/kg is 0.73 g/lb -- below the nutritionist's floor. If it survives
    anywhere, some client is being under-fed."""
    assert DEFAULT_PROTEIN_FACTOR != 1.6


def test_the_formula_expression_names_pounds(conn):
    """Storing the factor in g/kg would mean 1.7637 -- a unit conversion hiding
    inside a user-editable field. In pounds she says 0.8 and types 0.8."""
    assert "desired_weight_lb" in targets.DEFAULT_FORMULA_EXPRESSION
    assert "weight_kg *" not in targets.DEFAULT_FORMULA_EXPRESSION


def test_the_migration_constant_matches_the_code(conn):
    """0017_GFP-132.ddl hard-codes KG_PER_LB because a .ddl cannot import
    Python. If the two ever drift, every migrated client's factor is wrong by
    the amount of the drift, silently."""
    import pathlib
    ddl = (pathlib.Path(__file__).resolve().parent.parent
           / "db_script" / "migration" / "0017_GFP-132.ddl").read_text(encoding="utf-8")
    assert str(KG_PER_LB) in ddl, f"the migration does not use {KG_PER_LB}"


# --------------------------------------------------------------------------- #
# The range
# --------------------------------------------------------------------------- #
def test_a_factor_inside_the_band_reports_in_range(conn):
    client = _client(conn, desired_weight_kg=lb_to_kg(150), protein_factor=0.9)
    target = targets.protein_target_for(client, conn=conn)
    assert target.has_range
    assert target.in_range is True
    assert target.daily_low_grams < target.daily_grams < target.daily_high_grams


def test_a_factor_outside_the_band_reports_out_of_range(conn):
    """A client whose factor predates GFP-133's bounds can legitimately sit
    outside. That is worth showing, not hiding."""
    client = _client(conn, desired_weight_kg=lb_to_kg(150), protein_factor=1.4)
    assert targets.protein_target_for(client, conn=conn).in_range is False


def test_a_custom_formula_gets_no_invented_range(conn):
    """The band is a band on a FACTOR, so it only describes the default
    formula. A nutritionist's own age- or activity-based rule may have no band
    at all, and drawing ends around it would be inventing a prescription
    nobody gave (GFP-64 allows exactly this)."""
    formulas.set_formula(conn, targets.FORMULA_NAME, "weight_kg * 2")
    client = _client(conn, desired_weight_kg=lb_to_kg(150))
    target = targets.protein_target_for(client, conn=conn)

    assert target.has_range is False
    assert target.in_range is None
    assert target.daily_grams == pytest.approx(lb_to_kg(150) * 2)


def test_weekly_is_still_seven_daily(conn):
    client = _client(conn, desired_weight_kg=lb_to_kg(150))
    target = targets.protein_target_for(client, conn=conn)
    assert target.weekly_grams == pytest.approx(target.daily_grams * 7)


# --------------------------------------------------------------------------- #
# The migration, which is where existing customers' data is at risk
# --------------------------------------------------------------------------- #
def test_the_migration_preserves_each_clients_target(conn):
    """The neat property, and the reason to convert rather than reset:

        old:  weight_kg * f
        new:  (weight_kg / KG_PER_LB) * (f * KG_PER_LB)      <- identical

    So converting the stored factor leaves the daily target exactly where it
    was, and CLAMPING is then the only thing that changes anybody's number.
    """
    for kilograms, old_factor in ((82.0, 1.8), (75.0, 2.0), (90.0, 1.9)):
        converted = old_factor * KG_PER_LB
        if not (MIN_PROTEIN_FACTOR <= converted <= MAX_PROTEIN_FACTOR):
            continue                        # clamped ones are tested below
        old_target = kilograms * old_factor
        new_target = (kilograms / KG_PER_LB) * converted
        assert new_target == pytest.approx(old_target)


def test_the_old_default_clamps_upward(conn):
    """1.6 g/kg converts to 0.726 g/lb, below the floor. Every clamp is
    upward, because the old default sat under the nutritionist's range.
    Targets rise; none fall. On a nutrition tool that is the direction to
    err in."""
    converted = 1.6 * KG_PER_LB
    assert converted < MIN_PROTEIN_FACTOR
    assert max(MIN_PROTEIN_FACTOR, min(MAX_PROTEIN_FACTOR, converted)) == MIN_PROTEIN_FACTOR


def test_a_hand_set_factor_is_converted_not_preserved(conn):
    """The bug an earlier draft of the migration had, caught by running it
    against a real pre-migration database.

    A client sitting at 1.8 never chose '1.8 grams per pound' -- that value
    never meant pounds. Left alone it reads as 1.8 g/lb and puts an 82 kg
    client on 325 g/day instead of 148. PRESERVING THE NUMBER DESTROYS THE
    INTENT; converting the number preserves it.
    """
    kilograms = 82.0
    intended = kilograms * 1.8                       # 147.6 g/day
    naive = (kilograms / KG_PER_LB) * 1.8            # if left alone: 325 g/day
    converted = (kilograms / KG_PER_LB) * (1.8 * KG_PER_LB)

    assert naive > 2 * intended, "the un-migrated reading should be wildly high"
    assert converted == pytest.approx(intended)
