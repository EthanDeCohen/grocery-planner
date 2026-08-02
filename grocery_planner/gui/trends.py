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
``service.MIN_POINTS_TO_PLOT`` days it shows ``ProteinTrend.reason`` — which
distinguishes "nothing scraped yet" from "come back tomorrow" — and still lists
whatever latest prices *are* known, because refusing to plot is not a reason to
withhold the data.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import service
from ..stores import BY_KEY, STORES

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

_MARGIN_LEFT = 62
_MARGIN_TOP, _MARGIN_BOTTOM = 14, 26
#: Gap between the plot and its endpoint labels, and the least width to leave
#: for them when the widget is too narrow to fit them honestly.
_LABEL_GAP, _MIN_PLOT_WIDTH = 10, 120


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


def _slot(store_key: str) -> int:
    """This store's fixed palette slot: its position in the store registry."""
    keys = [s.key for s in STORES]
    return keys.index(store_key) if store_key in keys else len(keys)


def _display(store_key: str) -> str:
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


def _money_per_gram(value: float) -> str:
    """$/g protein, at the precision the numbers actually differ by."""
    return f"${value:.4f}"


class TrendChart(QWidget):
    """The plot itself: one polyline per store, hairline grid, endpoint labels."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._trend = service.ProteinTrend(days=service.DEFAULT_WINDOW_DAYS)

    def set_trend(self, trend: service.ProteinTrend) -> None:
        self._trend = trend
        self.setToolTip("\n".join(
            f"{_display(s.store)}: {_money_per_gram(s.latest.cost_per_gram_protein)}/g "
            f"— {s.latest.item_name}"
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
            s.store: f"{_display(s.store)} {_money_per_gram(s.latest.cost_per_gram_protein)}"
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
        values = [p.cost_per_gram_protein for s in series for p in s.points]
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
            colour = self.colour_for(store_trend.store)
            points = [
                QPointF(x_of(p.day), y_of(p.cost_per_gram_protein))
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
            [y_of(s.latest.cost_per_gram_protein) for s in series],
            metrics.height(), plot_top, plot_bottom,
        )
        painter.setPen(QPen(muted, 1))
        for store_trend, y in zip(series, label_y):
            painter.drawText(
                int(plot_right + _LABEL_GAP),
                int(y + metrics.height() / 3),
                labels[store_trend.store],
            )
        painter.end()


class TrendsPane(QWidget):
    """Chart, legend and the latest known price per store — or an honest reason."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.title = QLabel("Cheapest protein per day")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        self.chart = TrendChart()
        layout.addWidget(self.chart, 1)

        self.legend = QWidget()
        self.legend_layout = QHBoxLayout(self.legend)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.legend)

        self.latest = QLabel("")
        self.latest.setWordWrap(True)
        self.latest.setTextFormat(Qt.RichText)
        layout.addWidget(self.latest)

        self.reload()

    # ----------------------------------------------------------------- #
    def reload(self) -> None:
        trend = service.protein_price_trend()
        self.trend = trend
        self.chart.set_trend(trend)
        self.chart.setVisible(trend.is_plottable)
        self._build_legend(trend)

        if trend.is_plottable:
            self.subtitle.setText(
                f"Best $/g protein each day, last {trend.days} days. "
                "Lower is better."
            )
        else:
            self.subtitle.setText(trend.reason)

        # Shown either way: it is the relief for the two light-mode series
        # colours that sit under 3:1, and the only content at all when there
        # is not yet enough history to plot.
        rows = [
            f"<b>{_display(s.store)}</b> — {_money_per_gram(s.latest.cost_per_gram_protein)}/g "
            f"<span>({s.latest.item_name})</span>"
            for s in trend.series if s.latest
        ]
        self.latest.setText(
            ("Latest known:<br>" + "<br>".join(rows)) if rows else ""
        )
        self.latest.setVisible(bool(rows))

    def _build_legend(self, trend: service.ProteinTrend) -> None:
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
            colour = self.chart.colour_for(store_trend.store)
            swatch.setStyleSheet(f"background:{colour.name()};border-radius:2px;")
            self.legend_layout.addWidget(swatch)
            # Text keeps its ink colour; the swatch beside it carries identity.
            self.legend_layout.addWidget(QLabel(_display(store_trend.store)))
        self.legend_layout.addStretch(1)
