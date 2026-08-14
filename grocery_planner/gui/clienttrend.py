# ######### decohen-partners ##########
# Protein Ledger
"""This client's protein prices, against everybody's (GFP-129).

The main window's chart answers a shop-level question: where is protein
cheapest today. The client page answered nothing over time at all -- it was
entirely a snapshot.

**Two lines, and the second never touches the first.**

1. **Cheapest available** -- the effective $/g of the UNCONSTRAINED plan. The
   optimiser's line; no preference and no constraint applies to it. The user
   confirmed for v1 that this baseline stays.
2. **This client's plan** -- the effective $/g of the plan the client's
   preferences AND current selection actually produce.

GFP-144: line 2 used to be "the cheapest thing they will eat" -- a category
filter and nothing more. Once GFP-136 added constraints that change the plan
without changing that answer, the chart began contradicting the bill beside
it: with "include every protein I ticked" on, it reported that a client's
preferences cost nothing extra while the bill showed +$2.70/day, both on
screen at once. It was not wrong; it was answering a question nobody was
asking any more.

**Both lines are now total-cost-over-grams-covered for a real plan**, so they
are directly comparable and the gap means something. Measuring one as a
minimum and the other as a plan average would make the gap an artefact of the
two definitions rather than of the client's choices.

**The gap between them is the point.** It is what this client's preferences
cost them, and a nutritionist can put a finger on it: *your restrictions add
about a cent a gram* is a conversation that could not previously be had, and it
comes free from data already stored.

The first line is still never filtered or re-ranked by preference. The user
was explicit and repeated it: the optimiser plot does not change.

Every number here comes from ``bill.effective_cost_per_gram``, which runs the
BILL's own allocator over one day of history. Not a similar calculation -- the
same one. Two implementations that could drift is the defect being fixed, so
there is only one.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import bill as billing
from .. import config, db, preferences, targets
from ..customers import CustomerRepository
from ..service import trends as service
from .trends import TrendChart

#: How far back the client chart looks. Matches the main window's default so
#: the two charts are answering the same question over the same window.
WINDOW_DAYS = 90

BASELINE_LABEL = "Cheapest available"
THEIRS_LABEL = "Their plan"


class ClientTrendPane(QWidget):
    """The two-series chart, plus a line saying what it means."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_id: int | None = None
        #: The selection the bill is currently built under. Set by the client
        #: page whenever a checkbox moves -- that is what makes this chart
        #: follow the panel instead of contradicting it.
        self.selection = billing.Selection()
        #: Ranking a day of history costs ~40 ms and does NOT depend on the
        #: selection, so it is cached across checkbox changes. Cleared when the
        #: underlying prices could have changed.
        self._ranked_by_day: dict[str, list[dict]] | None = None

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
        self._ranked_by_day = None
        self.chart.set_trend(service.PriceTrend(days=WINDOW_DAYS))
        self.subtitle.setText("No client selected.")

    def set_client(self, customer_id: int) -> None:
        self.customer_id = customer_id
        # A different client does not change the PRICES, so the ranked pools
        # stay valid. Only what is done with them changes.
        self.reload()

    def set_selection(self, selection: "billing.Selection") -> None:
        """Follow the selection panel (GFP-144)."""
        self.selection = selection
        self.reload()

    def prices_changed(self) -> None:
        """Drop the cached ranking -- a scrape added days or changed today's."""
        self._ranked_by_day = None
        self.reload()

    # ----------------------------------------------------------------- #
    def _ranked(self, conn) -> dict[str, list[dict]]:
        if self._ranked_by_day is None:
            # Scoped to the configured ZIP: one ZIP is one market, and since
            # GFP-122 made the ZIP changeable an install can hold history from
            # two of them. Averaging those into one line would be a quiet lie.
            self._ranked_by_day = billing.rank_history_by_day(
                WINDOW_DAYS, conn, postal_code=config.postal_code()
            )
        return self._ranked_by_day

    def _series(self, ranked, label, target, categories, selection, conn):
        """One line: the plan's effective $/g on each day it can be computed.

        A day with no allocatable deal contributes NO POINT rather than a
        zero. A zero would draw as "free", which is savings.py's rule 1 --
        absent stays absent, never a guess.
        """
        points = []
        for day, pool in sorted(ranked.items()):
            value = billing.effective_cost_per_gram(
                pool, target, categories, selection, conn
            )
            if value is None:
                continue
            points.append(service.TrendPoint(
                day=day, value=value, item_name=label, price=value, store="",
            ))
        return service.TrendSeries(key=label, label=label, points=points)

    def reload(self) -> None:
        if self.customer_id is None:
            self.clear()
            return
        conn = db.connect()

        customer = CustomerRepository.get(self.customer_id, conn=conn)
        target = targets.protein_target_for(customer, conn=conn) if customer else None
        if customer is None or target is None or not target.daily_grams:
            # No target means no plan to price. Say so rather than drawing a
            # line whose meaning nobody could state.
            self.chart.set_trend(service.PriceTrend(days=WINDOW_DAYS))
            self.subtitle.setText(
                "This client has no weight on file, so there is no daily "
                "protein target to price."
            )
            return

        grams = target.daily_grams
        chosen = preferences.list_preferences(self.customer_id, conn=conn)
        ranked = self._ranked(conn)

        # UNCONSTRAINED, deliberately: no categories and a default Selection.
        # This is the optimiser's line and the thing everything else is
        # measured against (GFP-49's baseline rule, same reasoning).
        baseline = self._series(
            ranked, BASELINE_LABEL, grams, [], billing.Selection(), conn
        )
        series = [baseline]

        constrained = bool(chosen) or self.selection != billing.Selection()
        if constrained:
            theirs = self._series(
                ranked, THEIRS_LABEL, grams, chosen, self.selection, conn
            )
            series.append(theirs)
            self.subtitle.setText(self._explain_series(baseline, theirs))
        else:
            # Nothing is constraining the plan, so the two lines would be
            # IDENTICAL. Drawing a line on top of itself looks like a
            # rendering fault, so draw one and say why there is only one.
            self.subtitle.setText(
                "No preferences or constraints set, so this client's plan is "
                "the cheapest available."
            )

        self.chart.set_trend(
            service.PriceTrend(days=WINDOW_DAYS, series=series)
        )

    @staticmethod
    def _explain_series(baseline, theirs) -> str:
        """What the gap between the two lines costs, in words.

        The number, not the shape, is what a nutritionist repeats to a client.
        """
        best = baseline.latest
        mine = theirs.latest
        if best is None or mine is None:
            return (
                "Not enough price history yet to compare. This fills in as "
                "prices are collected each day."
            )
        gap = mine.value - best.value
        if gap <= 0:
            return "These choices cost nothing extra at today's prices."
        return (
            f"These choices cost about ${gap:.4f} per gram more than the "
            f"cheapest plan available (${mine.value:.4f} vs ${best.value:.4f})."
        )
