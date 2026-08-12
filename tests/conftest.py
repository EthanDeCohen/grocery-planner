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
