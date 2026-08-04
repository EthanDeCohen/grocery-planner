"""Price history stops growing without bound (GFP-42).

**Measured before the number was chosen**, rather than guessed: price history
costs about 411 bytes per row including indexes, at roughly 1,600 rows per day
for two stores. That is ~239 MB at 365 days against a ~500 MB budget, which
would be reached somewhere near 763 days. Adding stores scales rows-per-day
linearly, so that is the figure to re-measure -- not the byte count.

**Retention and the chart ranges are one decision, not two.** The selector
offers 1/3/7/30/365 days, and the longest of those is exactly what retention
promises to keep. Two independently-set numbers would drift the moment somebody
changed one, and the failure mode is not an error -- it is a confidently
labelled axis drawn over history that was deleted.

**Records survive pruning, deliberately.** GFP-75 stores record lows rather
than recomputing them precisely so "cheapest ever seen" outlives the
observation behind it.
"""
from __future__ import annotations

import pytest

from grocery_planner import config, records
from datetime import date, timedelta


def _observe(conn, item, days_ago, price=5.00, store="foodlion", zip_code="27401"):
    when = (date.today() - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO price_history(store, item_name, dollar_price, captured_at, "
        "postal_code) VALUES (?,?,?,?,?)",
        (store, item, price, f"{when}T12:00:00", zip_code),
    )
    conn.commit()


def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"]


# --------------------------------------------------------------------------- #
# Pruning
# --------------------------------------------------------------------------- #
def test_history_past_the_window_is_deleted(conn):
    _observe(conn, "Old Chicken", days_ago=400)
    _observe(conn, "New Chicken", days_ago=5)

    removed = records.prune_history(conn, days=365)
    assert removed == 1
    assert _count(conn) == 1


def test_history_inside_the_window_is_kept(conn):
    for age in (0, 1, 30, 364):
        _observe(conn, f"Chicken {age}", days_ago=age)
    assert records.prune_history(conn, days=365) == 0
    assert _count(conn) == 4


def test_pruning_is_idempotent(conn):
    """It runs after every scrape, so a second call must be a cheap no-op
    rather than something that keeps finding work."""
    _observe(conn, "Old", days_ago=400)
    assert records.prune_history(conn, days=365) == 1
    assert records.prune_history(conn, days=365) == 0
    assert records.prune_history(conn, days=365) == 0


def test_the_boundary_day_is_kept(conn):
    """Exactly at the window edge is inside it. An off-by-one here silently
    shortens every axis by a day."""
    _observe(conn, "Edge", days_ago=365)
    assert records.prune_history(conn, days=365) == 0
    assert _count(conn) == 1


def test_pruning_defaults_to_the_configured_window(conn, monkeypatch, tmp_path):
    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GROCERY_PLANNER_HISTORY_RETENTION_DAYS", "100")
    _observe(conn, "Older than 100", days_ago=150)
    _observe(conn, "Newer than 100", days_ago=50)

    assert records.prune_history(conn) == 1
    assert _count(conn) == 1


def test_records_outlive_the_history_they_came_from(conn):
    """GFP-75's whole reason for storing rather than recomputing: a record low
    must not disappear because the observation behind it aged out."""
    _observe(conn, "Chicken Breast 16 oz", days_ago=400, price=1.00)
    records.update_records(
        conn, "foodlion", "27401",
        [{"item_name": "Chicken Breast 16 oz", "dollar_price": 1.00}],
        date.today() - timedelta(days=400),
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) c FROM price_records").fetchone()["c"]
    assert before >= 1

    records.prune_history(conn, days=365)

    after = conn.execute("SELECT COUNT(*) c FROM price_records").fetchone()["c"]
    assert after == before, "pruning history must not touch stored records"


# --------------------------------------------------------------------------- #
# The floor: retention may not undercut what rolling windows need
# --------------------------------------------------------------------------- #
def test_retention_may_not_be_set_below_the_rolling_window_floor():
    """Pruning below it does not fail -- it silently starts answering
    "90-day low" from less than 90 days, which is worse."""
    with pytest.raises(config.SettingError):
        config._retention_days("history_retention_days", records.RETENTION_FLOOR_DAYS - 1)


def test_the_floor_itself_is_allowed():
    assert config._retention_days(
        "history_retention_days", records.RETENTION_FLOOR_DAYS
    ) == records.RETENTION_FLOOR_DAYS


def test_the_default_clears_the_floor():
    assert config.defaults()["history_retention_days"] >= records.RETENTION_FLOOR_DAYS


@pytest.mark.parametrize("bad", [0, -1, "many", ""])
def test_nonsense_retention_is_refused(bad):
    with pytest.raises(config.SettingError):
        config._retention_days("history_retention_days", bad)


# --------------------------------------------------------------------------- #
# The chart may only offer what retention keeps
# --------------------------------------------------------------------------- #
def test_no_range_outlives_retention(monkeypatch, tmp_path):
    """THE COUPLING. An axis labelled "last year" drawn over 90 days of kept
    history is a quiet lie of exactly the kind service/trends.py refuses."""
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui.trends import range_choices

    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(tmp_path / "config.json"))
    for kept in (90, 100, 365, 400):
        monkeypatch.setenv("GROCERY_PLANNER_HISTORY_RETENTION_DAYS", str(kept))
        assert all(days <= kept for days, _ in range_choices()), (
            f"a range longer than {kept} days of retention is on offer"
        )


def test_shortening_retention_removes_the_long_ranges(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui.trends import range_choices

    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GROCERY_PLANNER_HISTORY_RETENTION_DAYS", "365")
    assert 365 in [d for d, _ in range_choices()]

    monkeypatch.setenv("GROCERY_PLANNER_HISTORY_RETENTION_DAYS", "90")
    assert 365 not in [d for d, _ in range_choices()]
    assert 30 in [d for d, _ in range_choices()]


def test_the_selector_is_never_empty(monkeypatch, tmp_path):
    """An empty Range dropdown would be a dead control."""
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui.trends import range_choices

    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GROCERY_PLANNER_HISTORY_RETENTION_DAYS", "90")
    assert range_choices()


def test_a_broken_config_still_yields_ranges(monkeypatch, tmp_path):
    """range_choices backs a widget built at startup; it must not be the thing
    that stops the window opening."""
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui.trends import range_choices

    broken = tmp_path / "config.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(broken))
    assert range_choices()


def test_the_ranges_are_the_ones_asked_for(monkeypatch, tmp_path):
    """1 / 3 / 7 / 30 / 365, stock-market style, main window only."""
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui.trends import range_choices

    monkeypatch.setenv("GROCERY_PLANNER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GROCERY_PLANNER_HISTORY_RETENTION_DAYS", "365")
    assert [d for d, _ in range_choices()] == [1, 3, 7, 30, 365]


def test_the_client_chart_window_is_unchanged():
    """The client page stays as it was -- it answers "what should this person
    eat", not "how has the market moved"."""
    pytest.importorskip("PySide6.QtWidgets")
    from grocery_planner.gui import clienttrend

    assert clienttrend.WINDOW_DAYS == 90
