# ######### decohen-partners ##########
# Protein Ledger
"""How to choose, as a panel (GFP-136 / GFP-137 / GFP-142).

The protein-preference checkboxes used to sit in the middle of the bill panel,
between the headline figure and the itemised list. Two problems with that: they
pushed the list down out of sight, and they answered a different question from
everything around them. The bill says *what this costs*; these controls say
*how to pick it*. So they move into their own column, with the new controls.

"Weekly plan" (GFP-142) sits with the constraints rather than the objective,
and deliberately: "lowest cost" still decides what to reach for, and Mix It Up
constrains what counts as an acceptable WEEK -- exactly as cover-all and
single-store constrain a day. It is rendered as its own radio pair only
because the two options are mutually exclusive, not because it is a rival
objective. Default Mix It Up: recommending one item seven days running is not
something a nutritionist would hand a client, so the varied week is the
professional default and the flat one is the opt-out.

**Constraints and objective are separate, and that is the point.** "Include
all" and "lowest price" were first described as rival modes, but lowest price
is an OBJECTIVE and include-all is a CONSTRAINT -- they compose. "Include all,
at the lowest price" is a sentence, and is what ticking two boxes was always
meant to produce.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import bill, db, nutrition, preferences

#: Checkbox columns. Enough to keep a dozen categories from becoming one tall
#: list, few enough that the labels stay readable in a narrow column.
COLUMNS = 2

#: How tall the preference list is allowed to get before it scrolls (GFP-316).
#: Enough rows to read as a list you scroll rather than a box that got cut off,
#: and small enough that the panel still fits a 1536x816 screen unmaximised.
CATEGORIES_MIN_HEIGHT = 120


class SelectionPanel(QWidget):
    """Protein preferences, plus the constraints and the objective."""

    #: Anything here changes the plan, so one signal covers the lot.
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.customer_id: int | None = None
        self._loading = False

        layout = QVBoxLayout(self)

        self.title = QLabel("Selection type")
        self.title.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.title)

        # --- the objective: exactly one ---------------------------------- #
        objective_box = QGroupBox("Optimise for")
        objective_layout = QVBoxLayout(objective_box)
        self.lowest_cost = QRadioButton("Lowest cost")
        self.lowest_cost.setChecked(True)
        self.lowest_cost.setToolTip("The cheapest way to hit the protein target.")
        self.within_budget = QRadioButton("Most protein within budget")
        self.within_budget.setToolTip(
            "Stop at the weekly budget and report how much protein is short.\n"
            "Only differs from lowest cost when the budget actually binds."
        )
        objective_layout.addWidget(self.lowest_cost)
        objective_layout.addWidget(self.within_budget)
        layout.addWidget(objective_box)

        # --- the week (GFP-142) ------------------------------------------ #
        week_box = QGroupBox("Weekly plan")
        week_layout = QVBoxLayout(week_box)
        self.repeat_cheapest = QRadioButton("Repeat Cheapest")
        self.repeat_cheapest.setToolTip(
            "The cheapest $/g every day, even if that is the same item all\n"
            "seven days. This is what the optimiser has always done."
        )
        self.mix_it_up = QRadioButton("Mix It Up")
        self.mix_it_up.setChecked(True)
        self.mix_it_up.setToolTip(
            "Vary the week rather than recommending one item seven days\n"
            "running. Costs more; never delivers less protein."
        )
        week_layout.addWidget(self.mix_it_up)
        week_layout.addWidget(self.repeat_cheapest)
        layout.addWidget(week_box)

        # --- the constraints: any combination ---------------------------- #
        constraint_box = QGroupBox("Must also")
        constraint_layout = QVBoxLayout(constraint_box)
        self.cover_all = QCheckBox("Include every protein I ticked")
        self.cover_all.setToolTip(
            "Spread the plan across every category below, instead of filling\n"
            "it all from whichever one is cheapest."
        )
        self.single_store = QCheckBox("Buy from one store only")
        self.single_store.setToolTip(
            "Avoid sending a client to three shops to save a little."
        )
        constraint_layout.addWidget(self.cover_all)
        constraint_layout.addWidget(self.single_store)
        layout.addWidget(constraint_box)

        # --- the preferences --------------------------------------------- #
        #
        # GFP-316: THE GRID MUST SCROLL, and this is not a cosmetic preference.
        # There is one checkbox per distinct sub-category in the deals table,
        # so the list is a function of how many sources are loaded -- it grew
        # 9 -> 201 as stores were added, with nothing in the code to notice.
        # Un-scrolled, that made this panel 3,129 px tall, and because
        # ClientDetailPage shares a QStackedWidget with the roster (a stack's
        # minimum is the max over ALL its pages, shown or not) the whole main
        # window inherited a minimum of 1950 x 3461. Taller than any screen, so
        # the window could not be resized down and only looked right maximised.
        #
        # A QScrollArea fixes it structurally rather than by hoping the list
        # stays short: its own minimum is the frame plus scrollbars, so nothing
        # the grid does propagates outward, at 9 categories or at 900.
        layout.addWidget(QLabel("Protein preferences:"))
        self.categories_widget = QWidget()
        self.categories_layout = QGridLayout(self.categories_widget)
        self.categories_layout.setContentsMargins(0, 0, 0, 0)
        self._boxes: dict[str, QCheckBox] = {}
        self._build_checkboxes()

        self.categories_scroll = QScrollArea()
        self.categories_scroll.setWidgetResizable(True)
        self.categories_scroll.setFrameShape(QFrame.NoFrame)
        self.categories_scroll.setMinimumHeight(CATEGORIES_MIN_HEIGHT)
        self.categories_scroll.setWidget(self.categories_widget)
        # Takes the panel's spare vertical space, which is why there is no
        # trailing addStretch: the list is the thing that should grow.
        layout.addWidget(self.categories_scroll, 1)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #666;")
        layout.addWidget(self.note)

        for widget in (self.lowest_cost, self.within_budget,
                       self.mix_it_up, self.repeat_cheapest):
            widget.toggled.connect(self._emit)
        for widget in (self.cover_all, self.single_store):
            widget.stateChanged.connect(self._emit)
        # The note describes the CONSTRAINTS as much as the preferences, so it
        # has to follow both. Without this it said "the others may not appear"
        # while "include every protein I ticked" was ticked -- the panel
        # contradicting itself on screen.
        self.cover_all.stateChanged.connect(lambda _s: self._describe())

    # ------------------------------------------------------------------ #
    def _build_checkboxes(self) -> None:
        """One box per OFFERABLE category.

        GFP-139: the broad buckets are not offered beside the specific kinds
        they contain. "Meat" sitting next to "beef" and "chicken" is vague --
        ticking it would mean "beef or chicken or pork or turkey or lamb",
        which nobody means to say in a list that already offers those
        individually.

        The bucket's FOOD is not lost, which is the part that had to be got
        right: nutrition.food_matches maps a specific kind onto foods filed
        only under the bucket (GFP-134), so ticking "chicken" still finds the
        208 rows categorised merely as "Meat" that ARE chicken.
        """
        buckets = set(nutrition.CATEGORY_MEMBERS)
        offered = [
            c for c in nutrition.list_categories(db.connect())
            if c and c.strip().lower() not in buckets
        ]
        for index, category in enumerate(sorted(offered, key=str.lower)):
            box = QCheckBox(category)
            box.stateChanged.connect(self._on_preference_toggled)
            self._boxes[category] = box
            self.categories_layout.addWidget(box, index // COLUMNS, index % COLUMNS)

    def _emit(self, *_args: object) -> None:
        if not self._loading:
            self.changed.emit()

    def checked_categories(self) -> list[str]:
        return sorted(name for name, box in self._boxes.items() if box.isChecked())

    def selection(self, daily_budget: float | None = None) -> bill.Selection:
        """The Selection these controls describe."""
        objective = (
            bill.Objective.MOST_PROTEIN_WITHIN_BUDGET
            if self.within_budget.isChecked()
            else bill.Objective.LOWEST_COST
        )
        return bill.Selection(
            cover_all_categories=self.cover_all.isChecked(),
            single_store=self.single_store.isChecked(),
            objective=objective,
            daily_budget=daily_budget,
            vary_week=self.mix_it_up.isChecked(),
        )

    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        self.customer_id = None
        self._loading = True
        try:
            for box in self._boxes.values():
                box.setChecked(False)
        finally:
            self._loading = False
        self.note.setText("No client selected.")

    def set_client(self, customer_id: int) -> None:
        self.customer_id = customer_id
        self._loading = True
        try:
            stored = set(preferences.list_preferences(customer_id, conn=db.connect()))
            for name, box in self._boxes.items():
                box.setChecked(name in stored)
        finally:
            self._loading = False
        self._describe()

    def _on_preference_toggled(self, _checked: bool) -> None:
        if self._loading or self.customer_id is None:
            return
        try:
            preferences.set_preferences(
                self.customer_id, self.checked_categories(), conn=db.connect()
            )
        except Exception as exc:            # noqa: BLE001
            self.note.setText(f"Preference not saved — {exc}")
            return
        self._describe()
        self.changed.emit()

    def _describe(self) -> None:
        """Say what the current controls mean, in words.

        Particularly where a control does nothing: the two objectives produce
        an IDENTICAL plan while the client is under budget, and a control that
        usually changes nothing invites people to fiddle with it looking for
        an effect.
        """
        chosen = self.checked_categories()
        if not chosen:
            self.note.setText("No preferences ticked, so every protein is considered.")
        elif self.cover_all.isChecked() and len(chosen) > 1:
            self.note.setText(
                "The plan will include every one of these, which usually costs "
                "more than filling it from the cheapest alone."
            )
        else:
            self.note.setText(
                "The cheapest of these will be used; the others may not appear."
            )
