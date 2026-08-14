# ######### decohen-partners ##########
# Protein Ledger
"""Formula editor (GFP-35) — the GFP-11 Formulas tab, now a dialog.

GFP-35 retires the tab layout, but formula editing has to survive it: the
daily protein target is itself a stored formula (GFP-29, ``protein_factor``),
so losing this editor would cost the nutritionist the one number the whole
product is built on. It moves to Settings ▸ Formulas… unchanged in behaviour.

"Rank deals with this" used to repaint the Deals table; with that table gone
it previews the top-scoring deals inside the dialog instead, which is what the
button was really for — checking that a formula ranks the way you meant.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)
from simpleeval import simple_eval

from .. import db, formulas, service
from ..savings import DEAL_SCORE_VARS
from ..stores import BY_KEY
from ..targets import PROTEIN_TARGET_VARS
from .widgets import placeholder

RANK_PREVIEW_LIMIT = 20


def _formula_probe(conn) -> dict[str, float]:
    """Stand-in values used to validate a formula before it is saved.

    GFP-64: a formula is not only ever scored by ``savings.score_deals``
    (deal-ranking vars: ``price``, ``sale_price``, ...) -- GFP-29 also
    scores the ``protein_target_daily`` formula via ``targets`` (customer
    vars: ``weight_kg``, ``protein_factor``). A probe hand-maintained here
    covering only the first set silently rejects every valid formula that
    uses the second, which is exactly the bug this fixes: a nutritionist
    could not save a protein-target formula through the GUI at all.

    So this is built from each consumer's own published variable-name list
    (``savings.DEAL_SCORE_VARS``, ``targets.PROTEIN_TARGET_VARS``) plus the
    live profile context every formula consumer also merges in
    (``formulas._profile_context``), rather than a second hand-written dict
    that can drift out of sync with what those consumers actually supply --
    which is how this bug happened in the first place.
    """
    probe: dict[str, float] = {name: 1.0 for name in formulas._profile_context(conn)}
    probe.update({name: 1.0 for name in DEAL_SCORE_VARS})
    probe.update({name: 1.0 for name in PROTEIN_TARGET_VARS})
    return probe


class FormulaDialog(QDialog):
    """Create, validate, delete and try out the GFP-8 scoring formulas."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Formulas")
        self.resize(680, 520)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Expressions scored against each deal (price, unit_price, quantity, "
            "saved_percent) and your profile values."
        ))

        self.formula_list = QListWidget()
        self.formula_list.setMaximumHeight(180)
        self.formula_list.currentItemChanged.connect(self.on_formula_selected)
        layout.addWidget(self.formula_list)

        form = QHBoxLayout()
        form.addWidget(QLabel("Name:"))
        self.formula_name = QLineEdit()
        self.formula_name.setMaximumWidth(200)
        form.addWidget(self.formula_name)
        form.addWidget(QLabel("Expression:"))
        self.formula_expression = QLineEdit()
        self.formula_expression.setPlaceholderText("1 / unit_price")
        form.addWidget(self.formula_expression, 1)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.formula_save_btn = QPushButton("Save")
        self.formula_save_btn.clicked.connect(self.on_formula_save)
        actions.addWidget(self.formula_save_btn)
        self.formula_delete_btn = QPushButton("Delete")
        self.formula_delete_btn.clicked.connect(self.on_formula_delete)
        actions.addWidget(self.formula_delete_btn)
        self.formula_rank_btn = QPushButton("Rank deals with this")
        self.formula_rank_btn.clicked.connect(self.on_formula_rank)
        actions.addWidget(self.formula_rank_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        layout.addWidget(QLabel("Top-scoring deals:"))
        self.ranked_list = QListWidget()
        layout.addWidget(self.ranked_list, 1)

        self.formula_message = QLabel("")
        self.formula_message.setWordWrap(True)
        layout.addWidget(self.formula_message)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload_formulas()
        self._clear_ranking()

    # ----------------------------------------------------------------- #
    def reload_formulas(self) -> None:
        self.formula_list.clear()
        rows = formulas.list_formulas(db.connect())
        if not rows:
            self.formula_list.addItem(placeholder(
                "No formulas yet — name one below and Save."
            ))
            return
        for row in rows:
            item = QListWidgetItem(f"{row['name']}  =  {row['expression']}")
            item.setData(Qt.UserRole, (row["name"], row["expression"]))
            self.formula_list.addItem(item)

    def on_formula_selected(self, current, _previous=None) -> None:
        if current is None or current.data(Qt.UserRole) is None:
            return  # the placeholder row carries no formula
        name, expression = current.data(Qt.UserRole)
        self.formula_name.setText(name)
        self.formula_expression.setText(expression)

    def on_formula_save(self) -> None:
        name = self.formula_name.text().strip()
        expression = self.formula_expression.text().strip()
        if not name or not expression:
            self.formula_message.setText("Give the formula a name and an expression.")
            return
        # Validate before storing: a formula that cannot evaluate is not saved.
        try:
            simple_eval(expression, names=_formula_probe(db.connect()))
        except Exception as exc:
            self.formula_message.setText(f"Not saved — {type(exc).__name__}: {exc}")
            return
        formulas.set_formula(db.connect(), name, expression)
        self.formula_message.setText(f"Saved {name!r}.")
        self.reload_formulas()

    def on_formula_delete(self) -> None:
        name = self.formula_name.text().strip()
        if not name:
            return
        conn = db.connect()
        conn.execute("DELETE FROM formulas WHERE name=?", (name,))
        conn.commit()
        self.formula_name.clear()
        self.formula_expression.clear()
        self.formula_message.setText(f"Deleted {name!r}.")
        self.reload_formulas()
        self._clear_ranking()

    def on_formula_rank(self) -> None:
        """Preview the deals this formula scores highest, best first."""
        name = self.formula_name.text().strip()
        if not name:
            return
        try:
            ranked = service.best_deals(limit=RANK_PREVIEW_LIMIT, score_with=name)
        except KeyError:
            self.formula_message.setText(f"Save {name!r} first.")
            return
        self.ranked_list.clear()
        if not ranked:
            self._clear_ranking(f"{name!r} scored nothing — there are no current deals.")
            return
        for row in ranked:
            store = BY_KEY.get(row.get("store"))
            self.ranked_list.addItem(
                f"{row['score']:.3f}   {store.display_name if store else row.get('store', '')}"
                f"   {row.get('item_name', '')}"
            )
        self.formula_message.setText(
            f"Top {len(ranked)} deals by {name!r}. "
            "`gplan best --score-with` gives the same ranking with filters."
        )

    def _clear_ranking(self, text: str = "Nothing ranked yet — pick a formula and "
                                         "press “Rank deals with this”.") -> None:
        self.ranked_list.clear()
        self.ranked_list.addItem(placeholder(text))
