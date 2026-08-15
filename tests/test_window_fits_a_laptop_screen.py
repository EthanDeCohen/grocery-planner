"""The main window must fit an ordinary laptop screen (GFP-316).

This is a SIZE test rather than a layout test, and the distinction matters.
Nothing about the client page looked wrong in isolation -- every pane rendered,
every test passed. What broke was arithmetic nobody was doing: the preference
list builds one checkbox per distinct food category, categories come from the
data, and adding stores grew the list 9 -> 201. At 201 the panel was 3,129 px
tall, and because ClientDetailPage shares a QStackedWidget with the roster --
whose minimum is the max over ALL pages, shown or not -- the whole window
inherited a minimum of 1950 x 3461.

The user-visible symptom was not "the list is long". It was "the window will
not resize, and only looks right maximised", reported from the roster view,
which is three widgets away from the cause.

So the guard is the relationship the bug broke: however many categories the
data contains, the window must still fit on a screen. Seeding far more
categories than any real database has is the point -- a fixture with six would
pass no matter how badly this regressed.
"""
from __future__ import annotations

import pytest

#: A 1366x768 laptop, less the taskbar. Deliberately the SMALL common size
#: rather than the reporter's 1536x816: a window that fits this fits that.
SCREEN_W, SCREEN_H = 1366, 728

#: Comfortably more than any real database. 201 is what shipped and broke.
SEEDED_CATEGORIES = 400


@pytest.fixture
def crowded_window(env_db, monkeypatch):
    """A MainWindow whose database has an absurd number of food categories."""
    pytest.importorskip("PySide6", reason="GUI extra not installed")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from grocery_planner import db
    from grocery_planner.gui import app as gui_app

    conn = db.connect()
    conn.executemany(
        "INSERT INTO foods (name, category, source) VALUES (?, ?, 'test')",
        [(f"food {i}", f"Category Number {i}") for i in range(SEEDED_CATEGORIES)],
    )
    conn.commit()

    app = QApplication.instance() or QApplication([])
    win = gui_app.MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def test_the_window_fits_a_laptop_screen_however_many_categories_exist(crowded_window):
    from grocery_planner import db, nutrition

    offered = nutrition.list_categories(db.connect())
    assert len(offered) >= SEEDED_CATEGORIES, "the seed did not take"

    hint = crowded_window.minimumSizeHint()
    floor = crowded_window.minimumSize()
    width = max(hint.width(), floor.width())
    height = max(hint.height(), floor.height())

    assert width <= SCREEN_W and height <= SCREEN_H, (
        f"the window cannot be made smaller than {width}x{height}, which does "
        f"not fit a {SCREEN_W}x{SCREEN_H} screen. Something in the client page "
        f"is setting a floor it should be scrolling instead -- {len(offered)} "
        f"food categories are loaded."
    )


def test_the_preference_list_scrolls_rather_than_growing(crowded_window):
    """The specific mechanism, so a failure says which panel gave way."""
    panel = crowded_window.client_page.selection_panel
    hint = panel.minimumSizeHint()
    floor = panel.minimumSize()
    height = max(hint.height(), floor.height())

    assert height <= 500, (
        f"SelectionPanel wants {height} px. Its checkbox grid has to live in a "
        f"QScrollArea -- the list length is a function of how much data is "
        f"loaded, so any fixed allowance for it is a bug waiting on a new store."
    )
    assert panel.categories_scroll.widget() is panel.categories_widget
