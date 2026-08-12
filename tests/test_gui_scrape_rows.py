"""GFP-103: concurrent scrapes stack instead of erasing each other.

Reported from live use: starting Food Lion, then Harris Teeter, made the Food
Lion row vanish; then harristeeter-api replaced harristeeter, then Whole Foods
replaced that. Only one run was ever visible, and the replaced worker was left
unreferenced while still executing.

These tests drive the dialog without touching the network: ``start()`` is
exercised directly where a real thread is wanted, and the completion handlers
are called directly where the point is attribution rather than threading.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from grocery_planner.gui.scrape import ScrapeRow


@pytest.fixture
def window(env_db, monkeypatch):
    """A MainWindow over an isolated DB, rendered offscreen.

    Local rather than shared: PR #53 (GFP-41) moves this fixture into
    conftest.py, and defining it there here too would collide on merge.
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from grocery_planner.gui import app as gui_app

    app = QApplication.instance() or QApplication([])
    win = gui_app.MainWindow()
    yield win
    win.close()
    app.processEvents()


@pytest.fixture
def dialog(window):
    """The scrape dialog, joined on teardown.

    Tests that call start() spawn real QThreads. Qt aborts the process if one
    is destroyed while running, so teardown joins them rather than racing the
    window's destruction — the same reason ScrapeDialog.wait_for_runs exists.
    """
    dlg = window.open_scrape()
    yield dlg
    dlg.wait_for_runs()


def _rows_in_order(dialog) -> list[str]:
    """Store keys in the order their rows appear in the layout, top-down."""
    keys = []
    for i in range(dialog.rows_layout.count()):
        item = dialog.rows_layout.itemAt(i)
        widget = item.widget() if item else None
        if isinstance(widget, ScrapeRow):
            keys.append(widget.store_key)
    return keys


# --------------------------------------------------------------------------- #
# The reported bug
# --------------------------------------------------------------------------- #
def test_a_second_scrape_appends_a_row_instead_of_replacing_the_first(dialog):
    """The exact reported symptom, as an assertion."""
    dialog._row_for("foodlion")
    dialog._row_for("harristeeter")
    dialog._row_for("harristeeter-api")
    dialog._row_for("wholefoods")

    assert _rows_in_order(dialog) == [
        "foodlion", "harristeeter", "harristeeter-api", "wholefoods",
    ]
    assert len(dialog._rows) == 4


def test_a_finished_row_keeps_its_result_while_others_still_run(dialog):
    dialog._row_for("foodlion")
    dialog._row_for("harristeeter")
    dialog._on_done("foodlion", {"stats": {"total": 5, "weekly_ad": 5,
                                           "digital_coupons": 0}})

    assert "Stored 5 deals" in dialog._rows["foodlion"].status.text()
    assert not dialog._rows["foodlion"].running
    # The other row is untouched -- no shared label to clobber.
    assert dialog._rows["harristeeter"].status.text() == "Scraping…"


def test_results_go_to_the_store_they_belong_to_not_the_most_recent(dialog):
    """Out-of-order completion must not misattribute numbers to a store.

    The old dialog wrote every result into one shared label, so a slow first
    scrape finishing after a fast second one reported its totals under the
    second store's name.
    """
    dialog._row_for("foodlion")
    dialog._row_for("harristeeter")

    # harristeeter (started second) finishes first, then foodlion.
    dialog._on_done("harristeeter", {"stats": {"total": 900, "weekly_ad": 900,
                                               "digital_coupons": 0}})
    dialog._on_done("foodlion", {"stats": {"total": 1, "weekly_ad": 1,
                                           "digital_coupons": 0}})

    assert "900" in dialog._rows["harristeeter"].status.text()
    assert "900" not in dialog._rows["foodlion"].status.text()
    assert "Stored 1 deals" in dialog._rows["foodlion"].status.text()


def test_a_failure_lands_on_its_own_row_only(dialog):
    dialog._row_for("foodlion")
    dialog._row_for("wholefoods")
    dialog._on_failed("wholefoods", "session expired")

    assert "session expired" in dialog._rows["wholefoods"].status.text()
    assert "session expired" not in dialog._rows["foodlion"].status.text()


