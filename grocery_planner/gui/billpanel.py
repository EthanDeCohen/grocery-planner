"""Daily bill panel (GFP-52): the centre column of the client detail page.

The two price figures, the protein-category checkboxes, and the itemised
lines that make up the bill. This is the column the nutritionist actually
looks at, so every number on it is either sourced or explicitly absent.

All arithmetic lives in :mod:`grocery_planner.bill` (GFP-48/GFP-49); this
module only renders it. That split is why the same figures are testable
without a Qt event loop, and why a UI change cannot quietly alter a price.

**Amortisation is never dropped on the way to the screen.** The figures are
"what today's share of this week's cheapest protein would cost", not a
shopping total, and the panel prints :data:`bill.AMORTIZATION_NOTE` under
them rather than leaving a bare dollar amount to be misread as checkout.

**A checkbox is a filter, and filters do not need a Save button** (the
ticket's own words). Toggling one writes the client's stated preference
through :func:`preferences.set_preferences` and recomputes immediately --
these checkboxes *are* the GFP-30 preference set, not a scratch "what if"
overlay, so persisting them is what the nutritionist means by ticking one.

**Nothing checked shows the baseline, not an empty basket.** That is
``preferences.py``'s rule reaching the screen: zero stated preferences means
unconstrained, never "match nothing". With nothing ticked the two solves are
identical, so the panel shows one figure instead of a meaningless
"+$0.00 for your preferences".

**The category list comes from the data, never a hard-coded six.** GFP-30 is
explicit that ``nutrition.list_categories`` is the source of truth so a new
category becomes selectable by adding a food row, not by editing a UI. Note
the catalog currently carries two overlapping vocabularies (``beef`` /
``chicken`` beside ``Meat`` / ``Seafood``); that is a data-cleanup question
for the catalog, and this panel deliberately shows what is really there
rather than hiding half of it behind a prettier hard-coded list.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from .. import bill, db, nutrition, preferences
from ..stores import BY_KEY
from .widgets import placeholder

#: Checkbox columns. Enough to keep a dozen categories from becoming a
#: scroll bar in a column that also has to show the bill itself.
CHECKBOX_COLUMNS = 3


def _money(value: float) -> str:
    return f"${value:.2f}"


def _store_tag(store_key: str) -> str:
    """The small store tag beside each contributing line (GFP-52)."""
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


class BillPanel(QWidget):
    """The daily protein bill for one client, with its preference filter."""

    #: Emitted whenever the bill is recomputed — a new client, a preference
    #: toggle, a biometric save, a fresh scrape. Carries nothing: a listener
    #: reads :attr:`comparison`, so there is one source of truth for the bill
    #: rather than a copy travelling through the signal.
    bill_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_id: int | None = None
        self.comparison: bill.BillComparison | None = None
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        self.title = QLabel("Daily protein bill")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)

        # --- the two figures -------------------------------------------- #
        self.headline = QLabel("")
        headline_font = self.headline.font()
        headline_font.setBold(True)
        headline_font.setPointSize(headline_font.pointSize() + 6)
        self.headline.setFont(headline_font)
        layout.addWidget(self.headline)

        self.comparison_label = QLabel("")
        self.comparison_label.setWordWrap(True)
        layout.addWidget(self.comparison_label)

        # Shown only when the two figures cannot honestly be compared -- see
        # BillComparison.caveat. A starving preference is *cheaper*, and that
        # must never read as a saving.
        self.caveat_label = QLabel("")
        self.caveat_label.setWordWrap(True)
        layout.addWidget(self.caveat_label)

        self.amortisation_label = QLabel(bill.AMORTIZATION_NOTE)
        self.amortisation_label.setWordWrap(True)
        layout.addWidget(self.amortisation_label)

        # --- preference checkboxes --------------------------------------- #
        layout.addWidget(QLabel("Protein preferences:"))
        self.categories_widget = QWidget()
        self.categories_layout = QGridLayout(self.categories_widget)
        self.categories_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.categories_widget)

        # --- itemised lines ---------------------------------------------- #
        layout.addWidget(QLabel("What makes it up:"))
        self.lines_list = QListWidget()
        # Wrap rather than scroll sideways: a bill line carries four facts and
        # a horizontal scrollbar hides the last of them (the price).
        self.lines_list.setWordWrap(True)
        layout.addWidget(self.lines_list, 1)

        self.footer = QLabel("")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)

        self._build_checkboxes()
        self.clear()

    # ----------------------------------------------------------------- #
    def _build_checkboxes(self) -> None:
        """One checkbox per category the foods catalog actually knows about."""
        for index, category in enumerate(
            sorted(nutrition.list_categories(conn=db.connect()), key=str.lower)
        ):
            box = QCheckBox(category)
            box.toggled.connect(self._on_preference_toggled)
            self._boxes[category] = box
            self.categories_layout.addWidget(
                box, index // CHECKBOX_COLUMNS, index % CHECKBOX_COLUMNS
            )

    def _checked_categories(self) -> list[str]:
        return sorted(name for name, box in self._boxes.items() if box.isChecked())

    def _set_boxes(self, categories: list[str]) -> None:
        """Reflect a stored preference set without re-triggering a save."""
        chosen = set(categories)
        for name, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(name in chosen)
            box.blockSignals(False)

    # ----------------------------------------------------------------- #
    def clear(self) -> None:
        """Blank the panel, so no client's bill outlives the client shown."""
        self.customer_id = None
        self.comparison = None
        self._set_boxes([])
        self.categories_widget.setEnabled(False)
        self.headline.setText("—")
        self.comparison_label.setText("No client selected.")
        self.caveat_label.setText("")
        self.caveat_label.setVisible(False)
        self.lines_list.clear()
        self.lines_list.addItem(placeholder("Nothing to show yet."))
        self.footer.setText("")
        self.bill_changed.emit()

    def set_client(self, customer_id: int) -> bool:
        """Load one client's bill. ``False`` if there is no such client."""
        conn = db.connect()
        comparison = bill.compare_bills(customer_id, conn=conn)
        self.customer_id = customer_id
        self.categories_widget.setEnabled(True)
        self._set_boxes(preferences.list_preferences(customer_id, conn=conn))
        if comparison is None:
            # No weight on file (or no such client) -> no target -> no bill.
            # GFP-29's rule: say so, never price a guessed weight.
            self.comparison = None
            self.headline.setText("—")
            self.comparison_label.setText(
                "No protein target for this client yet, so there is no bill to "
                "compute. Add a weight in the biometrics panel."
            )
            self.caveat_label.setVisible(False)
            self.lines_list.clear()
            self.lines_list.addItem(placeholder("No target, so nothing to buy."))
            self.footer.setText("")
            self.bill_changed.emit()
            return False
        self._render(comparison)
        return True

    def reload(self) -> None:
        """Recompute for the current client — after a biometric edit or a scrape."""
        if self.customer_id is not None:
            self.set_client(self.customer_id)

    # ----------------------------------------------------------------- #
    def _on_preference_toggled(self, _checked: bool) -> None:
        """A checkbox is a filter: persist it and recompute, with no Save step."""
        if self.customer_id is None:
            return
        conn = db.connect()
        try:
            preferences.set_preferences(
                self.customer_id, self._checked_categories(), conn=conn
            )
        except ValueError as exc:
            # A category that has since left the catalog -- surfaced rather
            # than swallowed, since the stored preference is now unstorable.
            self.footer.setText(f"Preference not saved — {exc}")
            return
        self.reload()

    # ----------------------------------------------------------------- #
    def _render(self, comparison: bill.BillComparison) -> None:
        self.comparison = comparison
        plan = comparison.constrained
        self.headline.setText(f"{_money(plan.total_cost)}/day")

        if comparison.is_constrained:
            delta = comparison.delta_cost
            baseline = _money(comparison.baseline.total_cost)
            if abs(delta) < 0.005:
                # Half a cent apart is the same plan by another route; naming a
                # "+$0.00 penalty" for it would invent a cost that isn't there.
                self.comparison_label.setText(
                    f"Baseline {baseline}/day — these preferences cost nothing extra."
                )
            else:
                sign = "+" if delta > 0 else "−"
                self.comparison_label.setText(
                    f"Baseline {baseline}/day  ·  your plan "
                    f"{_money(plan.total_cost)}/day  ({sign}{_money(abs(delta))})"
                )
        else:
            # Nothing ticked: the two solves are the same, so one figure.
            self.comparison_label.setText(
                "Cheapest way to hit the target from everything on offer. "
                "Tick a preference to price a narrower plan."
            )

        self.caveat_label.setText(comparison.caveat)
        self.caveat_label.setVisible(bool(comparison.caveat))

        # --- itemised lines, each with its store tag --------------------- #
        self.lines_list.clear()
        if not plan.lines:
            self.lines_list.addItem(placeholder(
                "No deal on offer can be priced per gram of protein for these "
                "preferences."
            ))
        for line in plan.lines:
            food = f"  ·  {line.grams_food:.0f} g food" if line.grams_food else ""
            self.lines_list.addItem(
                f"[{_store_tag(line.store)}]  {line.item_name}  —  "
                f"{line.grams_protein:.0f} g protein{food}  ·  {_money(line.cost)}/day"
            )

        # --- what did not make it in ------------------------------------- #
        parts = [
            f"{plan.covered_grams:.0f} of {plan.target_grams:.0f} g protein covered"
        ]
        if not plan.is_complete:
            parts.append(f"{plan.shortfall_grams:.0f} g short")
        priced = plan.considered_deals
        parts.append(f"{priced} deal{'' if priced == 1 else 's'} priced")
        if plan.excluded_deals:
            # Never hidden: an unpriceable deal is information about coverage,
            # not something to quietly drop (GFP-48).
            parts.append(
                f"{plan.excluded_deals} could not be priced per gram of protein"
            )
        self.footer.setText(" · ".join(parts) + ".")
        self.bill_changed.emit()
