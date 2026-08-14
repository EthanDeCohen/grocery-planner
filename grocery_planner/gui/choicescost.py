# ######### decohen-partners ##########
# Protein Ledger
"""What this client's choices cost, as a grid (GFP-153).

Two questions the rest of the page answers separately and neither answers well:

* what does **variety** cost this client (Repeat Cheapest vs Mix It Up), and
* what do their **preferences** cost (their list vs no list at all).

Reading the two rows against each other is where the insight is. On the first
real client the variety penalty was 127% with one category ticked against 20%
unconstrained -- so "Mix It Up is expensive" was really "your preference list
is narrow", a different sentence about a different thing.

**That relationship is an observation, not a law, and the panel must not
state it as one.** Which row carries the steeper penalty depends entirely on
the catalog: a client restricted to a category with three near-identical
cheap options can vary almost for free, while an unrestricted one whose
cheapest food has no close second cannot. A test builds exactly that inverted
case. So the panel reports both numbers and leaves the comparison to the
reader rather than asserting a direction.

**Every figure comes from bill.week_plan**, the same object the bill and the
chart use. Not a similar calculation -- the same one. GFP-144 is the standing
example of what happens when a panel computes its own version of a number that
also appears elsewhere: the two disagree on screen and both look authoritative.

**Four plans, one ranking.** Solving the week four ways would rank the deal
pool four times at ~40 ms each, on a panel that redraws whenever a checkbox
moves. ``bill.rank_current_deals`` is hoisted out and shared, which takes the
redraw from 190 ms to 50 ms.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import bill, db

#: Row labels. "No preferences" rather than "unconstrained" -- the panel is
#: read by a nutritionist, not by whoever wrote the optimiser.
THEIRS = "Their preferences"
UNRESTRICTED = "No preferences"

HEADERS = ("", "Repeat", "Mix It Up", "Variety costs")


class ChoicesCostPane(QWidget):
    """A four-cell grid plus the sentence it supports."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_id: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)

        self.title = QLabel("What these choices cost")
        self.title.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.title)

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(3)
        layout.addWidget(self.grid_widget)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #666;")
        layout.addWidget(self.note)

        self._cells: list[QLabel] = []

    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        self.customer_id = None
        self._reset_grid()
        self.note.setText("No client selected.")

    def set_client(self, customer_id: int) -> None:
        self.customer_id = customer_id
        self.reload()

    def _reset_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) rather than deleteLater alone: this pane
                # re-renders on every checkbox toggle, and a merely-scheduled
                # deletion leaves the old grid painted underneath the new one.
                widget.setParent(None)
        self._cells = []

    def _put(self, row: int, column: int, text: str, *, bold: bool = False,
             muted: bool = False) -> None:
        label = QLabel(text)
        style = []
        if bold:
            style.append("font-weight: 600;")
        if muted:
            style.append("color: #666;")
        if style:
            label.setStyleSheet(" ".join(style))
        self.grid.addWidget(label, row, column)
        self._cells.append(label)

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        if self.customer_id is None:
            self.clear()
            return

        conn = db.connect()
        shared = bill.rank_current_deals(conn)

        def week(categories, vary):
            return bill.week_plan(
                self.customer_id, categories=categories,
                selection=bill.Selection(vary_week=vary),
                conn=conn, ranked=shared,
            )

        # categories=None means "use what is on file"; [] forces unconstrained
        # without touching stored rows (bill.py's documented distinction).
        theirs_repeat, theirs_mix = week(None, False), week(None, True)
        open_repeat, open_mix = week([], False), week([], True)

        if theirs_repeat is None:
            self._reset_grid()
            self.note.setText(
                "This client has no weight on file, so there is no plan to price."
            )
            return

        self._reset_grid()
        for column, header in enumerate(HEADERS):
            self._put(0, column, header, bold=True, muted=column == 0)

        rows = [
            (THEIRS, theirs_repeat, theirs_mix),
            (UNRESTRICTED, open_repeat, open_mix),
        ]
        for index, (label, repeat, mix) in enumerate(rows, start=1):
            self._put(index, 0, label, muted=True)
            self._put(index, 1, _money(repeat))
            self._put(index, 2, _money(mix))
            self._put(index, 3, _penalty(repeat, mix))

        self.note.setText(_explain(theirs_repeat, open_repeat))


def _money(plan) -> str:
    """A week's cost, or an em dash when the plan could not be built.

    Never a zero: an unbuildable plan costing "$0.00" reads as free, which is
    savings.py's rule 1 applied to a table cell.
    """
    return f"${plan.total_cost:,.2f}" if plan is not None else "—"


def _penalty(repeat, mix) -> str:
    """What variety adds, as a percentage of the flat week."""
    if repeat is None or mix is None or repeat.total_cost <= 0:
        return "—"
    delta = (mix.total_cost - repeat.total_cost) / repeat.total_cost
    if delta <= 0.005:
        # Under half a percent is noise, and "+0%" invites someone to wonder
        # whether the control is working.
        return "no extra"
    return f"+{delta * 100:.0f}%"


def _explain(theirs, unrestricted) -> str:
    """The sentence the grid exists to support.

    Deliberately about the PREFERENCES, not about variety: the surprising
    finding was that the variety penalty is mostly a function of how narrow
    the preference list is, and that is the part a nutritionist can act on.
    """
    if theirs is None or unrestricted is None:
        return ""
    if not theirs.categories:
        return (
            "No preferences set, so both rows price the same food. Ticking a "
            "protein below will separate them."
        )
    extra = theirs.total_cost - unrestricted.total_cost
    if extra <= 0.005:
        return (
            "These preferences cost nothing extra this week — what they will "
            "eat is already the cheapest protein available."
        )
    return (
        f"These preferences cost about ${extra:,.2f} a week more than eating "
        f"whatever is cheapest. Compare the two rows in the right-hand column "
        f"to see what variety costs on each."
    )
