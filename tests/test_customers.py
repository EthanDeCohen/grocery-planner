"""Tests for grocery_planner.customers (GFP-28): the customer domain.

Covers the lb/kg conversion boundary explicitly (the "2.2x bug" the ticket
is about) and the delete-safety (soft-delete) behaviour, plus the standard
CRUD paths through CustomerRepository.
"""
from __future__ import annotations

import pytest

from grocery_planner.customers import (
    Customer,
    CustomerRepository,
    from_kg,
    kg_to_lb,
    lb_to_kg,
    to_kg,
)

# --------------------------------------------------------------------------- #
# Unit conversion -- the lb/kg boundary
# --------------------------------------------------------------------------- #
def test_lb_to_kg_known_value():
    # 90 lb is the exact example from the ticket: at weight * 1.6, 90 kg
    # (mistakenly stored un-converted) gives 144 g/day of protein instead of
    # the correct ~65 g/day -- the 2.2x bug this schema exists to prevent.
    assert lb_to_kg(90) == pytest.approx(40.8233133, abs=1e-6)


def test_kg_to_lb_known_value():
    assert kg_to_lb(1) == pytest.approx(2.2046226, abs=1e-6)


def test_lb_kg_round_trip_is_exact_at_the_boundary():
    for pounds in (0, 1, 90, 90.5, 250, 0.1):
        assert kg_to_lb(lb_to_kg(pounds)) == pytest.approx(pounds, abs=1e-9)
    for kilograms in (0, 1, 40.8233, 113.4, 0.1):
        assert lb_to_kg(kg_to_lb(kilograms)) == pytest.approx(kilograms, abs=1e-9)


def test_to_kg_passes_kg_through_unchanged():
    assert to_kg(90, "kg") == 90
    assert to_kg(90, "KG") == 90  # case-insensitive
    assert to_kg(90, " kg ") == 90  # whitespace-tolerant


def test_to_kg_converts_lb():
    assert to_kg(90, "lb") == pytest.approx(40.8233133, abs=1e-6)
    assert to_kg(90, "LB") == pytest.approx(40.8233133, abs=1e-6)


def test_to_kg_rejects_unknown_unit():
    # No silent default to either unit -- a wrong default here is exactly
    # the 2.2x dosing bug this module exists to prevent.
    with pytest.raises(ValueError):
        to_kg(90, "pounds")
    with pytest.raises(ValueError):
        to_kg(90, "")
    with pytest.raises(ValueError):
        to_kg(90, "stone")


def test_from_kg_is_the_inverse_of_to_kg():
    kg = to_kg(90, "lb")
    assert from_kg(kg, "lb") == pytest.approx(90, abs=1e-9)
    assert from_kg(kg, "kg") == kg


def test_from_kg_rejects_unknown_unit():
    with pytest.raises(ValueError):
        from_kg(40.8, "pounds")


# --------------------------------------------------------------------------- #
# Customer.create -- the safe construction path
# --------------------------------------------------------------------------- #
def test_create_converts_entered_weight_to_canonical_kg():
    c = Customer.create("Jamie", weight=90, weight_unit="lb")
    assert c.weight_kg == pytest.approx(40.8233133, abs=1e-6)
    assert c.weight_unit == "lb"


def test_create_stores_kg_input_unchanged():
    c = Customer.create("Jamie", weight=65, weight_unit="kg")
    assert c.weight_kg == 65
    assert c.weight_unit == "kg"


def test_create_requires_unit_alongside_weight():
    with pytest.raises(ValueError):
        Customer.create("Jamie", weight=90)


def test_create_rejects_both_weight_and_weight_kg():
    with pytest.raises(ValueError):
        Customer.create("Jamie", weight=90, weight_unit="lb", weight_kg=40.8)


def test_create_with_no_weight_leaves_it_none():
    c = Customer.create("Jamie")
    assert c.weight_kg is None
    assert c.weight_unit is None


def test_create_defaults_protein_factor():
    c = Customer.create("Jamie")
    assert c.protein_factor == 1.6


def test_weight_display_echoes_original_unit():
    # The whole point: a nutritionist who typed pounds sees pounds back,
    # not a silently-converted kilogram figure.
    c = Customer.create("Jamie", weight=90, weight_unit="lb")
    assert c.weight_display == pytest.approx(90, abs=1e-9)

    c_kg = Customer.create("Alex", weight=65, weight_unit="kg")
    assert c_kg.weight_display == 65


def test_weight_display_is_none_without_a_unit():
    c = Customer(id=None, name="Jamie", weight_kg=40.8)
    assert c.weight_display is None


# --------------------------------------------------------------------------- #
# CustomerRepository -- save/get/list
# --------------------------------------------------------------------------- #
def test_save_inserts_and_returns_id(conn):
    c = Customer.create("Jamie", weight=90, weight_unit="lb", height_cm=180)
    saved = CustomerRepository.save(c, conn=conn)
    assert saved.id is not None
    assert saved.created_at is not None
    assert saved.updated_at is not None
    # Original object is untouched.
    assert c.id is None


def test_save_persists_canonical_kg_not_the_entered_pounds(conn):
    c = Customer.create("Jamie", weight=90, weight_unit="lb")
    saved = CustomerRepository.save(c, conn=conn)
    row = conn.execute(
        "SELECT weight_kg, weight_unit FROM customers WHERE id=?", (saved.id,)
    ).fetchone()
    assert row["weight_kg"] == pytest.approx(40.8233133, abs=1e-6)
    assert row["weight_unit"] == "lb"


