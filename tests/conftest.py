"""Shared pytest fixtures: isolated DBs and generated sample CSV data."""
from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from grocery_planner import db

DEALS_HEADER = ("item_name,sub_category,deal_type,deal_description,regular_price,"
                "sale_price,discount_amount,discount_percent,valid_from,valid_to,"
                "loyalty_required,notes")
PRICES_HEADER = ("item_name,brand,category,regular_price,sale_price,unit,"
                 "price_per_unit,on_sale,loyalty_required,date_collected,notes")

SAMPLE = {
    "foodlion": {
        "deals": [
            "Boneless Chicken Breast,Meat & Seafood,Weekly Ad,$1.99/lb,,1.99,,,2026-06-10,2026-06-16,Y,",
            "Gala Apples,Produce,Weekly Ad,$0.99/lb,,0.99,,,2026-06-10,2026-06-16,Y,",
            "Mystery Flyer Item,Weekly Ad Feature (price not listed),Weekly Ad (price not listed),Weekly ad item,,,,,2026-06-10,2026-06-16,Y,price_missing=true",
        ],
        "prices": [
            "Whole Milk,Food Lion,Dairy,3.49,,gallon,3.49,N,N,2026-06-10,",
        ],
    },
    "wholefoods": {
        "deals": [
            "Wild Salmon,Meat & Seafood,Weekly Ad,$9.99/lb,,9.99,,,2026-06-10,2026-06-16,N,",
        ],
        "prices": [
            "Organic Eggs,365,Dairy,4.99,3.99,dozen,3.99,Y,N,2026-06-10,on sale",
        ],
    },
}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.sqlite3")
    yield c
    c.close()


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    """Point the CLI/default connection at an isolated DB via the env override."""
    p = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(p))
    return p


@pytest.fixture
def window(env_db, monkeypatch):
    """A MainWindow over an isolated DB, rendered offscreen.

    Lives here rather than in test_gui.py because GFP-41 gave it a second
    consumer. Every Qt import is INSIDE the body on purpose: conftest is
    imported for the whole suite, and the GUI is an optional extra that CI does
    not install (it runs ``.[dev]``), so a module-level PySide6 import here
    would fail collection for tests that never touch Qt.
    """
    pytest.importorskip("PySide6", reason="GUI extra not installed")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from grocery_planner.gui import app as gui_app

    app = QApplication.instance() or QApplication([])
    win = gui_app.MainWindow()
    yield win
    win.close()
    app.processEvents()


