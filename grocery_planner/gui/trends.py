# ######### decohen-partners ##########
# Protein Ledger
"""Price-trends pane (GFP-36): is protein getting cheaper, and where?

The right-hand pane of the main window. Draws one line per store of the
cheapest $/g protein that store offered each day, over
``service.protein_price_trend``'s window — the numbers come from the
front-end-agnostic core (``service/trends.py``), this module only draws them.

**Painted by hand, not by Qt Charts.** Qt Charts is GPLv3-or-commercial, unlike
the LGPL Qt modules the rest of this app uses; taking that dependency for one
small line chart would put a licence obligation on a product meant to be handed
to nutritionists. A few dozen lines of ``QPainter`` avoid it entirely and keep
the frozen binary smaller.

**Honesty.** The pane never draws a chart it cannot support. Below
``service.MIN_POINTS_TO_PLOT`` days it shows ``PriceTrend.reason`` — which
distinguishes "nothing scraped yet" from "come back tomorrow" — and still lists
whatever latest prices *are* known, because refusing to plot is not a reason to
withhold the data.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .. import service
from ..records import RETENTION_FLOOR_DAYS
from ..stores import STORES

# Categorical slots 1-4 of the house palette, in fixed order, validated for
# this chart form in both modes (adjacent-pair CVD ΔE 9.1 light / 8.4 dark;
# normal-vision 22.9 / 19.8). A slot is tied to a STORE, never to a series'
# rank, so re-sorting the series -- service.protein_price_trend returns the
# cheapest store first -- never repaints a line the reader has already learned.
SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
SERIES_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500")
# Anything past the palette folds into one muted "other" rather than inventing
# a hue; with three registered stores this is a guard, not a live path.
OTHER_LIGHT, OTHER_DARK = "#6b6a66", "#96958d"

LINE_WIDTH = 2
MARKER_RADIUS = 4  # 8px marker, per the mark spec
GRID_LINES = 4

# GFP-41/GFP-42: the ranges the selector offers, stock-market style. Every one
# must be <= the retention window, because an axis labelled "last 365 days"
# drawn over 90 days of retained history is a quiet lie of exactly the kind
# service/trends.py refuses to tell.
#
# DERIVED FROM RETENTION, never set alongside it. Retention is the promise;
# these are what may be shown given that promise. Two independent numbers
# would drift the moment somebody changed one, and the failure would be a
# confidently-labelled axis rather than an error.
_CANDIDATE_RANGES: tuple[tuple[int, str], ...] = (
    (1, "Today"),
    (3, "Last 3 days"),
    (7, "Last 7 days"),
    (30, "Last 30 days"),
    (365, "Last year"),
)


def range_choices() -> tuple[tuple[int, str], ...]:
    """The ranges this install may honestly offer.

    A function rather than a constant because retention is configurable, and a
    module-level tuple would freeze whatever the setting was at import time.
    """
    from .. import config

    try:
        kept = config.history_retention_days()
    except Exception:                       # noqa: BLE001
        # A broken config must not empty the selector; fall back to the floor
        # retention is never allowed below.
        kept = RETENTION_FLOOR_DAYS
    offered = tuple((d, label) for d, label in _CANDIDATE_RANGES if d <= kept)
    # Never nothing: the shortest range is always honest.
    return offered or (_CANDIDATE_RANGES[0],)


def widest_range() -> int:
    """The longest range on offer.

    The store selector is populated from this rather than from the current
    window, so narrowing the range cannot make the store you were looking at
    vanish from the picker you would need to get back to it.
    """
    return max(days for days, _ in range_choices())
#: userData for the "every store" entry. Empty rather than None because
#: QComboBox.currentData() returns None for a missing role, and the two would
#: then be indistinguishable.
ALL_STORES = ""

# GFP-109: two tabs rather than a default plus a hidden toggle. "Animal protein"
# is meat and seafood only; "Overall" is every protein source, exactly as before.
# Both stay first-class, and which question is on screen is legible at a glance --
# a chart silently ranking a subset would be the same class of quiet lie this
# pane already refuses elsewhere.
#
# ANIMAL PROTEIN LEADS AND IS THE DEFAULT, by product decision: meat is what the
# tool is for, and the overall view's honest-but-useless answer (a pancake mix
# won on $/g protein) is exactly what a nutritionist should NOT be shown first.
# Overall stays one click away rather than being removed -- the data was never
# the problem, the ranking was.
TAB_ANIMAL, TAB_OVERALL = 0, 1
TAB_LABELS = ("Animal protein", "Overall protein")
#: Which tab a freshly-opened window lands on.
DEFAULT_TAB = TAB_ANIMAL

#: GFP-110: entries per page in the "Latest known" list. Two is the shape the
#: user saw and asked to keep, and a fixed page keeps the pane a constant height
#: however many stores exist -- the list would otherwise push the chart out as
#: stores and per-client ZIPs (GFP-53) multiply.
LATEST_PAGE_SIZE = 2

_MARGIN_LEFT = 62
_MARGIN_TOP, _MARGIN_BOTTOM = 14, 26
#: Gap between the plot and its endpoint labels, and the least width to leave
#: for them when the widget is too narrow to fit them honestly.
_LABEL_GAP, _MIN_PLOT_WIDTH = 10, 120

#: What the chart's minimum width budgets for a store label (GFP-353).
#:
#: A FIXED string, deliberately, not the longest name actually on the chart.
#: A minimum size computed from data is what GFP-316 was: the preference list
#: grew with the number of sources until the window no longer fitted any
#: screen. A store called something enormous must make its own label elide --
#: which paintEvent already does -- rather than make the window wider.
_LABEL_REFERENCE = "Harris Teeter"


def _spread(positions: list[float], gap: float, top: float, bottom: float) -> list[float]:
    """Nudge endpoint labels apart so near-equal series stay readable.

    Two stores within a fraction of a cent of each other -- which is exactly
    when the comparison matters most -- would otherwise print their labels on
    top of one another. Returns new y values in the same order as the input,
    each at least ``gap`` from its neighbour and inside ``top``/``bottom``.
    """
    order = sorted(range(len(positions)), key=lambda i: positions[i])
    placed = dict.fromkeys(order, 0.0)

    previous = top - gap
    for index in order:                       # push down through the stack
        y = max(positions[index], previous + gap)
        placed[index] = y
        previous = y
    previous = bottom + gap
    for index in reversed(order):             # then back up off the bottom edge
        y = min(placed[index], previous - gap)
        placed[index] = y
        previous = y
    return [placed[i] for i in range(len(positions))]


#: Series that are not stores, and the palette slot each one takes (GFP-144).
#:
#: The client chart's two lines exist to be COMPARED, so they must not be the
#: same colour -- and every non-store key used to fall through to the single
#: muted "other", which drew both of them identically. Registering them here
#: keeps the assignment deterministic and keeps it out of the drawing code,
#: which should not know what a series means.
NON_STORE_SLOTS: dict[str, int] = {
    "Cheapest available": 0,
    "Their plan": 1,
}


def _slot(store_key: str) -> int:
    """This series' fixed palette slot.

    A store's slot is its position in the store registry, so a store keeps its
    colour no matter which other stores are on the chart.
    """
    keys = [s.key for s in STORES]
    if store_key in keys:
        return keys.index(store_key)
    if store_key in NON_STORE_SLOTS:
        return NON_STORE_SLOTS[store_key]
    return len(keys)


def _money_per_gram(value: float) -> str:
    """$/g protein, at the precision the numbers actually differ by."""
    return f"${value:.4f}"


class TrendChart(QWidget):
    """The plot itself: one polyline per store, hairline grid, endpoint labels."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        # GFP-353: a floor on the WIDTH too. Without one this was the only pane
        # on the client page that could be starved -- its two siblings both
        # enforce minimums -- so it was squeezed to 288px at every laptop size
        # and the labels saying which line is which store were elided away.
        self._apply_minimum_width()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._trend = service.PriceTrend(days=service.DEFAULT_WINDOW_DAYS)

    def _apply_minimum_width(self) -> None:
        """The narrowest this chart can be and still say what it is showing.

        Derived rather than hardcoded, so that a user who has turned up their
        system font size gets a wider chart instead of a clipped one -- the
        same reason the rest of the app sizes text off the widget font.
        """
        metrics = QFontMetrics(self.font())
        self.setMinimumWidth(
            _MARGIN_LEFT
            + _MIN_PLOT_WIDTH
            + _LABEL_GAP * 2
            + metrics.horizontalAdvance(_LABEL_REFERENCE)
        )

    def changeEvent(self, event) -> None:  # noqa: N802
        # A font change alters what the labels need, and the floor is expressed
        # in font terms, so it has to be recomputed rather than left at
        # whatever the font was at construction.
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._apply_minimum_width()

    def set_trend(self, trend: service.PriceTrend) -> None:
        self._trend = trend
        self.setToolTip("\n".join(
            f"{s.label}: {_money_per_gram(s.latest.value)}/g — {s.latest.item_name}"
            for s in trend.series if s.latest
        ))
        self.update()

    # ----------------------------------------------------------------- #
    def _is_dark(self) -> bool:
        return self.palette().color(QPalette.Window).lightness() < 128

    def colour_for(self, store_key: str) -> QColor:
        slot = _slot(store_key)
        palette = SERIES_DARK if self._is_dark() else SERIES_LIGHT
        if slot >= len(palette):
            return QColor(OTHER_DARK if self._is_dark() else OTHER_LIGHT)
        return QColor(palette[slot])

    def paintEvent(self, event) -> None:  # noqa: N802
        series = self._trend.plottable
        if not series:
            return  # the pane above says why; an empty box says it better than axes
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        ink = self.palette().color(QPalette.WindowText)
        muted = QColor(ink)
        muted.setAlpha(140)
        grid = QColor(ink)
        grid.setAlpha(38)  # one shade off the surface, solid, never dashed

        metrics = QFontMetrics(painter.font())
        # The endpoint labels are known before any geometry is, so the right
        # margin is measured from them rather than guessed -- a guess clips
        # the longest store name, which is how a chart lies quietly.
        labels = {
            s.key: f"{s.label} {_money_per_gram(s.latest.value)}"
            for s in series
        }
        label_width = max(metrics.horizontalAdvance(text) for text in labels.values())

        plot_left = _MARGIN_LEFT
        plot_right = max(
            self.width() - label_width - _LABEL_GAP * 2, plot_left + _MIN_PLOT_WIDTH
        )
        plot_top = _MARGIN_TOP
        plot_bottom = max(self.height() - _MARGIN_BOTTOM, plot_top + 1)

        days = [date.fromisoformat(p.day) for s in series for p in s.points]
        values = [p.value for s in series for p in s.points]
        first_day, last_day = min(days), max(days)
        span_days = max((last_day - first_day).days, 1)
        low, high = min(values), max(values)
        if high == low:                      # a flat line still deserves a plot
            high, low = high * 1.05, low * 0.95
        pad = (high - low) * 0.08
        low, high = low - pad, high + pad

        def x_of(day: str) -> float:
            offset = (date.fromisoformat(day) - first_day).days
            return plot_left + (plot_right - plot_left) * offset / span_days

        def y_of(value: float) -> float:
            ratio = (value - low) / (high - low)
            return plot_bottom - (plot_bottom - plot_top) * ratio

        # --- grid + value axis ------------------------------------------ #
        painter.setPen(QPen(grid, 1))
        for step in range(GRID_LINES + 1):
            y = plot_top + (plot_bottom - plot_top) * step / GRID_LINES
            painter.drawLine(QPointF(plot_left, y), QPointF(plot_right, y))
        painter.setPen(QPen(muted, 1))
        for step in range(GRID_LINES + 1):
            y = plot_top + (plot_bottom - plot_top) * step / GRID_LINES
            value = high - (high - low) * step / GRID_LINES
            painter.drawText(
                0, int(y - metrics.height() / 2), _MARGIN_LEFT - 8, metrics.height(),
                Qt.AlignRight | Qt.AlignVCenter, _money_per_gram(value),
            )

        # --- date axis: just the ends, so nothing collides -------------- #
        baseline = plot_bottom + metrics.height()
        painter.drawText(int(plot_left), int(baseline), f"{first_day:%b %d}")
        painter.drawText(
            int(plot_right - metrics.horizontalAdvance(f"{last_day:%b %d}")),
            int(baseline), f"{last_day:%b %d}",
        )

        # --- the series -------------------------------------------------- #
        surface = self.palette().color(QPalette.Window)
        for store_trend in series:
            colour = self.colour_for(store_trend.key)
            points = [
                QPointF(x_of(p.day), y_of(p.value))
                for p in store_trend.points
            ]
            painter.setPen(QPen(colour, LINE_WIDTH, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(points)
            # A surface ring, not a border, so overlapping markers separate.
            for point in points:
                painter.setPen(QPen(surface, 2))
                painter.setBrush(colour)
                painter.drawEllipse(point, MARKER_RADIUS, MARKER_RADIUS)
            painter.setBrush(Qt.NoBrush)

        # --- endpoint labels, only ones, spread so they cannot overlap ---- #
        label_y = _spread(
            [y_of(s.latest.value) for s in series],
            metrics.height(), plot_top, plot_bottom,
        )
        painter.setPen(QPen(muted, 1))
        # ELIDE rather than let a label run off the edge (GFP-137). The right
        # margin above is measured from the longest label, but it is floored at
        # _MIN_PLOT_WIDTH so the plot itself stays usable -- which means a
        # narrow column can leave less room than the labels want.
        #
        # Overflowing there prints text that is silently cut mid-word, which is
        # the "chart lying quietly" this module already worries about in the
        # margin comment. An ellipsis says the name is abbreviated; a hard cut
        # does not, and the tooltip carries the full text either way.
        room = max(0, self.width() - int(plot_right + _LABEL_GAP))
        for store_trend, y in zip(series, label_y):
            painter.drawText(
                int(plot_right + _LABEL_GAP),
                int(y + metrics.height() / 3),
                metrics.elidedText(labels[store_trend.key], Qt.ElideRight, room),
            )
        painter.end()


class TrendsPane(QWidget):
    """Chart, selectors, legend and the latest known price — or an honest reason.

    The store and range selectors (GFP-41) narrow ``service.protein_price_trend``
    rather than filtering an already-drawn chart, so what is plotted and what
    ``gplan trends --store X --days N`` prints are the same query.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.title = QLabel("Cheapest protein per day")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)

        # GFP-109. A QTabBar, not a QTabWidget: both tabs draw the SAME chart,
        # legend and list from one query with a single argument changed, so a
        # QTabWidget's second page would either duplicate every widget or hold
        # an empty placeholder. A bar is the honest widget for "one view, two
        # questions".
        self.tabs = QTabBar()
        for label in TAB_LABELS:
            self.tabs.addTab(label)
        self.tabs.setCurrentIndex(DEFAULT_TAB)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # A WIDGET wrapping the row, not a bare layout: GFP-104 has to hide the
        # selectors wholesale on an empty database, and a QLayout cannot be
        # hidden — only the widget holding it can.
        self.selectors = QWidget()
        self.selectors.setLayout(self._build_selectors())
        layout.addWidget(self.selectors)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        self.chart = TrendChart()
        layout.addWidget(self.chart, 1)

        self.legend = QWidget()
        self.legend_layout = QHBoxLayout(self.legend)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.legend)

        # GFP-110: the list pages two at a time, with the arrows beside it
        # rather than below, so a one-page list looks identical to how it did
        # before the arrows existed.
        self.latest_row = QWidget()
        latest_layout = QHBoxLayout(self.latest_row)
        latest_layout.setContentsMargins(0, 0, 0, 0)

        self.prev_btn = QPushButton("◀")
        self.next_btn = QPushButton("▶")
        for button, tip in (
            (self.prev_btn, "Previous stores"), (self.next_btn, "Next stores"),
        ):
            button.setFixedWidth(28)
            button.setToolTip(tip)
            button.setAutoDefault(False)      # must not steal Enter from the roster

        self.latest = QLabel("")
        self.latest.setWordWrap(True)
        self.latest.setTextFormat(Qt.RichText)

        latest_layout.addWidget(self.prev_btn)
        latest_layout.addWidget(self.latest, 1)
        latest_layout.addWidget(self.next_btn)
        layout.addWidget(self.latest_row)

        self.prev_btn.clicked.connect(lambda: self._step_latest(-1))
        self.next_btn.clicked.connect(lambda: self._step_latest(1))

        self._any_history = False   # replaced by the first _refresh_store_choices
        self._has_data = False      # replaced by the first reload()
        self._latest_page = 0
        self.reload()

    # ----------------------------------------------------------------- #
    def _build_selectors(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        self.store_select = QComboBox()
        self.store_select.addItem("All stores", ALL_STORES)
        self.range_select = QComboBox()
        for days, label in range_choices():
            self.range_select.addItem(label, days)
        default = self.range_select.findData(service.DEFAULT_WINDOW_DAYS)
        self.range_select.setCurrentIndex(default if default >= 0 else 0)

        for label, widget in (("Store", self.store_select), ("Range", self.range_select)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
        row.addStretch(1)

        # Connected last, so building the widgets above cannot fire a reload
        # before __init__ has finished creating the chart they would redraw.
        for widget in (self.store_select, self.range_select):
            widget.currentIndexChanged.connect(self.reload)
        return row

    def _refresh_store_choices(self) -> None:
        """Rebuild the store list from data, keeping the user's pick if it survives.

        Repopulating a QComboBox emits currentIndexChanged, which is wired to
        reload() -- so signals are blocked here. Without that, every reload
        would schedule another reload.
        """
        wanted = self.store_select.currentData()
        stores = service.trend_stores(days=widest_range())
        # Remembered so an empty chart can tell "nothing has ever been scraped"
        # apart from "your filter excluded everything" -- see _explain_empty.
        self._any_history = bool(stores)

        self.store_select.blockSignals(True)
        try:
            self.store_select.clear()
            self.store_select.addItem("All stores", ALL_STORES)
            for key, label in stores:
                self.store_select.addItem(label, key)
            found = self.store_select.findData(wanted)
            # A store can leave the list (its history aged out). Falling back to
            # "All stores" beats silently plotting a different store's line
            # under the name the user last chose.
            self.store_select.setCurrentIndex(found if found >= 0 else 0)
        finally:
            self.store_select.blockSignals(False)

    @property
    def selected_store(self) -> str | None:
        """The store filter to pass to the service — ``None`` means every store."""
        return self.store_select.currentData() or None

    @property
    def selected_days(self) -> int:
        return self.range_select.currentData()

    # ----------------------------------------------------------------- #
    @property
    def has_data(self) -> bool:
        """Has this install ever captured a price? Drives the empty state."""
        return self._has_data

    def _show_nothing_yet(self) -> None:
        """GFP-104: on a genuinely empty database, show ONE plain message.

        Reported from first use: the heading, both dropdowns and a "no prices"
        line all sat over an empty pane. Controls that look operable but govern
        nothing are worse than no controls -- a Store dropdown listing no stores
        and a Range dropdown ranging over nothing invite a user to fiddle with
        them instead of doing the one thing that would help.

        Distinct from every other empty case here, all of which are preserved:
        those mean "there IS data, just not this data", so their controls are
        exactly what the user needs.
        """
        self.trend = service.PriceTrend(days=self.selected_days)
        for widget in (self.title, self.tabs, self.selectors,
                       self.chart, self.legend, self.latest_row):
            widget.setVisible(False)
        self.subtitle.setVisible(True)
        self.subtitle.setText(
            "No price data yet.\n\n"
            "Use Data ▸ Run scrape… and press “Scrape all” to fetch this "
            "week's prices. Everything on this page fills in once that finishes."
        )

    def reload(self) -> None:
        self._has_data = service.has_price_history()
        # Refreshed even when hiding everything: the selectors keep their state
        # while invisible, so skipping this would leave a stale store selected
        # and silently reapply it the moment data returned.
        self._refresh_store_choices()
        if not self._has_data:
            self._show_nothing_yet()
            return

        for widget in (self.title, self.tabs, self.selectors):
            widget.setVisible(True)
        trend = service.protein_price_trend(
            days=self.selected_days,
            store=self.selected_store,
            meat_only=self.meat_only,
        )
        self.trend = trend
        self.chart.set_trend(trend)
        self.chart.setVisible(trend.is_plottable)
        self._build_legend(trend)

        if trend.is_plottable:
            # Naming the subset on screen, not just in the tab: a chart that
            # ranks meat while its caption says "protein" is the quiet lie
            # GFP-109 was filed about.
            what = "animal protein" if self.meat_only else "protein"
            self.subtitle.setText(
                f"Best $/g {what} each day, last {trend.days} days. "
                "Lower is better."
            )
        else:
            self.subtitle.setText(self._explain_empty(trend))

        self._render_latest(trend)

    # ----------------------------------------------------------------- #
    # GFP-109 — the tabs
    # ----------------------------------------------------------------- #
    @property
    def meat_only(self) -> bool:
        """Is the Animal-protein tab showing? Drives the service's meat filter."""
        return self.tabs.currentIndex() == TAB_ANIMAL

    def _on_tab_changed(self, _index: int) -> None:
        # The tabs show different series, so a page into the old list means
        # nothing in the new one (GFP-110).
        self._latest_page = 0
        self.reload()

    # ----------------------------------------------------------------- #
    # GFP-110 — the latest-known list, two at a time
    # ----------------------------------------------------------------- #
    def latest_entries(self, trend: service.PriceTrend) -> list[str]:
        """One rendered line per series that has a latest price."""
        return [
            f"<b>{s.label}</b> — {_money_per_gram(s.latest.value)}/g "
            f"<span>({s.latest.item_name})</span>"
            for s in trend.series if s.latest
        ]

    def page_count(self, total: int) -> int:
        return max(1, -(-total // LATEST_PAGE_SIZE))   # ceiling division

    def _step_latest(self, direction: int) -> None:
        entries = self.latest_entries(self.trend)
        pages = self.page_count(len(entries))
        self._latest_page = max(0, min(self._latest_page + direction, pages - 1))
        self._render_latest(self.trend)

    def _render_latest(self, trend: service.PriceTrend) -> None:
        """Shown whether or not the chart draws.

        It is the relief for the two light-mode series colours that sit under
        3:1, and the only content at all when there is not yet enough history
        to plot — so it survives every empty case except a wholly empty
        database (GFP-104).
        """
        entries = self.latest_entries(trend)
        pages = self.page_count(len(entries))
        # Clamped rather than reset: a reload that drops a store should not
        # throw the user back to page 1, but a page that no longer exists must
        # not render blank either.
        self._latest_page = max(0, min(self._latest_page, pages - 1))

        start = self._latest_page * LATEST_PAGE_SIZE
        shown = entries[start:start + LATEST_PAGE_SIZE]

        heading = "Latest known:"
        if pages > 1:
            heading += f" <span>({self._latest_page + 1}/{pages})</span>"
        self.latest.setText(("<br>".join([heading, *shown])) if shown else "")

        # Hidden, not merely disabled, when everything fits: a control that can
        # never do anything is what GFP-104 just removed from this pane. At the
        # ends they are DISABLED rather than hidden, so the arrows do not jump
        # around under the cursor mid-use.
        paged = pages > 1
        for button in (self.prev_btn, self.next_btn):
            button.setVisible(paged)
        self.prev_btn.setEnabled(paged and self._latest_page > 0)
        self.next_btn.setEnabled(paged and self._latest_page < pages - 1)
        self.latest_row.setVisible(bool(shown))
        self.latest.setVisible(bool(shown))

    def _explain_empty(self, trend: service.PriceTrend) -> str:
        """Why the chart is blank — the selectors made this ambiguous.

        ``PriceTrend.reason`` says "run a scrape", which is right for an empty
        database and wrong the moment a filter is what emptied the result: the
        data exists, the user just excluded it. Telling someone to scrape when
        they only need to widen a dropdown sends them off to fix nothing.
        """
        narrowed = (
            self.selected_store is not None
            or self.selected_days < widest_range()
            or self.meat_only
        )
        if trend.observed_days == 0 and self._any_history and narrowed:
            store = self.store_select.currentText() if self.selected_store else "any store"
            # WHICH narrowing emptied this? Blaming the tab whenever it happens
            # to be on sends someone to the Overall tab that is just as empty,
            # and asserting "there is other protein" without looking would be
            # this pane telling the user something it never checked. So ask:
            # would dropping ONLY the meat filter, over this same window and
            # store, find anything? One extra query, and only ever on the empty
            # path.
            if self.meat_only and self._has_other_protein():
                return (
                    f"No animal protein for {store} in the last {trend.days} days. "
                    "There is other protein on record — see the Overall protein tab."
                )
            return (
                f"No protein prices for {store} in the last {trend.days} days. "
                "There is history further back — widen the range, or pick All stores."
            )
        return trend.reason

    def _has_other_protein(self) -> bool:
        """Is there non-meat protein in exactly this window and store?"""
        wider = service.protein_price_trend(
            days=self.selected_days, store=self.selected_store, meat_only=False
        )
        return wider.observed_days > 0

    def _build_legend(self, trend: service.PriceTrend) -> None:
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # One series needs no legend box -- the title and the direct label name it.
        plottable = trend.plottable
        self.legend.setVisible(len(plottable) >= 2)
        for store_trend in plottable:
            swatch = QFrame()
            swatch.setFixedSize(10, 10)
            colour = self.chart.colour_for(store_trend.key)
            swatch.setStyleSheet(f"background:{colour.name()};border-radius:2px;")
            self.legend_layout.addWidget(swatch)
            # Text keeps its ink colour; the swatch beside it carries identity.
            self.legend_layout.addWidget(QLabel(store_trend.label))
        self.legend_layout.addStretch(1)
