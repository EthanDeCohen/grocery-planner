"""GFP-165: the Flipp banners found by the 2026-08-09 market survey.

Registered from a table rather than as eleven near-identical modules -- see
``flipp_banners`` for why. These tests hold the table to the same contract a
hand-written module satisfies, because the registry cannot tell them apart and
neither should anything downstream.
"""
from __future__ import annotations

import pytest

from grocery_planner import scrapers
from grocery_planner.scrapers import base, flipp_banners
from grocery_planner.service import ingest
from grocery_planner.stores import BY_KEY


def test_every_banner_is_registered():
    for banner in flipp_banners.BANNERS:
        assert banner.key in ingest.all_scrapers()


def test_every_banner_satisfies_the_scraper_contract():
    """The registry is duck-typed, so a table row must be indistinguishable
    from a module -- test_scraper.py asserts this surface on every entry."""
    for key, module in flipp_banners.MODULES.items():
        assert module.MERCHANT and module.DEFAULT_POSTAL_CODE
        assert callable(module.scrape)
        assert callable(module.serves)
        assert getattr(module, "SCRAPER_KEY", module.STORE_KEY) == key
        assert scrapers.store_key_for(module) == key
        assert scrapers.source_for(module) == "scrape"


def test_every_banner_has_a_display_name():
    """A deal must be attributable to something a nutritionist can read."""
    for banner in flipp_banners.BANNERS:
        assert BY_KEY[banner.key].display_name == banner.display_name


def test_merchant_strings_are_exactly_as_flipp_labels_them():
    """A near-miss silently matches nothing: Flipp writes "Wegman's" with an
    apostrophe and "Lowes Foods" without one."""
    merchants = {b.config.merchant_name for b in flipp_banners.BANNERS}
    assert "Wegman's" in merchants
    assert "Lowes Foods" in merchants
    assert "Wegmans" not in merchants
    assert "Lowe's Foods" not in merchants


def test_no_banner_collides_with_a_hand_written_module():
    """Registering the table must not silently replace foodlion or giant-ad."""
    hand_written = {"foodlion", "foodlion-catalog", "giant", "giant-ad",
                    "harristeeter", "harristeeter-api", "wholefoods"}
    assert not (set(flipp_banners.MODULES) & hand_written)


def test_poor_value_banners_are_flagged_rather_than_dropped(caplog):
    """Target at 1.6% and Wegmans at 1.9% were measured as poor value. They are
    still registered -- the user asked for them -- but the measurement travels
    with them so the decision is visible rather than silently made here."""
    low = [b for b in flipp_banners.BANNERS
           if b.protein_density < flipp_banners.LOW_DENSITY]
    assert {b.key for b in low} == {"target", "wegmans"}
    for banner in low:
        assert banner.note, f"{banner.key} is low-density and says nothing about it"


def test_a_banner_asks_flipp_rather_than_declaring_a_footprint(monkeypatch):
    """GFP-257: the same rule the hand-written modules follow. A declared
    prefix list was measurably wrong for Food Lion."""
    for module in flipp_banners.MODULES.values():
        assert not hasattr(module, "SERVICE_AREA")

    seen = {}

    def _fake(config, postal_code):
        seen["merchant"] = config.merchant_name
        return True

    monkeypatch.setattr(base, "serves_postal_code", _fake)
    assert flipp_banners.MODULES["publix"].serves("27401") is True
    assert seen["merchant"] == "Publix"


@pytest.mark.parametrize("key", [b.key for b in flipp_banners.BANNERS])
def test_a_banner_scrapes_through_the_shared_flipp_client(key, monkeypatch):
    """No per-store branching: every banner reaches base.scrape_store."""
    called = {}

    def _fake(config, postal_code=None, include_coupons=True):
        called["key"] = config.key
        return [], {"id": 1}, {"total": 0}

    monkeypatch.setattr(base, "scrape_store", _fake)
    flipp_banners.MODULES[key].scrape(postal_code="27401")
    assert called["key"] == key