# --------------------------------------------------------------------------- #
# Worker lifetime -- the non-cosmetic half of the bug
# --------------------------------------------------------------------------- #
def test_a_running_worker_is_held_for_the_whole_run(dialog, monkeypatch):
    """ScrapeWorker has no Qt parent, so losing the reference is a crash risk.

    The old code assigned to a single ``self._worker``; a second scrape dropped
    the first worker's only reference while its run() was still executing.
    """
    import threading

    import grocery_planner.gui.scrape as scrape_module

    # Block inside the worker so both runs are genuinely in flight at once --
    # which is the state the old code could not survive.
    release = threading.Event()
    entered = threading.Barrier(3, timeout=10)

    def blocking(store_key, force=False):
        entered.wait()
        release.wait(timeout=10)
        raise RuntimeError("stopped on purpose")

    monkeypatch.setattr(scrape_module.jobs, "run_tracked_scrape", blocking)

    assert dialog.start("foodlion") is True
    assert dialog.start("harristeeter") is True
    entered.wait()                       # both workers are now inside run()

    try:
        # Neither displaced the other, and each is a DISTINCT live worker --
        # the old single `self._worker` attribute could hold only one.
        assert set(dialog._runs) == {"foodlion", "harristeeter"}
        workers = [worker for _thread, worker in dialog._runs.values()]
        assert len({id(w) for w in workers}) == 2
        assert {w.store_key for w in workers} == {"foodlion", "harristeeter"}
        assert set(dialog._rows) == {"foodlion", "harristeeter"}
    finally:
        release.set()
        dialog.wait_for_runs()


def test_a_finished_run_is_released_so_the_dict_cannot_grow_without_bound(dialog):
    dialog._row_for("foodlion")
    dialog._runs["foodlion"] = (None, None)
    dialog._on_done("foodlion", {"stats": {}})
    assert "foodlion" not in dialog._runs
    # ...but its row stays, because the user still wants to read the result.
    assert "foodlion" in dialog._rows


# --------------------------------------------------------------------------- #
# Per-store guarding, not whole-dialog disabling
# --------------------------------------------------------------------------- #
def test_the_same_store_cannot_be_started_twice(dialog):
    dialog._runs["foodlion"] = (None, None)
    assert dialog.start("foodlion") is False


def test_a_running_store_does_not_block_a_different_store(dialog):
    """Several stores at once is the whole point of the redesign."""
    dialog._runs["foodlion"] = (None, None)
    dialog.store_box.setCurrentIndex(dialog.store_box.findData("harristeeter"))
    assert dialog.scrape_btn.isEnabled()


def test_selecting_a_running_store_disables_its_button(dialog):
    """The old guard was defeated by changing the picker; this one is not."""
    dialog._runs["foodlion"] = (None, None)
    dialog._sync_buttons()
    dialog.store_box.setCurrentIndex(dialog.store_box.findData("foodlion"))
    assert not dialog.scrape_btn.isEnabled()


def test_rerunning_a_store_reuses_its_row_rather_than_stacking_duplicates(dialog):
    dialog._row_for("foodlion")
    dialog._on_done("foodlion", {"stats": {"total": 3, "weekly_ad": 3,
                                           "digital_coupons": 0}})
    row = dialog._row_for("foodlion")          # a second run of the same store

    assert _rows_in_order(dialog) == ["foodlion"]
    assert row.status.text() == "Scraping…"    # reset, not left showing the old result
    assert row.running


# --------------------------------------------------------------------------- #
# Scrape all
# --------------------------------------------------------------------------- #
def test_scrape_all_starts_every_ready_store(dialog, monkeypatch):
    import grocery_planner.gui.scrape as scrape_module

    monkeypatch.setattr(scrape_module.jobs, "run_tracked_scrape",
                        lambda store_key, force=False: (_ for _ in ()).throw(
                            RuntimeError("stopped on purpose")))
    dialog.on_scrape_all()

    from grocery_planner import service
    # GFP-257: "every ready store" became "every ready store that serves this
    # ZIP". Scrape all must agree with the dropdown above it, or the button
    # quietly means something different from the list.
    assert set(dialog._rows) == set(service.scrapers_for_postal_code().keys)


def test_scrape_all_skips_stores_already_running_rather_than_failing(dialog):
    from grocery_planner import service

    for key in service.available_scrapers():
        dialog._runs[key] = (None, None)
    dialog.on_scrape_all()
    assert "already scraping" in dialog.message.text()


def test_scrape_all_is_not_hardcoded_to_a_store_count(dialog):
    """GFP-32's spirit: the resolver stays the source of truth, not a count.

    GFP-257 moved that source from `available_scrapers()` (ready) to
    `scrapers_for_postal_code()` (ready AND serving this ZIP). The rule is
    unchanged -- nothing here may assume how many stores there are.
    """
    from grocery_planner import service

    offered = [dialog.store_box.itemData(i) for i in range(dialog.store_box.count())]
    assert offered == list(service.scrapers_for_postal_code().keys)
