"""Tests for the shared front-end-agnostic service layer (GFP-14)."""
import pytest

from grocery_planner import service


def test_available_scrapers_lists_known_stores():
    scrapers = service.available_scrapers()
    assert scrapers == sorted(scrapers)
    assert {"foodlion", "harristeeter"} <= set(scrapers)


def test_run_scrape_unknown_store_raises(conn):
    with pytest.raises(service.UnknownStoreError):
        service.run_scrape("wholefoods", conn=conn)


def test_fetch_deals_empty_and_filtered(conn):
    assert service.fetch_deals(conn=conn) == []

    conn.execute(
        "INSERT INTO deals(store, item_name, sale_price, source) "
        "VALUES ('foodlion', 'Milk', 2.5, 'scrape')"
    )
    conn.commit()

    assert len(service.fetch_deals(conn=conn)) == 1
    assert len(service.fetch_deals(store="foodlion", conn=conn)) == 1
    assert service.fetch_deals(store="harristeeter", conn=conn) == []


# --------------------------------------------------------------------------- #
# GFP-16 — deal freshness
# --------------------------------------------------------------------------- #
TODAY = "2026-06-12"


def _seed_deals(conn):
    conn.executemany(
        "INSERT INTO deals(store, item_name, sale_price, valid_to, source) "
        "VALUES ('foodlion', ?, ?, ?, 'scrape')",
        [
            ("Fresh Chicken", 1.99, "2026-06-16"),   # still valid
            ("Ends Today", 0.99, TODAY),             # valid through today
            ("Stale Apples", 0.79, "2026-06-09"),    # expired
            ("Undated Feature", None, None),         # no end date -> unknown
        ],
    )
    conn.commit()


def test_fetch_deals_flags_expired_rows(conn):
    _seed_deals(conn)
    flags = {r["item_name"]: r["expired"] for r in service.fetch_deals(today=TODAY, conn=conn)}
    assert flags == {
        "Fresh Chicken": 0,
        "Ends Today": 0,      # a deal is good through its last day
        "Stale Apples": 1,
        "Undated Feature": 0,  # unknown is not expired
    }


def test_fetch_deals_hide_expired_and_count_agree(conn):
    _seed_deals(conn)
    kept = [r["item_name"] for r in
            service.fetch_deals(hide_expired=True, today=TODAY, conn=conn)]
    assert "Stale Apples" not in kept
    assert len(kept) == 3
    assert service.count_deals(hide_expired=True, today=TODAY, conn=conn) == 3
    assert service.count_deals(today=TODAY, conn=conn) == 4


def test_fetch_deals_on_sale_filter(conn):
    _seed_deals(conn)
    names = [r["item_name"] for r in service.fetch_deals(on_sale=True, today=TODAY, conn=conn)]
    assert "Undated Feature" not in names
    assert service.count_deals(on_sale=True, hide_expired=True, today=TODAY, conn=conn) == 2


def test_count_deals_limit_is_ignored_by_count(conn):
    _seed_deals(conn)
    assert len(service.fetch_deals(limit=2, today=TODAY, conn=conn)) == 2
    assert service.count_deals(today=TODAY, conn=conn) == 4


def test_is_expired_helper():
    assert service.is_expired("2026-06-09", TODAY)
    assert not service.is_expired(TODAY, TODAY)
    assert not service.is_expired("", TODAY)
    assert not service.is_expired(None, TODAY)
