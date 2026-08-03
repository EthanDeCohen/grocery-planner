"""This client's protein prices, against everybody's (GFP-129).

The main window's chart answers a shop-level question: where is protein
cheapest today. The client page answered nothing over time at all -- it was
entirely a snapshot.

**Two lines, and the second never touches the first.**

1. **Cheapest available** -- the lowest $/g of animal protein on offer,
   unfiltered. Identical to what the main window plots. This is the optimiser's
   line and it does not change.
2. **This client's plan** -- the same computation restricted to the protein
   categories they will actually eat.

**The gap between them is the point.** It is what this client's preferences
cost them, and a nutritionist can put a finger on it: *your restrictions add
about a cent a gram* is a conversation that could not previously be had, and it
comes free from data already stored.

Deliberately NOT recomputing, re-ranking or filtering the first line. The user
was explicit and repeated it: the optimiser plot does not change; this ADDS a
series.

Inherits GFP-134: the category filter reads ``foods.category``, which mixes
broad buckets with specific kinds, so a client who ticks "chicken" may miss
actual chicken. That is used here ANYWAY, on purpose -- it is the same filter
the bill uses, and a chart that filtered correctly while the bill did not would
be worse than both being consistently wrong. Fixing GFP-134 fixes both.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import db, preferences
from ..service import trends as service
from .trends import TrendChart

#: How far back the client chart looks. Matches the main window's default so
#: the two charts are answering the same question over the same window.
WINDOW_DAYS = 90

BASELINE_LABEL = "Cheapest available"


class ClientTrendPane(QWidget):
    """The two-series chart, plus a line saying what it means."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.title = QLabel("Protein prices over time")
        self.title.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.title)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color: #666;")
        layout.addWidget(self.subtitle)

        self.chart = TrendChart()
        layout.addWidget(self.chart, 1)

    # ----------------------------------------------------------------- #
    def clear(self) -> None:
        self.customer_id = None
        self.chart.set_trend(service.PriceTrend(days=WINDOW_DAYS))
        self.subtitle.setText("No client selected.")

    def set_client(self, customer_id: int) -> None:
        self.customer_id = customer_id
        self.reload()

    def reload(self) -> None:
        if self.customer_id is None:
            self.clear()
            return
        conn = db.connect()
        chosen = preferences.list_preferences(self.customer_id, conn=conn)

        baseline = service.price_trend(
            meat_only=True, series_label=BASELINE_LABEL,
            days=WINDOW_DAYS, conn=conn,
        )
        series = list(baseline.series)

        if chosen:
            label = "Their preferences"
            theirs = service.price_trend(
                meat_only=True, categories=chosen, series_label=label,
                days=WINDOW_DAYS, conn=conn,
            )
            series += list(theirs.series)
            self.subtitle.setText(self._explain(baseline, theirs))
        else:
            # No preferences means the two lines would be IDENTICAL. Drawing a
            # line on top of itself looks like a rendering fault, so draw one
            # and say why there is only one.
            self.subtitle.setText(
                "No protein preferences set, so this client's plan is the "
                "cheapest available."
            )

        self.chart.set_trend(
            service.PriceTrend(days=WINDOW_DAYS, series=series)
        )

    @staticmethod
    def _explain(baseline: service.PriceTrend, theirs: service.PriceTrend) -> str:
        """What the gap between the two lines costs, in words.

        The number, not the shape, is what a nutritionist repeats to a client.
        """
        best = baseline.series[0].latest if baseline.series else None
        mine = theirs.series[0].latest if theirs.series else None
        if best is None or mine is None:
            return (
                "Not enough price history yet to compare. This fills in as "
                "prices are collected each day."
            )
        gap = mine.value - best.value
        if gap <= 0:
            return "These preferences cost nothing extra at today's prices."
        return (
            f"These preferences cost about ${gap:.4f} per gram more than the "
            f"cheapest protein on offer (${mine.value:.4f} vs ${best.value:.4f})."
        )