def test_save_rejects_bad_weight_unit_before_writing(conn):
    c = Customer(id=None, name="Jamie", weight_kg=40.8, weight_unit="stone")
    with pytest.raises(ValueError):
        CustomerRepository.save(c, conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0


def test_save_updates_existing_customer(conn):
    from dataclasses import replace

    saved = CustomerRepository.save(Customer.create("Jamie", weight=90, weight_unit="lb"), conn=conn)
    updated = CustomerRepository.save(replace(saved, goal="cut"), conn=conn)
    assert updated.id == saved.id
    assert updated.goal == "cut"

    fetched = CustomerRepository.get(saved.id, conn=conn)
    assert fetched.goal == "cut"


def test_get_returns_none_for_missing_id(conn):
    assert CustomerRepository.get(999, conn=conn) is None


def test_get_defaults_to_including_deleted(conn):
    saved = CustomerRepository.save(Customer.create("Jamie"), conn=conn)
    CustomerRepository.delete(saved.id, conn=conn)
    assert CustomerRepository.get(saved.id, conn=conn) is not None
    assert CustomerRepository.get(saved.id, include_deleted=False, conn=conn) is None


def test_list_orders_by_name(conn):
    for name in ("Zoe", "Amir", "Mira"):
        CustomerRepository.save(Customer.create(name), conn=conn)
    names = [c.name for c in CustomerRepository.list(conn=conn)]
    assert names == ["Amir", "Mira", "Zoe"]


def test_list_search_filters_by_name(conn):
    CustomerRepository.save(Customer.create("Jamie Smith"), conn=conn)
    CustomerRepository.save(Customer.create("Alex Jones"), conn=conn)
    results = CustomerRepository.list(search="smith", conn=conn)
    assert [c.name for c in results] == ["Jamie Smith"]


def test_list_search_escapes_wildcards(conn):
    CustomerRepository.save(Customer.create("100% Fitness"), conn=conn)
    CustomerRepository.save(Customer.create("Someone Else"), conn=conn)
    # A literal '%' in the search term must not match everything.
    results = CustomerRepository.list(search="100%", conn=conn)
    assert [c.name for c in results] == ["100% Fitness"]


def test_list_defaults_to_db_connect(monkeypatch, tmp_path):
    # No conn passed -> falls back to db.connect(), matching the
    # service/deals.py and nutrition.py convention.
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "default.sqlite3"))
    assert CustomerRepository.list() == []


# --------------------------------------------------------------------------- #
# Deletion safety
# --------------------------------------------------------------------------- #
def test_delete_is_soft_row_survives_in_the_database(conn):
    saved = CustomerRepository.save(Customer.create("Jamie", weight=90, weight_unit="lb"), conn=conn)
    result = CustomerRepository.delete(saved.id, conn=conn)
    assert result is True

    row = conn.execute("SELECT * FROM customers WHERE id=?", (saved.id,)).fetchone()
    assert row is not None
    assert row["deleted_at"] is not None
    # The irreplaceable data itself -- name, weight -- is still intact.
    assert row["name"] == "Jamie"
    assert row["weight_kg"] == pytest.approx(40.8233133, abs=1e-6)


def test_deleted_customer_is_excluded_from_list_by_default(conn):
    saved = CustomerRepository.save(Customer.create("Jamie"), conn=conn)
    CustomerRepository.delete(saved.id, conn=conn)
    assert CustomerRepository.list(conn=conn) == []
    assert [c.id for c in CustomerRepository.list(include_deleted=True, conn=conn)] == [saved.id]


def test_delete_returns_false_for_missing_id(conn):
    assert CustomerRepository.delete(999, conn=conn) is False


def test_delete_is_idempotent(conn):
    saved = CustomerRepository.save(Customer.create("Jamie"), conn=conn)
    first = CustomerRepository.delete(saved.id, conn=conn)
    second = CustomerRepository.delete(saved.id, conn=conn)
    assert first is True
    assert second is False  # already deleted -- no-op, not an error

    # The original deleted_at timestamp was not clobbered by the second call.
    row = conn.execute("SELECT deleted_at FROM customers WHERE id=?", (saved.id,)).fetchone()
    assert row["deleted_at"] is not None


def test_restore_undoes_a_delete(conn):
    saved = CustomerRepository.save(Customer.create("Jamie"), conn=conn)
    CustomerRepository.delete(saved.id, conn=conn)
    assert CustomerRepository.restore(saved.id, conn=conn) is True

    fetched = CustomerRepository.get(saved.id, include_deleted=False, conn=conn)
    assert fetched is not None
    assert fetched.deleted_at is None
    assert fetched.is_deleted is False


def test_restore_returns_false_when_not_deleted(conn):
    saved = CustomerRepository.save(Customer.create("Jamie"), conn=conn)
    assert CustomerRepository.restore(saved.id, conn=conn) is False


def test_is_deleted_property(conn):
    saved = CustomerRepository.save(Customer.create("Jamie"), conn=conn)
    assert saved.is_deleted is False
    CustomerRepository.delete(saved.id, conn=conn)
    fetched = CustomerRepository.get(saved.id, conn=conn)
    assert fetched.is_deleted is True
