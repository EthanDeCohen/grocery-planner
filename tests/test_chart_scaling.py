"""The trend chart has to grow with the window, and stay legible (GFP-353).

The reported symptom was "the app needs to scale better, the gui charts don't
always fully show". The measurement behind it: widening the window from 920 to
1366 gave the chart **zero** extra pixels -- all 446 went to the two forms
beside it -- so it sat at 288px at every laptop size with the labels saying
which line is which store elided away. It only became readable above roughly
1900px wide.

Every test here asserts a RELATIONSHIP rather than a pixel count, because the
numbers move with the font and the platform. A test pinning 358px would fail on
a machine with a different default font while the layout was perfectly fine,
and would pass on a machine where the labels were clipped.
"""
from __future__ import annotations

import pytest

#: The smallest common laptop, less the taskbar -- the same screen
#: test_window_fits_a_laptop_screen.py uses, for the same reason.
SCREEN_W, SCREEN_H = 1366, 728


@pytest.fixture
def client_page(window):
    """The client page, shown, so its layout is real rather than pending."""
    pytest.importorskip("PySide6", reason="GUI extra not installed")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    # SHOWN, deliberately. The shared `window` fixture does not show the
    # window, and an unshown window is never laid out -- every widget reports
    # its size HINT and resize() changes nothing, so a layout test against it
    # measures arithmetic that never reaches a user.
    window.show()
    page = window.client_page
    window.stack.setCurrentWidget(page)
    app.processEvents()
    return window, page, app


def _label_room(chart) -> tuple[int, int]:
    """(room the chart leaves for a label, width a label needs).

    Mirrors the arithmetic in TrendChart.paintEvent, which is the thing that
    actually decides whether a label is drawn or elided to nothing.
    """
    from PySide6.QtGui import QFontMetrics

    from grocery_planner.gui import trends

    metrics = QFontMetrics(chart.font())
    needed = metrics.horizontalAdvance(trends._LABEL_REFERENCE)
    plot_right = max(
        chart.width() - needed - trends._LABEL_GAP * 2,
        trends._MARGIN_LEFT + trends._MIN_PLOT_WIDTH,
    )
    room = max(0, chart.width() - int(plot_right + trends._LABEL_GAP))
    return room, needed


def test_the_chart_can_label_itself_on_a_laptop_screen(client_page):
    """The regression. At 1366x728 there must be room for a store name.

    A chart whose lines are unlabelled does not answer the question it exists
    to answer -- which store is cheaper -- so this is not a cosmetic bound.
    """
    window, page, app = client_page
    window.resize(SCREEN_W, SCREEN_H)
    app.processEvents()

    room, needed = _label_room(page.trend.chart)
    assert room >= needed, (
        f"the chart is {page.trend.chart.width()}px wide and leaves {room}px "
        f"for a store label needing {needed}px -- the labels will be elided"
    )


#: The least share of newly-available width the chart must receive.
#:
#: A bare "it got wider" assertion does NOT catch the reported bug: on the
#: broken layout the chart still crept from 288px to 383px between a 1366 and a
#: 1920 window -- 17% of the 554px gained, while the two forms took the rest.
#: Measured after the fix it takes about 70%. A quarter is comfortably clear of
#: the broken behaviour and comfortably below the fixed behaviour, so this
#: discriminates rather than merely passing.
MIN_SHARE_OF_NEW_WIDTH = 0.25


def test_the_chart_takes_a_real_share_of_extra_width(client_page):
    """The complaint, stated as a relationship that actually discriminates.

    The chart is the pane that needs width most -- a squeezed time series is
    unreadable in a way a squeezed form is not -- so it must be a major
    beneficiary of a bigger window, not a rounding error in one.
    """
    window, page, app = client_page

    window.resize(SCREEN_W, SCREEN_H)
    app.processEvents()
    narrow = page.trend.chart.width()

    window.resize(1920, 1040)
    app.processEvents()
    wide = page.trend.chart.width()

    gained, available = wide - narrow, 1920 - SCREEN_W
    share = gained / available
    assert share >= MIN_SHARE_OF_NEW_WIDTH, (
        f"the window gained {available}px and the chart took {gained}px "
        f"({share:.0%}) -- the panes beside it are absorbing the width again"
    )


def test_the_forms_do_not_absorb_the_extra_width(client_page):
    """The mechanism, guarded directly.

    If either wrapped column goes back to Expanding horizontally it will eat
    the growth again, and the test above would still pass for a while on a
    wide enough screen before quietly regressing.
    """
    from PySide6.QtWidgets import QSizePolicy

    _, page, _ = client_page
    for name, scroll in (
        ("biometrics", page.biometrics_scroll),
        ("selection", page.selection_scroll),
    ):
        assert scroll.sizePolicy().horizontalPolicy() != QSizePolicy.Expanding, (
            f"the {name} column expands horizontally again, so it will take "
            "the width the chart needs"
        )


def test_the_chart_minimum_does_not_depend_on_the_data(client_page):
    """GFP-316's lesson, applied to the fix for this ticket.

    Sizing the chart from the longest store name actually on it would be the
    obvious "improvement" and would reintroduce a minimum size that grows with
    the database -- which is how the window once became taller than any
    screen. Long names must elide instead.
    """
    from grocery_planner import service

    _, page, app = client_page
    chart = page.trend.chart
    before = chart.minimumWidth()

    absurd = "A Grocery Store With A Preposterously Long Name" * 3
    trend = service.PriceTrend(days=service.DEFAULT_WINDOW_DAYS)
    chart.set_trend(trend)
    app.processEvents()
    # Also exercise the label path directly, in case set_trend later starts
    # measuring what it is given.
    chart.setToolTip(absurd)
    app.processEvents()

    assert chart.minimumWidth() == before, (
        "the chart's minimum width changed with its contents -- a data-driven "
        "minimum is what made the window taller than the screen in GFP-316"
    )


def test_the_window_still_fits_the_laptop_after_widening_the_chart(client_page):
    """Giving the chart a floor raises the window's minimum. It must not raise
    it past the screen this app is meant to run on."""
    window, _, _ = client_page
    assert window.minimumWidth() <= SCREEN_W, (
        f"the window now demands {window.minimumWidth()}px, more than a "
        f"{SCREEN_W}px laptop screen"
    )
    assert window.minimumHeight() <= SCREEN_H
