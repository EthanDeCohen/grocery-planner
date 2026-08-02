"""Client detail page (GFP-37): the page the nutritionist actually looks at.

Three columns, each its own module so they could be built independently:

- :mod:`.biometrics` — biometrics, photo placeholder and the required protein
  headline (GFP-51).
- :mod:`.billpanel` — the daily protein bill, its preference checkboxes and
  its itemised lines (GFP-52, over the GFP-48/GFP-49 engine).
- :mod:`.wheretobuy` — where each contributing line can be bought (GFP-38).

This module owns only what joins them: the header, back-navigation to the
roster, and the recompute wiring.

**Any biometric or preference edit recomputes the bill immediately** -- the
ticket's second acceptance criterion, and the whole reason the columns are
separate widgets rather than one form. ``BiometricsPanel.client_changed``
fires on save and this page turns that into a bill recompute; the bill panel
recomputes itself on a checkbox toggle and hands its new lines to the
where-to-buy column. Neither side column knows the other exists, which is
what let them be written in parallel.

**Per-client pie chart is explicitly out of scope for v1** (the ticket says
so); the price-trends chart on the roster page covers the "is this getting
cheaper" question without a second chart here.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import db, targets
from ..customers import CustomerRepository
from .billpanel import BillPanel
from .biometrics import BiometricsPanel
from .wheretobuy import WhereToBuyPane

#: Column widths. The bill is the widest because it is what the page is for.
COLUMN_SIZES = (300, 420, 260)


class ClientDetailPage(QWidget):
    """Biometrics, daily bill and where-to-buy for one client."""

    #: Emitted when the user wants the roster back.
    back_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.customer_id: int | None = None
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.back_btn = QPushButton("← Clients")
        self.back_btn.setShortcut("Alt+Left")
        self.back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_btn)

        self.name_label = QLabel("")
        font = self.name_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 3)
        self.name_label.setFont(font)
        header.addWidget(self.name_label, 1)
        layout.addLayout(header)

        self.target_label = QLabel("")
        layout.addWidget(self.target_label)

        # --- the three columns ------------------------------------------- #
        self.biometrics = BiometricsPanel()
        self.bill_panel = BillPanel()
        self.where_to_buy = WhereToBuyPane()

        self.columns = QSplitter()
        self.columns.addWidget(self.biometrics)
        self.columns.addWidget(self.bill_panel)
        self.columns.addWidget(self.where_to_buy)
        self.columns.setSizes(list(COLUMN_SIZES))
        layout.addWidget(self.columns, 1)

        # A biometric save changes the target, which changes the bill, which
        # changes where to buy. One edit, one chain, no manual refresh.
        self.biometrics.client_changed.connect(self._on_biometrics_saved)
        # A preference toggle recomputes the bill inside BillPanel itself, so
        # the third column follows the bill's own signal rather than this page
        # having to guess when a checkbox changed something.
        self.bill_panel.bill_changed.connect(self._sync_where_to_buy)

    # ----------------------------------------------------------------- #
    def show_client(self, customer_id: int) -> bool:
        """Load one client into all three columns. False if there is no such client."""
        conn = db.connect()
        customer = CustomerRepository.get(customer_id, include_deleted=False, conn=conn)
        if customer is None:
            self.customer_id = None
            self.name_label.setText("Client not found")
            self.target_label.setText("")
            self.biometrics.clear()
            self.bill_panel.clear()
            self.where_to_buy.clear()
            return False

        self.customer_id = customer_id
        self.name_label.setText(customer.name)
        self._render_target(customer, conn)
        self.biometrics.set_client(customer_id)
        self.bill_panel.set_client(customer_id)
        self._sync_where_to_buy()
        return True

    def _render_target(self, customer, conn) -> None:
        target = targets.protein_target_for(customer, conn=conn)
        if target is None:
            # GFP-29's rule: no weight on file means no target, never a guess.
            self.target_label.setText(
                "No weight on file yet, so there is no protein target to compute."
            )
            return
        weight = customer.weight_display
        self.target_label.setText(
            f"{target.daily_grams:.0f} {target.daily_unit} "
            f"({target.weekly_grams:.0f} {target.weekly_unit}) "
            f"— {weight:g} {customer.weight_unit} × {customer.protein_factor:g}"
        )

    # ----------------------------------------------------------------- #
    def _on_biometrics_saved(self, customer_id: int) -> None:
        """A saved biometric edit re-derives the target, the bill and the ads."""
        conn = db.connect()
        customer = CustomerRepository.get(customer_id, include_deleted=False, conn=conn)
        if customer is None:
            return
        self.name_label.setText(customer.name)
        self._render_target(customer, conn)
        self.bill_panel.reload()
        self._sync_where_to_buy()

    def _sync_where_to_buy(self) -> None:
        """Hand the bill's current lines to the where-to-buy column."""
        comparison = self.bill_panel.comparison
        self.where_to_buy.set_lines(comparison.constrained.lines if comparison else [])

    def reload(self) -> None:
        """Recompute everything for the current client — e.g. after a scrape."""
        if self.customer_id is not None:
            self.show_client(self.customer_id)
