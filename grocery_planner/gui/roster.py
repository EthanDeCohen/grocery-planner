"""Client roster pane (GFP-36): the left half of the main window.

Search, the client list, and add-client. Selecting a client opens their detail
page (GFP-37) — this pane only says *which* client was chosen, via
:attr:`RosterPane.client_selected`, so the two can be built independently.

**Keyboard navigable**, which the ticket asks for by name: focus starts in the
search field, Down/Up move into and through the list without leaving the
keyboard, and Enter opens the highlighted client — or the only match, when
there is exactly one and focus is still in the search box. A nutritionist
typing a client's name mid-conversation should never have to reach for
the mouse.

Weights are shown in the unit the nutritionist entered (``weight_display``),
never silently normalised to kilograms on screen — the GFP-28/GFP-29 rule.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import db, targets
from ..customers import DEFAULT_PROTEIN_FACTOR, KG, LB, Customer, CustomerRepository
from .widgets import placeholder


class AddClientDialog(QDialog):
    """Enough of a client to be useful; the rest is GFP-51's biometrics panel.

    Name is the only required field. A weight is optional but, when given, its
    unit is mandatory and explicit — ``Customer.create`` converts to canonical
    kilograms here rather than anywhere downstream, which is the whole point of
    the GFP-28 split.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add client")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Full name")
        form.addRow("Name:", self.name_edit)

        weight_row = QHBoxLayout()
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(0.0, 1000.0)
        self.weight_spin.setDecimals(1)
        self.weight_spin.setSpecialValueText("not on file")  # 0 means "unknown"
        weight_row.addWidget(self.weight_spin, 1)
        self.unit_box = QComboBox()
        self.unit_box.addItem("kg", KG)
        self.unit_box.addItem("lb", LB)
        weight_row.addWidget(self.unit_box)
        container = QWidget()
        container.setLayout(weight_row)
        form.addRow("Weight:", container)

        self.factor_spin = QDoubleSpinBox()
        self.factor_spin.setRange(0.1, 5.0)
        self.factor_spin.setSingleStep(0.1)
        self.factor_spin.setDecimals(2)
        self.factor_spin.setValue(DEFAULT_PROTEIN_FACTOR)
        self.factor_spin.setToolTip("Grams of protein per kilogram of body weight per day.")
        form.addRow("Protein factor:", self.factor_spin)

        layout.addLayout(form)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.saved: Customer | None = None

    def on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.message.setText("A client needs a name.")
            return
        weight = self.weight_spin.value() or None
        customer = Customer.create(
            name,
            weight=weight,
            weight_unit=self.unit_box.currentData() if weight else None,
            protein_factor=self.factor_spin.value(),
        )
        self.saved = CustomerRepository.save(customer, conn=db.connect())
        self.accept()


class RosterPane(QWidget):
    """Searchable, keyboard-navigable list of clients, plus add-client."""

    #: Emitted with a customer id when a client is chosen (Enter or double-click).
    client_selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.title = QLabel("Clients")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)

        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search clients…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.reload)
        self.search_edit.returnPressed.connect(self.on_search_entered)
        self.search_edit.installEventFilter(self)
        top.addWidget(self.search_edit, 1)

        self.add_btn = QPushButton("Add client…")
        self.add_btn.clicked.connect(self.on_add_client)
        top.addWidget(self.add_btn)
        layout.addLayout(top)

        self.client_list = QListWidget()
        self.client_list.itemActivated.connect(self.on_item_activated)
        self.client_list.itemDoubleClicked.connect(self.on_item_activated)
        layout.addWidget(self.client_list, 1)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.search_edit.setFocus()
        self.reload()

    # ----------------------------------------------------------------- #
    def reload(self) -> None:
        conn = db.connect()
        search = self.search_edit.text().strip()
        self.client_list.clear()
        clients = CustomerRepository.list(search=search, conn=conn)
        # GFP-104 checked this pane for the trends pane's problem -- chrome
        # sitting over no data -- and it does NOT have it: the empty state is
        # already one clear sentence naming the one useful action. Hiding the
        # search box as well was tried and reverted: it governs nothing with
        # zero clients, but a hidden widget cannot take focus, which breaks the
        # deliberate Down-arrow behaviour GFP-36 built into this list. Not worth
        # a working keyboard path.
        if not clients:
            self.client_list.addItem(placeholder(
                "No clients match this search."
                if search else
                "No clients yet — press “Add client…” to start."
            ))
            return
        for client in clients:
            item = QListWidgetItem(self._label(client, conn))
            item.setData(Qt.UserRole, client.id)
            self.client_list.addItem(item)

    def _label(self, client: Customer, conn) -> str:
        """Name plus the number the product is about, or plainly nothing.

        A client with no weight on file gets no invented target — GFP-29's rule
        — so the row says the weight is missing instead of showing a figure
        derived from a guess.
        """
        target = targets.protein_target_for(client, conn=conn)
        if target is None:
            return f"{client.name} — weight not on file"
        weight = client.weight_display
        unit = client.weight_unit or ""
        return (
            f"{client.name} — {target.daily_grams:.0f} {target.daily_unit}"
            f"  ({weight:g} {unit})"
        )

    # ----------------------------------------------------------------- #
    # Keyboard navigation (the ticket's second acceptance criterion)
    # ----------------------------------------------------------------- #
    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """Down/Up in the search field steps into the list rather than dead-ending.

        An event filter on the field itself rather than a ``keyPressEvent`` on
        this pane: a key only reaches the parent when the child ignores it, and
        whether ``QLineEdit`` ignores an arrow key is its business, not
        something this pane should depend on.
        """
        if watched is self.search_edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up) and self.selectable_count():
                self.focus_list()
                return True
        return super().eventFilter(watched, event)

    def focus_list(self) -> None:
        """Move focus into the client list, selecting the first row if none is."""
        self.client_list.setFocus()
        if self.client_list.currentRow() < 0:
            self.client_list.setCurrentRow(0)

    def selectable_count(self) -> int:
        """Real client rows, ignoring the unselectable placeholder."""
        return sum(
            1 for i in range(self.client_list.count())
            if self.client_list.item(i).data(Qt.UserRole) is not None
        )

    def on_search_entered(self) -> None:
        """Enter in the search box opens the only match, or moves into the list."""
        if self.selectable_count() == 1:
            self.on_item_activated(self.client_list.item(0))
        elif self.selectable_count() > 1:
            self.focus_list()

    def on_item_activated(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        customer_id = item.data(Qt.UserRole)
        if customer_id is not None:
            self.client_selected.emit(int(customer_id))

    # ----------------------------------------------------------------- #
    def on_add_client(self) -> None:
        dialog = AddClientDialog(self)
        if dialog.exec() != QDialog.Accepted or dialog.saved is None:
            return
        self.search_edit.clear()   # a new client must be visible after adding
        self.reload()
        self.select_client(dialog.saved.id)
        self.message.setText(f"Added {dialog.saved.name}.")

    def select_client(self, customer_id: int | None) -> None:
        """Highlight one client by id, if they are in the current list."""
        for index in range(self.client_list.count()):
            if self.client_list.item(index).data(Qt.UserRole) == customer_id:
                self.client_list.setCurrentRow(index)
                return
