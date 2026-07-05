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
