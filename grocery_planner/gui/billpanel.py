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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
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
        #: GFP-136/137: set by the client page from SelectionPanel. None means
        #: "use the client's stored preferences and the default selection",
        #: which is what this panel does when driven on its own.
        self.categories: list[str] | None = None
        self.selection: bill.Selection | None = None
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

        # Shown ONLY when a budget is set. A client with no budget is
        # unmeasured, not permanently under -- and a row reading "no budget"
        # on every client who does not use the feature is clutter.
        self.budget_label = QLabel("")
        self.budget_label.setWordWrap(True)
        self.budget_label.setVisible(False)
        layout.addWidget(self.budget_label)

        # GFP-156: the CHOICE that follows the verdict. One line, shown only
        # when over budget. The user's framing (GFP-131) is that there are two
        # options and only two -- relax a preference, or accept going over --
        # so this names the best relaxation and says the alternative plainly
        # rather than listing all eight.
        self.options_label = QLabel("")
        self.options_label.setWordWrap(True)
        self.options_label.setStyleSheet("color: #666;")
        self.options_label.setVisible(False)
        layout.addWidget(self.options_label)

        self.comparison_label = QLabel("")
        self.comparison_label.setWordWrap(True)
        layout.addWidget(self.comparison_label)

        # Shown only when the two figures cannot honestly be compared -- see
        # BillComparison.caveat. A starving preference is *cheaper*, and that
        # must never read as a saving.
        self.caveat_label = QLabel("")
        self.caveat_label.setWordWrap(True)
        layout.addWidget(self.caveat_label)

        # GFP-124: the amortisation caveat is TRUE and load-bearing -- it stops
        # somebody reading $1.86 as "what I will spend at the till today". But a
        # caveat that has to be read on every visit is in the wrong place: it is
        # onboarding text occupying permanent space above everything actionable.
        #
        # So it moves to the headline's tooltip: read once, findable on purpose,
        # never deleted. Deleting it outright would be the wrong fix.
        self.headline.setToolTip(bill.AMORTIZATION_NOTE)
        self.amortisation_label = QLabel("")
        self.amortisation_label.setVisible(False)
        self.amortisation_label.setWordWrap(True)
        layout.addWidget(self.amortisation_label)

        # --- preference checkboxes --------------------------------------- #
        # GFP-137: the preference checkboxes moved to SelectionPanel, which
        # is where "how to pick" now lives. Kept and HIDDEN rather than
        # deleted: this panel is still usable on its own, and the client page
        # simply drives it from outside.
        self.preferences_label = QLabel("Protein preferences:")
        self.preferences_label.setVisible(False)
        layout.addWidget(self.preferences_label)
        self.categories_widget = QWidget()
        self.categories_layout = QGridLayout(self.categories_widget)
        self.categories_layout.setContentsMargins(0, 0, 0, 0)
        self.categories_widget.setVisible(False)
        layout.addWidget(self.categories_widget)

        # --- itemised lines ---------------------------------------------- #
        layout.addWidget(QLabel("What makes it up:"))
        self.lines_list = QListWidget()
        # Wrap rather than scroll sideways: a bill line carries four facts and
        # a horizontal scrollbar hides the last of them (the price).
        self.lines_list.setWordWrap(True)
        layout.addWidget(self.lines_list, 1)

        # GFP-130: what was left out, and why. Under the itemised list, since
        # it explains that list rather than competing with it.
        self.excluded_label = QLabel("")
        self.excluded_label.setWordWrap(True)
        self.excluded_label.setStyleSheet("color: #666;")
        self.excluded_label.setVisible(False)
        layout.addWidget(self.excluded_label)

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
        self.budget_label.setVisible(False)
        self.options_label.setVisible(False)
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
        comparison = bill.compare_bills(
            customer_id, categories=self.categories, conn=conn,
            selection=self.selection,
        )
        self.customer_id = customer_id
        self.categories_widget.setEnabled(True)
        self._set_boxes(preferences.list_preferences(customer_id, conn=conn))
        if comparison is None:
            # No weight on file (or no such client) -> no target -> no bill.
            # GFP-29's rule: say so, never price a guessed weight.
            self.comparison = None
            self.headline.setText("—")
            self.budget_label.setVisible(False)
            self.options_label.setVisible(False)
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
        """Recompute for the current client — after a biometric edit, a scrape,
        or a change in the selection panel."""
        if self.customer_id is not None:
            self.set_client(self.customer_id)

    def set_selection(
        self, categories: list[str] | None, selection: bill.Selection | None
    ) -> None:
        """Drive this panel from outside and recompute (GFP-136/137)."""
        self.categories = categories
        self.selection = selection
        self.reload()

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
    def _render_week(self, plan: bill.Bill) -> None:
        """Fold the week into the headline, and state the budget in one line.

        The user asked for "7 day total vs 1 day" and then, seeing it, that it
        was too wordy. Both notes were true: the panel showed $3.17/day twice
        (headline and comparison line) and spent a full sentence explaining
        why a varied week is not seven times a day.

        So the two figures share the headline, the explanation becomes a
        tooltip, and the budget gets one short line that appears only when
        there is a budget -- the user's rule: no budget, no problem.

        The week comes from ``budget.weekly_plan``, which since GFP-155 prices
        the REAL seven days. With Mix It Up on, multiplying day one says
        $22.17 where the plan costs $50.42, so a verdict built on it could be
        wrong by more than 2x.
        """
        from .. import budget as budget_module
        from ..customers import CustomerRepository

        self.budget_label.setVisible(False)
        self.options_label.setVisible(False)
        if self.customer_id is None:
            return

        conn = db.connect()
        customer = CustomerRepository.get(self.customer_id, conn=conn)
        if customer is None:
            return

        weekly = budget_module.weekly_plan(
            customer, categories=self.categories, conn=conn,
            selection=self.selection,
        )
        if weekly is None:
            return

        week_cost = weekly.weekly_cost
        self.headline.setText(
            f"{_money(plan.total_cost)}/day  ·  {_money(week_cost)}/week"
        )

        flat = plan.total_cost * budget_module.DAYS_PER_WEEK
        note = bill.AMORTIZATION_NOTE
        if abs(week_cost - flat) >= 0.005:
            # A reader who multiplies and gets a different number will assume
            # one of the figures is wrong, so the reason stays available --
            # just not occupying a line of the panel.
            note += (
                "\n\nThe week is not seven times the day because Mix It Up "
                "varies it; a varied week costs more."
            )
        self.headline.setToolTip(note)

        if not weekly.has_budget:
            return

        self.budget_label.setVisible(True)
        if weekly.is_over:
            self.budget_label.setText(
                f"{_money(weekly.over_by)} over {_money(weekly.budget)} budget"
            )
            self.budget_label.setStyleSheet("color: #b3261e; font-weight: 600;")
            self._render_options(customer, conn)
        else:
            self.budget_label.setText(
f"{_money(weekly.headroom)} left of {_money(weekly.budget)}"
            )
            self.budget_label.setStyleSheet("color: #146c2e; font-weight: 600;")

    def _render_options(self, customer, conn) -> None:
        """The two ways out of being over budget (GFP-156).

        GFP-131's framing, in the user's words: "keep to cost low or going
        above budget -- the only two options the nutritionist will have to
        make". So this names the single BEST relaxation and states the
        alternative, rather than listing all eight and turning a decision into
        a table.

        Going over budget is a legitimate choice and is presented as one. The
        protein target is never the thing that gives way (GFP-131/GFP-136).
        """
        from .. import budget as budget_module

        advice = budget_module.advise(
            customer, categories=self.categories, conn=conn,
            selection=self.selection,
        )
        if advice is None or not advice.is_over:
            return

        self.options_label.setVisible(True)
        if advice.unreachable:
            # Allowing everything is still over. Naming a preference to relax
            # here would be actively misleading -- the preferences are not the
            # problem.
            self.options_label.setText(
                "No preference change reaches this budget at current prices."
            )
            self.options_label.setToolTip("")
            return

        best = advice.best
        if best is None:
            self.options_label.setText("")
            self.options_label.setVisible(False)
            return

        self.options_label.setText(
            f"Allow {best.category}: {_money(best.weekly_cost)}/week, "
            f"or accept going over"
        )
        # The other options stay reachable without occupying the panel.
        others = advice.options[1:]
        self.options_label.setToolTip(
            "\n".join(
                f"allow {r.category}: {_money(r.weekly_cost)}/week "
                f"(saves {_money(r.saves)})"
                for r in advice.options
            ) if others else ""
        )

    def _render_lines(self, plan: bill.Bill) -> None:
        """One widget row per item: what it is, what it gives, what it costs,
        where to buy it, and a link (GFP-123).

        A QListWidget row cannot hold a clickable link, so each row is a real
        widget set as the item's own. That is what lets the store and the ad
        link live ON the item instead of in a second panel repeating its name.
        """
        self.lines_list.clear()
        if not plan.lines:
            self.lines_list.addItem(placeholder(
                "No deal on offer can be priced per gram of protein for these "
                "preferences."
            ))
            return

        for line in plan.lines:
            item = QListWidgetItem(self.lines_list)
            row = self._line_row(line)
            item.setSizeHint(row.sizeHint())
            self.lines_list.addItem(item)
            self.lines_list.setItemWidget(item, row)

    def _line_row(self, line: bill.BillLine) -> QWidget:
        row = QWidget()
        box = QVBoxLayout(row)
        box.setContentsMargins(4, 4, 4, 4)
        box.setSpacing(1)

        food = f"  ·  {line.grams_food:.0f} g food" if line.grams_food else ""
        headline = QLabel(
            f"{line.item_name}  —  {line.grams_protein:.0f} g protein{food}"
            f"  ·  {_money(line.cost)}/day"
        )
        headline.setWordWrap(True)
        box.addWidget(headline)

        # The store and the link, on the same row as the item they belong to.
        if line.source_url:
            where = QLabel(
                f'<span style="color:#666;">{_store_tag(line.store)}</span> — '
                f'<a href="{line.source_url}">View ad</a>'
            )
            where.setTextFormat(Qt.RichText)
            where.setOpenExternalLinks(True)
        else:
            # GFP-38's rule, kept: no captured link is said plainly rather than
            # rendered as a dead "Buy now" that goes nowhere.
            where = QLabel(f"{_store_tag(line.store)} — no ad link captured")
            where.setTextFormat(Qt.PlainText)
            where.setStyleSheet("color: #666;")
        box.addWidget(where)
        return row

    def _render_excluded(self, plan: bill.Bill) -> None:
        """What was left out, and why (GFP-130).

        The panel used to show what the plan CONTAINS and stay silent about
        what it left out -- and silence is indistinguishable from "there was
        nothing else". A nutritionist looking at a plan with no beef in it
        could not tell whether beef was dear this week, whether the client is
        marked as not eating it, or whether nothing beef-ish could be priced.
        Three different situations, three different responses.

        Grouped and counted rather than listed: hundreds of rows would be
        ignored, and the question being answered is "why isn't X in here?",
        not "what is in the catalogue?".
        """
        reasons: list[str] = []
        allowed = self._checked_categories()
        if allowed:
            reasons.append(
                f"excluded by preference: everything outside "
                f"{', '.join(sorted(allowed))}"
            )
        if plan.excluded_deals:
            reasons.append(
                f"{plan.excluded_deals} deal"
                f"{'' if plan.excluded_deals == 1 else 's'} could not be priced "
                "per gram of protein"
            )
        priced_but_unused = max(0, plan.considered_deals - len(plan.lines))
        if priced_but_unused:
            reasons.append(
                f"{priced_but_unused} priced deal"
                f"{'' if priced_but_unused == 1 else 's'} lost to something cheaper"
            )

        if not reasons:
            self.excluded_label.setText("")
            self.excluded_label.setVisible(False)
            return
        self.excluded_label.setText("Not included — " + "; ".join(reasons) + ".")
        self.excluded_label.setVisible(True)

    def _render(self, comparison: bill.BillComparison) -> None:
        self.comparison = comparison
        plan = comparison.constrained
        self.headline.setText(f"{_money(plan.total_cost)}/day")
        self._render_week(plan)


        if comparison.is_constrained:
            delta = comparison.delta_cost
            baseline = _money(comparison.baseline.total_cost)
            if abs(delta) < 0.005:
                # Half a cent apart is the same plan by another route; naming a
                # "+$0.00 penalty" for it would invent a cost that isn't there.
                self.comparison_label.setText(
                    f"Cheapest {baseline}/day — no extra cost."
                )
            else:
                sign = "+" if delta > 0 else "−"
                # No longer repeats the plan's own figure: it is the
                # headline directly above this line.
                self.comparison_label.setText(
f"Cheapest {baseline}/day  ({sign}{_money(abs(delta))})"
                )
        else:
            # Nothing ticked: the two solves are the same, so one figure.
            # GFP-124: was two sentences. The checkbox row directly beneath
            # already demonstrates what the preferences do, so explaining it in
            # words was telling the user something the UI was showing them.
            self.comparison_label.setText("Cheapest way to hit the target.")

        self.caveat_label.setText(comparison.caveat)
        self.caveat_label.setVisible(bool(comparison.caveat))

        # --- itemised lines: ONE row per item, carrying everything ------- #
        #
        # GFP-123. This used to be two panels side by side -- "What makes it
        # up" here and "Where to buy" in a third column -- listing the SAME
        # items in different words. The user read them as two unrelated boxes,
        # which is exactly what two bordered rectangles of equal weight say.
        #
        # Condensed into one panel, that duplication could not survive: every
        # fact both panels carried is now on a single row, and nothing is said
        # twice.
        self._render_lines(plan)

        # --- what did not make it in ------------------------------------- #
        parts = [
            f"{plan.covered_grams:.0f} of {plan.target_grams:.0f} g protein covered"
        ]
        if not plan.is_complete:
            parts.append(f"{plan.shortfall_grams:.0f} g short")
        priced = plan.considered_deals
        parts.append(f"{priced} deal{'' if priced == 1 else 's'} priced")
        self.footer.setText(" · ".join(parts) + ".")
        # The unpriceable count moved into the excluded panel, where it sits
        # beside the other reasons something is missing instead of trailing the
        # coverage line as an unexplained number (GFP-130).
        self._render_excluded(plan)
        self.bill_changed.emit()
