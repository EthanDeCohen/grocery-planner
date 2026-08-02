"""Tests for durable price records (GFP-75).

The property that matters most here is the one the whole ticket exists for:
a record must survive the deletion of the ``price_history`` rows it was
derived from. ``test_records_survive_history_being_pruned`` is that test, and
it is the one that would fail if someone later "simplified" records into a
query over history.
"""
from __future__ import annotations

from datetime import date

import pytest

from grocery_planner import db, records


def _row(name, price, **extra):
    row = {
        "item_name": name,
        "sub_category": None,
        "deal_type": "weekly",
        "regular_price": None,
        "sale_price": None,
        "dollar_price": price,
        "discount_amount": None,
        "discount_percent": None,
    }
    row.update(extra)
    return row


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "records.sqlite3"))
    c = db.connect()
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# observed_price -- what counts as an observation at all
# --------------------------------------------------------------------------- #
def test_observed_price_prefers_dollar_price_then_sale_then_regular():
    assert records.observed_price({"dollar_price": 1.0, "sale_price": 2.0}) == 1.0
    assert records.observed_price({"sale_price": 2.0, "regular_price": 3.0}) == 2.0
    assert records.observed_price({"regular_price": 3.0}) == 3.0


def test_observed_price_rejects_zero_and_negative():
    # A zero would otherwise become an unbeatable, entirely fictional record
    # low that no later real price could ever displace.
    assert records.observed_price({"dollar_price": 0}) is None
    assert records.observed_price({"dollar_price": -1}) is None


def test_observed_price_is_none_when_nothing_is_readable():
    assert records.observed_price({"item_name": "Mystery Item"}) is None
    assert records.observed_price({"dollar_price": "not a number"}) is None


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def test_first_observation_creates_a_record(conn):
    summary = records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-01")
    assert summary["created"] == 1
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.record_low_price == 4.99
    assert r.record_high_price == 4.99
    assert r.first_seen_at == r.last_seen_at == "2026-08-01"
    assert r.observations == 1


def test_a_lower_price_moves_the_record_and_its_date(conn):
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-01")
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 3.49)], "2026-08-08")
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.record_low_price == 3.49
    assert r.record_low_price_at == "2026-08-08"
    assert r.record_high_price == 4.99
    assert r.record_high_price_at == "2026-08-01"
    assert r.observations == 2


def test_a_higher_price_does_not_disturb_the_low(conn):
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 3.49)], "2026-08-01")
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 5.99)], "2026-08-08")
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.record_low_price == 3.49
    assert r.record_low_price_at == "2026-08-01"
    assert r.record_high_price == 5.99


def test_matching_the_record_keeps_the_original_date(conn):
    # Strictness matters: if a tie moved the date forward, an item sitting at
    # its usual price would appear to set a new record every single week and
    # "record low, first seen today" would stop carrying information.
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 3.49)], "2026-08-01")
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 3.49)], "2026-08-08")
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.record_low_price == 3.49
    assert r.record_low_price_at == "2026-08-01"


def test_unpriced_rows_contribute_nothing(conn):
    records.update_records(conn, "foodlion", "27401", [_row("Mystery Item", None)], "2026-08-01")
    assert records.fetch_records(conn, order_by="price") == []


def test_same_item_twice_in_one_scrape_takes_the_best_and_worst(conn):
    # A weekly-ad row and a coupon row for the same product both arrive in one
    # scrape; the cheaper must win rather than whichever happens to be last.
    summary = records.update_records(
        conn, "foodlion", "27401",
        [_row("Pork Chops", 5.99), _row("Pork Chops", 3.49, deal_type="coupon")],
        "2026-08-01",
    )
    assert summary["created"] == 1
    (r,) = records.fetch_records(conn, order_by="price")
    assert (r.record_low_price, r.record_high_price) == (3.49, 5.99)


def test_records_are_scoped_per_store_and_per_zip(conn):
    # A record low is a price and prices are per-location -- the GFP-76 spike
    # measured the same SKU at $5.39/lb and $4.79/lb in different ZIPs.
    # Pooling them would report a price the customer's store never offered.
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-01")
    records.update_records(conn, "foodlion", "90210", [_row("Pork Chops", 2.99)], "2026-08-01")
    records.update_records(conn, "harristeeter", "27401", [_row("Pork Chops", 6.99)], "2026-08-01")
    assert len(records.fetch_records(conn, order_by="price")) == 3
    (one,) = records.fetch_records(conn, store="foodlion", postal_code="27401", order_by="price")
    assert one.record_low_price == 4.99


def test_is_thin_flags_a_record_built_on_too_little(conn):
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-01")
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.is_thin is True
    for day in ("2026-08-08", "2026-08-15"):
        records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], day)
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.observations == 3
    assert r.is_thin is False


# --------------------------------------------------------------------------- #
# THE POINT OF THE TICKET
# --------------------------------------------------------------------------- #
def test_records_survive_history_being_pruned(conn):
    """The whole reason records are stored rather than derived (GFP-42).

    If this ever fails, someone has turned records back into a query over
    ``price_history`` and the all-time low will silently vanish the first time
    retention runs -- unrecoverably, because the observation is gone.
    """
    conn.execute(
        "INSERT INTO price_history(store, postal_code, item_name, deal_type, "
        "dollar_price, captured_at) VALUES ('foodlion','27401','Pork Chops','weekly',3.49,'2026-01-01')"
    )
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 3.49)], "2026-01-01")
    conn.commit()

    # Retention prunes everything. This is GFP-42 doing its job.
    conn.execute("DELETE FROM price_history")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 0

    (r,) = records.fetch_records(conn, order_by="price")
    assert r.record_low_price == 3.49
    assert r.record_low_price_at == "2026-01-01"


