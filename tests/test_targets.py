"""Tests for grocery_planner.targets (GFP-29): the protein-target engine.

Centers on the "2.2x bug" boundary the ticket calls out explicitly (a 90 lb
client vs. a 90 kg client must land on very different targets), the
None-not-a-guess rule for unknown weight, the weekly figure, and that
protein_factor stays editable through the formulas engine rather than a
hard-coded multiplier.
"""
from __future__ import annotations

import pytest

from grocery_planner import formulas
from grocery_planner.customers import (
    DEFAULT_PROTEIN_FACTOR,
    Customer,
    CustomerRepository,
    kg_to_lb,
)
from grocery_planner.targets import (
    DAYS_PER_WEEK,
    DEFAULT_FORMULA_EXPRESSION,
    FORMULA_NAME,
    GRAMS_PER_DAY,
    GRAMS_PER_WEEK,
    ProteinTarget,
    protein_target,
    protein_target_for,
)

# --------------------------------------------------------------------------- #
# The lb/kg boundary -- the whole point of the ticket
# --------------------------------------------------------------------------- #
def test_90_lb_client_resolves_to_72_g_per_day(conn):
    # GFP-132: 90 lb desired x 0.8 g/lb = 72 g/day. Was 65.317 under the old
    # per-kilogram formula; the point of the test is unchanged -- a pounds
    # client must be read as pounds.
    c = Customer.create("Jamie", weight=90, weight_unit="lb")
    target = protein_target_for(c, conn=conn)
    assert target.daily_grams == pytest.approx(90 * DEFAULT_PROTEIN_FACTOR, abs=0.01)