@pytest.fixture
def sample_data(tmp_path) -> Path:
    """Build a data/<store>/{deals,prices}.csv tree and return the data dir."""
    root = tmp_path / "data"
    for store, files in SAMPLE.items():
        folder = root / store
        folder.mkdir(parents=True)
        (folder / "deals.csv").write_text(
            DEALS_HEADER + "\n" + "\n".join(files["deals"]) + "\n", encoding="utf-8")
        (folder / "prices.csv").write_text(
            PRICES_HEADER + "\n" + "\n".join(files["prices"]) + "\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# GFP-263/GFP-267: the suite pays no production pacing, and reaches no network.
#
# Both autouse, because the failure they prevent is one nobody notices:
#
# 1. `retry.Paced` sleeps for real. The scrapers pace themselves deliberately
#    (0.5 s between product pages) and tests that drive a scrape inherited it,
#    so the suite spent whole seconds per test doing nothing -- three tests
#    alone cost 9 s, and the full run went from ~256 s to over 600 s. Pacing is
#    production behaviour; in a test it is dead time.
#
# 2. Nothing in the suite may touch a live host. Every scraper test today
#    injects a fake transport, but "today" is the operative word: one live call
#    added later would hammer a real retailer on every CI run and every local
#    `pytest`, and the symptom would be the 403 wall the pacer exists to avoid
#    -- appearing in someone's test run, far from the code that caused it.
#    A test that needs the network must say so with @pytest.mark.live.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _pacing_is_instant_in_tests(monkeypatch):
    """Keep the pacer's arithmetic, drop its wall-clock cost.

    Patches the module rather than every construction site, which works only
    because `Paced` resolves `time.sleep` at call time. Tests that pass their
    own fake clock (test_pacing.py) are unaffected -- they never reach this.
    """
    from grocery_planner.scrapers import retry as _retry

    monkeypatch.setattr(_retry.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _no_network_in_tests(request, monkeypatch):
    """Fail loudly on any real HTTP call, rather than quietly making one."""
    if request.node.get_closest_marker("live"):
        return

    import httpx

    def _blocked(*_args, **_kwargs):
        raise RuntimeError(
            "This test attempted a real HTTP request. Tests must inject a "
            "transport or fixture instead; mark it @pytest.mark.live if a live "
            "call is genuinely the point. See conftest.py."
        )

    # Blocked at the TRANSPORT, not at the client. Tests legitimately build a
    # real httpx.Client around httpx.MockTransport -- that is the recommended
    # way to test an httpx caller offline, and patching Client.request/send
    # would break every one of them while proving nothing. Only the transport
    # that opens an actual socket is stubbed out.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked, raising=False)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _blocked, raising=False
    )
    monkeypatch.setattr(urllib.request, "urlopen", _blocked, raising=False)


# --------------------------------------------------------------------------- #
# THE SCRAPER REGISTRY, DECLARED ONCE AND BY HAND (GFP-303)
#
# This list is hand-maintained ON PURPOSE. Two tests assert exact set-equality
# against it, so a store cannot enter `SCRAPERS` without a human writing its key
# down here. Do NOT "fix" that by deriving this from `SCRAPERS` -- the
# assertion would become `SCRAPERS == SCRAPERS` and would prove nothing. The
# deliberate declaration IS the safety rail.
#
# What changed in GFP-303 is only that the declaration lived in TWO files
# (tests/test_scraper.py and tests/test_service.py), so adding one store meant
# editing two lists. On 2026-08-14 GFP-293 went red on one, was fixed, then went
# red again on the other -- the same omission found one file at a time, each
# costing a full ~14-minute CI cycle. One list, still manual.
#
# tests/test_scrape_limit.py deliberately does NOT use this: it spot-checks
# membership in a list that is *derived* from the registry, which is the right
# shape for a fact about code (which scrapers accept `--limit`) as opposed to a
# decision a human makes (which stores exist at all).
#
# WHY THERE ARE MORE KEYS THAN STORES -- the accumulated history, which is the
# other half of this list's value:
#
# * GFP-4    'wholefoods' joined the two original Flipp-sourced stores. It is
#            registered but not READY without a hand-minted session cookie,
#            which is why all_scrapers() and available_scrapers() differ.
# * GFP-98   'harristeeter-api' is the Kroger shelf-price API for the SAME
#            physical store as the 'harristeeter' Flipp weekly ad. Two entries,
#            one shop, on purpose.
# * GFP-265  'sprouts-storefront' is that pattern again -- and the cautionary
#            tale. The Instacart storefront client had been SILENTLY SHADOWED
#            by the Flipp 'sprouts' banner, because
#            `SCRAPERS.update(flipp_banners.MODULES)` is last-write-wins and the
#            hand-written module reused the banner's key. It cost a live
#            debugging session. 'aldi-storefront' collides the same way.
#            'traderjoes' is the one with NO collision, which is why it carries
#            no SCRAPER_KEY of its own.
# * GFP-270  'walmart' is a genuinely NEW shop, the first source reaching a
#            chain GFP-197 filed as unreachable. 'publix-catalog' is not new --
#            it is a second feed for the 'publix' banner, and needs its own
#            SCRAPER_KEY for the same last-write-wins reason. Neither is ready
#            without a Parse.bot key.
# * GFP-293  'publix-storefront' is a THIRD feed for one shop: the Flipp banner,
#            the Parse.bot catalogue, and the Instacart white-label. Publix
#            holds the record. It is the only one of the three needing no
#            credential, so it is the only one also in available_scrapers().
# --------------------------------------------------------------------------- #
KNOWN_SCRAPER_KEYS = {
    "acme", "aldi", "aldi-storefront", "foodlion", "foodlion-catalog",
    "giant", "giant-ad", "harristeeter", "harristeeter-api", "hmart",
    "lidl", "lidl-catalogue", "lowesfoods", "publix", "publix-catalog",
    "publix-storefront", "sprouts", "sprouts-storefront", "target",
    "traderjoes", "walmart", "wegmans", "wegmans-api", "weis", "wholefoods",
}
