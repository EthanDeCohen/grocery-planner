"""Tests for grocery_planner.preferences (GFP-30): per-client protein
category preferences.

Covers the two things the ticket cares most about getting right:

* the category list is data-driven (nutrition.list_categories()), not a
  hard-coded set -- a category only usable because it exists as data;
* zero preferences means "unconstrained" (rank across everything), not "an
  empty basket" -- this is what GFP-49's unconstrained baseline relies on.

Plus the standard replace-all CRUD path through set_preferences/
list_preferences.
"""
from __future__ import annotations

import pytest

from grocery_planner import preferences
from grocery_planner.customers import Customer, CustomerRepository


def _make_customer(conn, name="Jamie") -> int:
    saved = CustomerRepository.save(Customer.create(name), conn=conn)
    return saved.id


# --------------------------------------------------------------------------- #
# Zero preferences == unconstrained, not "prefers nothing"
# --------------------------------------------------------------------------- #
def test_new_customer_has_no_preferences_recorded(conn):
    customer_id = _make_customer(conn)
    assert preferences.list_preferences(customer_id, conn=conn) == []


def test_clearing_preferences_returns_to_the_unconstrained_empty_state(conn):
    customer_id = _make_customer(conn)
    preferences.set_preferences(customer_id, ["chicken", "fish"], conn=conn)
    assert preferences.list_preferences(customer_id, conn=conn) == ["chicken", "fish"]

    preferences.set_preferences(customer_id, [], conn=conn)
    assert preferences.list_preferences(customer_id, conn=conn) == []


# --------------------------------------------------------------------------- #
# set_preferences / list_preferences -- replace-all CRUD
# --------------------------------------------------------------------------- #
def test_set_preferences_persists_and_returns_sorted_categories(conn):
    customer_id = _make_customer(conn)
    result = preferences.set_preferences(
        customer_id, ["whey", "beef", "tofu"], conn=conn
    )
    assert result == ["beef", "tofu", "whey"]
    assert preferences.list_preferences(customer_id, conn=conn) == [
        "beef", "tofu", "whey",
    ]


def test_set_preferences_replaces_the_previous_set(conn):
    customer_id = _make_customer(conn)
    preferences.set_preferences(customer_id, ["beef", "pork"], conn=conn)
    preferences.set_preferences(customer_id, ["fish"], conn=conn)
    assert preferences.list_preferences(customer_id, conn=conn) == ["fish"]


def test_set_preferences_deduplicates(conn):
    customer_id = _make_customer(conn)
    result = preferences.set_preferences(
        customer_id, ["chicken", "chicken", "beef"], conn=conn
    )
    assert result == ["beef", "chicken"]
    assert preferences.list_preferences(customer_id, conn=conn) == ["beef", "chicken"]


def test_set_preferences_rejects_unknown_category(conn):
    customer_id = _make_customer(conn)
    with pytest.raises(ValueError):
        preferences.set_preferences(customer_id, ["beef", "unicorn"], conn=conn)
    # Nothing was written -- an invalid call must not leave a partial set.
    assert preferences.list_preferences(customer_id, conn=conn) == []


def test_set_preferences_rejection_does_not_clobber_existing_valid_set(conn):
    customer_id = _make_customer(conn)
    preferences.set_preferences(customer_id, ["beef"], conn=conn)
    with pytest.raises(ValueError):
        preferences.set_preferences(customer_id, ["unicorn"], conn=conn)
    assert preferences.list_preferences(customer_id, conn=conn) == ["beef"]


def test_preferences_are_per_customer(conn):
    jamie = _make_customer(conn, "Jamie")
    alex = _make_customer(conn, "Alex")
    preferences.set_preferences(jamie, ["beef"], conn=conn)
    preferences.set_preferences(alex, ["fish", "tofu"], conn=conn)

    assert preferences.list_preferences(jamie, conn=conn) == ["beef"]
    assert preferences.list_preferences(alex, conn=conn) == ["fish", "tofu"]


def test_deleting_customer_row_cascades_preferences(conn):
    # Note: this exercises a hard DELETE of the customers row directly, not
    # CustomerRepository.delete() (which is a soft delete and does NOT
    # remove the row -- see grocery_planner/customers.py). The cascade only
    # fires when the row itself is actually gone.
    customer_id = _make_customer(conn)
    preferences.set_preferences(customer_id, ["beef", "chicken"], conn=conn)

    conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
    conn.commit()

    row = conn.execute(
        "SELECT COUNT(*) FROM customer_protein_preferences WHERE customer_id=?",
        (customer_id,),
    ).fetchone()
    assert row[0] == 0


def test_soft_deleting_customer_does_not_touch_preferences(conn):
    customer_id = _make_customer(conn)
    preferences.set_preferences(customer_id, ["beef"], conn=conn)
    CustomerRepository.delete(customer_id, conn=conn)

    assert preferences.list_preferences(customer_id, conn=conn) == ["beef"]


# --------------------------------------------------------------------------- #
# Data-driven category list -- no hard-coded categories in this module
# --------------------------------------------------------------------------- #
def test_all_six_v1_categories_are_accepted(conn):
    customer_id = _make_customer(conn)
    six = ["beef", "pork", "chicken", "fish", "tofu", "whey"]
    assert preferences.set_preferences(customer_id, six, conn=conn) == sorted(six)


def test_a_new_category_becomes_valid_once_it_exists_as_data(conn):
    # No code change here -- just a new foods row -- is what should be
    # required to make a 7th category selectable (the ticket's "a seventh
    # category must be a row of data, never a code change").
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref) "
        "VALUES ('Lentils, raw', 'legume', 'curated', 'legume-test')"
    )
    conn.commit()

    customer_id = _make_customer(conn)
    result = preferences.set_preferences(customer_id, ["legume"], conn=conn)
    assert result == ["legume"]


def test_set_preferences_defaults_to_db_connect(monkeypatch, tmp_path):
    # No conn passed -> falls back to db.connect(), matching the
    # service/deals.py / nutrition.py / customers.py convention.
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "default.sqlite3"))
    from grocery_planner import db

    own = db.connect()
    customer_id = _make_customer(own)
    own.close()

    result = preferences.set_preferences(customer_id, ["beef"])
    assert result == ["beef"]
    assert preferences.list_preferences(customer_id) == ["beef"]