def test_90_kg_client_resolves_to_144_g_per_day(conn):
    # The mistaken reading the ticket warns about -- 90 kg is a real client
    # and correctly yields the much larger 144 g/day, distinct from the 90 lb
    # case above by more than 2x.
    c = Customer.create("Jamie", weight=90, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert target.daily_grams == pytest.approx(
        kg_to_lb(90) * DEFAULT_PROTEIN_FACTOR, abs=1e-9
    )


def test_90_lb_and_90_kg_clients_differ_by_more_than_2x(conn):
    lb_client = Customer.create("Lb Client", weight=90, weight_unit="lb")
    kg_client = Customer.create("Kg Client", weight=90, weight_unit="kg")
    lb_target = protein_target_for(lb_client, conn=conn)
    kg_target = protein_target_for(kg_client, conn=conn)
    assert kg_target.daily_grams / lb_target.daily_grams == pytest.approx(2.2046226, abs=1e-4)


# --------------------------------------------------------------------------- #
# None, never a guess, when weight is unknown
# --------------------------------------------------------------------------- #
def test_protein_target_for_is_none_when_weight_unknown(conn):
    c = Customer.create("No Weight Yet")
    assert c.weight_kg is None
    assert protein_target_for(c, conn=conn) is None


def test_protein_target_by_id_is_none_when_weight_unknown(conn):
    saved = CustomerRepository.save(Customer.create("No Weight Yet"), conn=conn)
    assert protein_target(saved.id, conn=conn) is None


def test_protein_target_is_none_for_missing_customer_id(conn):
    assert protein_target(999, conn=conn) is None


def test_protein_target_resolves_a_soft_deleted_customer(conn):
    # protein_target is a lookup by id, not a listing -- a soft-deleted
    # client (grocery_planner/customers.py) should not silently disappear
    # from a read the same way it disappears from CustomerRepository.list.
    saved = CustomerRepository.save(
        Customer.create("Jamie", weight=65, weight_unit="kg"), conn=conn
    )
    CustomerRepository.delete(saved.id, conn=conn)
    target = protein_target(saved.id, conn=conn)
    assert target is not None
    assert target.daily_grams == pytest.approx(
        kg_to_lb(65) * DEFAULT_PROTEIN_FACTOR, abs=1e-9
    )


# --------------------------------------------------------------------------- #
# Weekly figure
# --------------------------------------------------------------------------- #
def test_weekly_grams_is_seven_times_daily(conn):
    c = Customer.create("Jamie", weight=65, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert target.weekly_grams == pytest.approx(target.daily_grams * DAYS_PER_WEEK)
    assert target.weekly_grams == pytest.approx(
        kg_to_lb(65) * DEFAULT_PROTEIN_FACTOR * 7, abs=1e-9
    )


# --------------------------------------------------------------------------- #
# Result carries its unit -- never a bare number
# --------------------------------------------------------------------------- #
def test_result_carries_unit_strings(conn):
    c = Customer.create("Jamie", weight=65, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert isinstance(target, ProteinTarget)
    assert target.daily_unit == GRAMS_PER_DAY == "g/day"
    assert target.weekly_unit == GRAMS_PER_WEEK == "g/week"
    assert target.daily_unit != target.weekly_unit


# --------------------------------------------------------------------------- #
# protein_factor is per-client and editable, not baked in
# --------------------------------------------------------------------------- #
def test_default_protein_factor_is_the_bottom_of_the_prescribed_band(conn):
    # GFP-132: 0.8 g per POUND of desired weight, the conservative end of the
    # nutritionist's 0.8-1.0 range. Was 1.6 g/kg, which is 0.73 g/lb -- below
    # her floor, so every client was under-fed.
    assert DEFAULT_PROTEIN_FACTOR == 0.8
    c = Customer.create("Jamie", weight=100, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert target.daily_grams == pytest.approx(kg_to_lb(100) * 0.8, abs=1e-9)


def test_per_client_protein_factor_changes_the_target(conn):
    # protein_factor lives on the Customer row (GFP-28) and is editable per
    # client -- two clients with the same weight but different factors must
    # get different targets, with no code change.
    high = Customer.create("High Factor", weight=100, weight_unit="kg", protein_factor=1.0)
    low = Customer.create("Low Factor", weight=100, weight_unit="kg", protein_factor=0.8)
    pounds = kg_to_lb(100)
    assert protein_target_for(high, conn=conn).daily_grams == pytest.approx(pounds * 1.0, abs=1e-9)
    assert protein_target_for(low, conn=conn).daily_grams == pytest.approx(pounds * 0.8, abs=1e-9)


def test_protein_factor_never_hard_coded_a_saved_formula_overrides_it(conn):
    # A nutritionist can replace the whole calculation via the formulas
    # engine (grocery_planner/formulas.py), same mechanism
    # grocery_planner/service/deals.py's score_deals uses for user formulas.
    # If the multiplier were hard-coded in targets.py, this formula override
    # would have no effect.
    formulas.set_formula(conn, FORMULA_NAME, "weight_kg * protein_factor + 20")
    c = Customer.create("Jamie", weight=65, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert target.daily_grams == pytest.approx(65 * DEFAULT_PROTEIN_FACTOR + 20, abs=1e-9)


def test_saved_formula_can_reference_profile_values(conn):
    # The formula context merges in the profile table too (like score_deals
    # does), so a practice-wide setting is expressible without touching every
    # client record.
    conn.execute("INSERT INTO profile(key, value) VALUES ('bonus_grams', '15')")
    conn.commit()
    formulas.set_formula(conn, FORMULA_NAME, "weight_kg * protein_factor + bonus_grams")
    c = Customer.create("Jamie", weight=65, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert target.daily_grams == pytest.approx(65 * DEFAULT_PROTEIN_FACTOR + 15, abs=1e-9)


def test_falls_back_to_default_expression_without_a_saved_formula(conn):
    assert conn.execute(
        "SELECT COUNT(*) FROM formulas WHERE name=?", (FORMULA_NAME,)
    ).fetchone()[0] == 0
    c = Customer.create("Jamie", weight=65, weight_unit="kg")
    target = protein_target_for(c, conn=conn)
    assert target.daily_grams == pytest.approx(
        kg_to_lb(65) * DEFAULT_PROTEIN_FACTOR, abs=1e-9
    )
    # GFP-132: pounds of DESIRED weight, not kilograms of current.
    assert DEFAULT_FORMULA_EXPRESSION == "desired_weight_lb * protein_factor"


# --------------------------------------------------------------------------- #
# Never computed from a raw, unspecified-unit weight
# --------------------------------------------------------------------------- #
def test_protein_target_for_only_reads_weight_kg_not_weight_display(conn):
    # A customer entered in lb has a weight_display of ~90 (their pounds
    # echoed back) but a weight_kg of ~40.8 -- the target must come from the
    # latter. This pins the exact bug the ticket describes: computing from
    # whatever number is closest to hand (90) instead of the canonical kg
    # value would silently 2.2x every lb client's target.
    c = Customer.create("Jamie", weight=90, weight_unit="lb")
    assert c.weight_display == pytest.approx(90, abs=1e-6)
    target = protein_target_for(c, conn=conn)
    # The canonical kilograms, converted to pounds -- NOT weight_display,
    # which for a kg client would be a different number entirely.
    assert target.daily_grams == pytest.approx(
        kg_to_lb(c.weight_kg) * DEFAULT_PROTEIN_FACTOR, abs=1e-9
    )


# --------------------------------------------------------------------------- #
# protein_target(customer_id) end-to-end via the repository
# --------------------------------------------------------------------------- #
def test_protein_target_by_id_matches_protein_target_for(conn):
    saved = CustomerRepository.save(
        Customer.create("Jamie", weight=90, weight_unit="lb"), conn=conn
    )
    by_id = protein_target(saved.id, conn=conn)
    by_customer = protein_target_for(saved, conn=conn)
    assert by_id == by_customer


def test_protein_target_defaults_to_db_connect(monkeypatch, tmp_path):
    # No conn passed -> falls back to db.connect(), matching the
    # customers.py / service/deals.py / nutrition.py convention.
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "default.sqlite3"))
    assert protein_target(999) is None
