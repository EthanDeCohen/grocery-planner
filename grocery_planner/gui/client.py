"""Client detail page — the navigation target, not yet the page (GFP-36).

GFP-36 owns "selecting a client opens their detail page", so the roster needs
somewhere to open *to*. The page itself is GFP-37, with its three columns
filled in by GFP-51 (biometrics), GFP-52 (daily bill) and GFP-38 (where to
buy). This is the seam between them: the header and back-navigation GFP-36 is
responsible for, and an explicitly-unbuilt body for GFP-37 to replace.

It shows the client's name and protein target rather than nothing, so the
navigation is verifiably working — and so an empty page is never mistaken for
a client whose record failed to load.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import db, targets
from ..customers import CustomerRepository

BODY_PLACEHOLDER = (
    "The client detail page is built here: biometrics and photo, the daily "
    "protein bill with preferences and store tags, and where to buy."
)


class ClientDetailPage(QWidget):
    """Header, back-navigation and a placeholder body for GFP-37 to fill."""

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

        layout.addStretch(1)
        self.body = QLabel(BODY_PLACEHOLDER)
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setWordWrap(True)
        layout.addWidget(self.body)
        layout.addStretch(1)

    def show_client(self, customer_id: int) -> bool:
        """Load one client into the page. False if there is no such client."""
        conn = db.connect()
        customer = CustomerRepository.get(customer_id, include_deleted=False, conn=conn)
        if customer is None:
            self.customer_id = None
            self.name_label.setText("Client not found")
            self.target_label.setText("")
            return False

        self.customer_id = customer_id
        self.name_label.setText(customer.name)
        target = targets.protein_target_for(customer, conn=conn)
        if target is None:
            # GFP-29's rule: no weight on file means no target, never a guess.
            self.target_label.setText(
                "No weight on file yet, so there is no protein target to compute."
            )
        else:
            weight = customer.weight_display
            self.target_label.setText(
                f"{target.daily_grams:.0f} {target.daily_unit} "
                f"({target.weekly_grams:.0f} {target.weekly_unit}) "
                f"— {weight:g} {customer.weight_unit} × {customer.protein_factor:g}"
            )
        return True