# --------------------------------------------------------------------------- #
# Backfill
# --------------------------------------------------------------------------- #
def _seed_history(conn, rows):
    conn.executemany(
        "INSERT INTO price_history(store, postal_code, item_name, deal_type, "
        "dollar_price, captured_at) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


def test_backfill_reads_existing_history(conn):
    _seed_history(conn, [
        ("foodlion", "27401", "Pork Chops", "weekly", 4.99, "2026-07-01"),
        ("foodlion", "27401", "Pork Chops", "weekly", 3.49, "2026-07-08"),
        ("foodlion", "27401", "Pork Chops", "weekly", 5.99, "2026-07-15"),
    ])
    totals = records.backfill_from_history(conn)
    assert totals["days"] == 3
    (r,) = records.fetch_records(conn, order_by="price")
    assert (r.record_low_price, r.record_low_price_at) == (3.49, "2026-07-08")
    assert (r.record_high_price, r.record_high_price_at) == (5.99, "2026-07-15")


def test_backfill_attributes_records_to_the_right_day_not_replay_order(conn):
    # Inserted newest-first on purpose: the backfill must sort by captured_at,
    # or the record's date is whichever row the database happened to hand back
    # first -- a value that is right with a date that is wrong.
    _seed_history(conn, [
        ("foodlion", "27401", "Pork Chops", "weekly", 3.49, "2026-07-08"),
        ("foodlion", "27401", "Pork Chops", "weekly", 4.99, "2026-07-01"),
    ])
    records.backfill_from_history(conn)
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.record_low_price_at == "2026-07-08"
    assert r.first_seen_at == "2026-07-01"


def test_backfill_is_idempotent(conn):
    _seed_history(conn, [
        ("foodlion", "27401", "Pork Chops", "weekly", 3.49, "2026-07-01"),
    ])
    records.backfill_from_history(conn)
    first = records.fetch_records(conn, order_by="price")[0]
    records.backfill_from_history(conn)
    second = records.fetch_records(conn, order_by="price")[0]
    assert first.record_low_price == second.record_low_price
    assert first.record_low_price_at == second.record_low_price_at
    # Including the counter: a replay must not inflate `observations` and
    # thereby launder a one-sighting record into an authoritative-looking one.
    assert first.observations == second.observations == 1


def test_rescraping_the_same_day_is_not_a_second_observation(conn):
    # Mirrors price_history's per-calendar-day grain (GFP-39): running a
    # scrape twice in one day updates today's figure, it does not fabricate a
    # second data point out of nothing.
    for _ in range(3):
        records.update_records(
            conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-01"
        )
    (r,) = records.fetch_records(conn, order_by="price")
    assert r.observations == 1
    assert r.is_thin is True


def test_an_out_of_order_older_observation_extends_first_seen(conn):
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-08")
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 5.99)], "2026-07-01")
    (r,) = records.fetch_records(conn, order_by="price")
    # first_seen must reach back to the older day, and last_seen must NOT be
    # dragged backwards by it.
    assert r.first_seen_at == "2026-07-01"
    assert r.last_seen_at == "2026-08-08"


# --------------------------------------------------------------------------- #
# Rolling windows (derived, not stored)
# --------------------------------------------------------------------------- #
def test_rolling_window_only_sees_inside_the_window(conn):
    _seed_history(conn, [
        ("foodlion", "27401", "Pork Chops", "weekly", 1.99, "2026-01-01"),  # old & cheap
        ("foodlion", "27401", "Pork Chops", "weekly", 4.99, "2026-07-20"),
        ("foodlion", "27401", "Pork Chops", "weekly", 5.99, "2026-08-01"),
    ])
    w = records.rolling_window(
        conn, "foodlion", "27401", "Pork Chops", days=30, today=date(2026, 8, 2)
    )
    # The January bargain is the all-time low but must NOT be the 30-day low --
    # that is the entire point of having a window: it shows price creep that
    # an all-time low hides.
    assert w.low_price == 4.99
    assert w.high_price == 5.99
    assert w.observations == 2


def test_rolling_window_reports_emptiness_rather_than_faking_a_number(conn):
    w = records.rolling_window(
        conn, "foodlion", "27401", "Nothing Here", days=30, today=date(2026, 8, 2)
    )
    assert w.observations == 0
    assert w.is_complete is False
    assert w.low_price is None


def test_retention_floor_covers_the_longest_window():
    # GFP-42 must not prune below this, or a "90-day low" is quietly computed
    # from fewer than 90 days.
    assert records.RETENTION_FLOOR_DAYS >= max(records.ROLLING_WINDOWS_DAYS)


# --------------------------------------------------------------------------- #
# fetch_records
# --------------------------------------------------------------------------- #
def test_fetch_records_rejects_an_unknown_ordering(conn):
    with pytest.raises(ValueError, match="unknown order_by"):
        records.fetch_records(conn, order_by="vibes")


def test_fetch_by_cpgp_excludes_items_with_no_protein_cost(conn):
    # There is no honest ranking position for an item whose protein cost was
    # never computable, so it is dropped rather than sorted as if it were free.
    records.update_records(conn, "foodlion", "27401", [_row("Paper Towels", 4.99)], "2026-08-01")
    assert records.fetch_records(conn, order_by="cpgp") == []
    assert len(records.fetch_records(conn, order_by="price")) == 1


def test_count_records_summarises(conn):
    records.update_records(conn, "foodlion", "27401", [_row("Pork Chops", 4.99)], "2026-08-01")
    summary = records.count_records(conn)
    assert summary["items"] == 1
    assert summary["established"] == 0
