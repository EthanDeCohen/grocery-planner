# ######### decohen-partners ##########
# Protein Ledger
"""Client roster pane (GFP-36, completed by GFP-33): the left half of the window.

Search, the client list, and the full add/edit/remove trio. Selecting a client
opens their detail page (GFP-37) — this pane only says *which* client was
chosen, via :attr:`RosterPane.client_selected`, so the two can be built
independently.

**One code path with the CLI (GFP-33).** Nothing in this module touches
``CustomerRepository``, ``db.connect()`` or SQL. Every read and every write
goes through :mod:`grocery_planner.service.clients` — the same functions
``gplan client add/edit/delete/restore`` call — so the two front ends cannot
drift the way the formula delete did (GUI-only inline SQL, no CLI equivalent).
Even the row text and the delete confirmation's wording come from the service
(``weight_label``/``target_label``/``describe_client``), because a confirmation
that names the client in one front end and shows a bare id in the other is that
same drift in miniature.

**Keyboard navigable**, which GFP-36 asks for by name: focus starts in the
search field, Down/Up move into and through the list without leaving the
keyboard, and Enter opens the highlighted client — or the only match, when
there is exactly one and focus is still in the search box. A nutritionist
typing a client's name mid-conversation should never have to reach for the
mouse.

**Removing a client takes three deliberate acts**, because a client record is
hand-typed during an intake conversation and, unlike a price, cannot be
re-scraped:

1. A client must be *selected* — the Remove button is disabled otherwise, so
   there is no "remove whatever happened to be highlighted".
2. :class:`ConfirmRemoveDialog` names the person, their weight and their target
   and defaults to Cancel, so a stray Return cancels rather than deletes. Its
   confirm button reads "Remove <name>", not "OK".
3. Only then does the pane pass ``confirm=True`` to
   :func:`grocery_planner.service.clients.delete_client`, whose removal is a
   *soft* delete — and the pane keeps an Undo button on screen afterwards,
   wired to ``restore_client``, for the case where all three acts were carried
   out and the answer was still wrong.

Weights are shown in the unit the nutritionist entered, never silently
normalised to kilograms on screen — the GFP-28/GFP-29 rule. What the unit
selector *does* depends on whether there is a weight on file to restate; see
:meth:`ClientDialog._on_unit_changed`.
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

from ..customers import DEFAULT_PROTEIN_FACTOR, KG, LB, Customer
from ..service import clients as client_service
from .widgets import placeholder


class ClientDialog(QDialog):
    """Add a client, or edit the fields the roster itself shows.

    One dialog for both so "what a client is" is described once: passing
    ``client=`` prefills it and turns Save into an edit. The deeper record
    (height, age, activity, goal, notes) stays with GFP-51's biometrics panel —
    this is the intake-conversation subset, plus a rename.

    Name is the only required field. A weight is optional but, when given, its
    unit is explicit and travels with it into
    :func:`~grocery_planner.service.clients.create_client` /
    :func:`~grocery_planner.service.clients.update_client`, which do the
    conversion to canonical kilograms. This dialog never computes a
    ``weight_kg`` of its own — that is the whole point of the GFP-28 split.
    """

    def __init__(self, parent=None, client: Customer | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Add client" if client is None else f"Edit {client.name}")
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

        # The unit the number currently on screen is written in. Tracked
        # separately from the combo box so _on_unit_changed knows what it is
        # converting *from*.
        self._active_unit = KG
        # Has a person changed the weight, as opposed to this dialog filling it
        # in? Only a typed weight is sent back for conversion — see on_save.
        self._weight_edited = False
        if client is not None:
            self._load(client)
        self.unit_box.currentIndexChanged.connect(self._on_unit_changed)
        self.weight_spin.valueChanged.connect(self._on_weight_edited)

    # ------------------------------------------------------------------ #
    def _load(self, client: Customer) -> None:
        """Prefill from an existing client, in the unit they were entered in."""
        self.name_edit.setText(client.name)
        # A weightless client has no unit either; kg is only a starting point
        # for the selector and says nothing about the (still absent) weight.
        self._active_unit = client.weight_unit or KG
        self.weight_spin.setValue(client.weight_display or 0.0)
        self.unit_box.setCurrentIndex(self.unit_box.findData(self._active_unit))
        self.factor_spin.setValue(client.protein_factor)

    def _on_weight_edited(self, _value: float) -> None:
        self._weight_edited = True

    def _on_unit_changed(self, _index: int) -> None:
        """What the unit selector means depends on whether a weight is on file.

        * Editing a client who *has* a weight: the selector restates it. 90 kg
          shown as lb is 198.4 — the person did not change because a combo box
          moved. This is what GFP-51's biometrics panel does, and an edit that
          behaved differently in two places would be the drift GFP-33 is about.
        * Adding a client (or one with no weight yet): there is nothing to
          restate. The number was just typed and the selector is what says
          which unit it was typed in, so it is left exactly as entered —
          converting a freshly typed 195 into 429.9 lb would be absurd.
        """
        new_unit = self.unit_box.currentData()
        if new_unit == self._active_unit:
            return
        on_file = self.client is not None and self.client.weight_kg is not None
        displayed = self.weight_spin.value()
        if on_file and displayed:  # 0 is "not on file" — nothing to convert
            self.weight_spin.blockSignals(True)
            self.weight_spin.setValue(
                client_service.restate_weight(displayed, self._active_unit, new_unit)
            )
            self.weight_spin.blockSignals(False)
        self._active_unit = new_unit

    def on_save(self) -> None:
        """Create or update through the service layer, or explain the refusal.

        A rejected save leaves the dialog open with its message filled in
        rather than closing: the typed values are the only copy of what the
        nutritionist just heard, and closing would throw them away.
        """
        weight = self.weight_spin.value() or None
        unit = self.unit_box.currentData()
        try:
            if self.client is None:
                self.saved = client_service.create_client(
                    self.name_edit.text(),
                    weight=weight,
                    weight_unit=unit if weight is not None else None,
                    protein_factor=self.factor_spin.value(),
                )
            else:
                # An untouched weight is sent as UNSET, not as the number in the
                # spinbox: the spinbox shows one decimal place, so re-saving it
                # would quietly round the stored mass every time somebody opened
                # this dialog to fix a spelling (90 kg -> 198.4 lb -> 89.993 kg).
                # A unit change on its own is still forwarded, which restates
                # the weight for display and leaves weight_kg untouched.
                weight_arg = weight if self._weight_edited else client_service.UNSET
                will_have_weight = weight if self._weight_edited else self.client.weight_kg
                if will_have_weight is None:
                    # No weight to interpret, so no unit to state: a unit with
                    # no weight describes nothing (GFP-28).
                    unit_arg = client_service.UNSET
                elif not self._weight_edited and unit == self.client.weight_unit:
                    unit_arg = client_service.UNSET       # nothing changed
                else:
                    unit_arg = unit
                self.saved = client_service.update_client(
                    self.client.id,
                    name=self.name_edit.text(),
                    weight=weight_arg,
                    weight_unit=unit_arg,
                    protein_factor=self.factor_spin.value(),
                )
        except client_service.ClientError as exc:
            self.message.setText(str(exc))
            return
        self.accept()


#: GFP-36's name for the add-only version of the dialog above, kept because
#: "add a client" is still a distinct thing to reach for even though adding and
#: editing are now one class.
AddClientDialog = ClientDialog


class ConfirmRemoveDialog(QDialog):
    """"Remove Jane Doe?" — never "remove record 4?".

    Its own dialog rather than a ``QMessageBox.question`` for two reasons that
    matter for irreplaceable data: the confirm button is labelled with the
    client's *name* (so the last thing read before removing says who), and
    Cancel is the default button, so Return — the key most likely to be hit by
    reflex — cancels.
    """

    def __init__(self, client: Customer, target=None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Remove client")
        layout = QVBoxLayout(self)

        self.detail = QLabel(client_service.describe_client(client, target))
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.warning = QLabel(
            "Client records are typed in by hand and cannot be re-collected "
            "the way prices can. This one can be brought back with Undo, or "
            f"with “gplan client restore {client.id}”."
        )
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)

        buttons = QDialogButtonBox()
        self.cancel_btn = buttons.addButton(QDialogButtonBox.Cancel)
        self.remove_btn = buttons.addButton(
            f"Remove {client.name}", QDialogButtonBox.DestructiveRole
        )
        self.remove_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        # Explicitly, not by luck of layout order: the safe button is the one
        # Return activates.
        self.remove_btn.setAutoDefault(False)
        self.remove_btn.setDefault(False)
        self.cancel_btn.setAutoDefault(True)
        self.cancel_btn.setDefault(True)
        self.cancel_btn.setFocus()
        layout.addWidget(buttons)


class RosterPane(QWidget):
    """Searchable, keyboard-navigable list of clients, plus add/edit/remove."""

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
        self.client_list.currentItemChanged.connect(self._sync_actions)
        layout.addWidget(self.client_list, 1)

        actions = QHBoxLayout()
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.clicked.connect(self.on_edit_client)
        actions.addWidget(self.edit_btn)
        self.remove_btn = QPushButton("Remove…")
        self.remove_btn.setToolTip("Remove the selected client (recoverable).")
        self.remove_btn.clicked.connect(self.on_remove_client)
        actions.addWidget(self.remove_btn)
        actions.addStretch(1)
        # The undo of the last removal. Hidden rather than disabled when there
        # is nothing to undo: a permanently visible Undo invites a click that
        # resurrects a client removed ten minutes and three selections ago.
        self.undo_btn = QPushButton("Undo remove")
        self.undo_btn.clicked.connect(self.on_undo_remove)
        self.undo_btn.hide()
        actions.addWidget(self.undo_btn)
        layout.addLayout(actions)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self._undo_id: int | None = None
        self.search_edit.setFocus()
        self.reload()
        self._sync_actions()

    # ----------------------------------------------------------------- #
    def reload(self) -> None:
        """Redraw the list from the service layer's client summaries."""
        search = self.search_edit.text().strip()
        self.client_list.clear()
        summaries = client_service.list_client_summaries(search=search)
        # GFP-104 checked this pane for the trends pane's problem -- chrome
        # sitting over no data -- and it does NOT have it: the empty state is
        # already one clear sentence naming the one useful action. Hiding the
        # search box as well was tried and reverted: it governs nothing with
        # zero clients, but a hidden widget cannot take focus, which breaks the
        # deliberate Down-arrow behaviour GFP-36 built into this list. Not worth
        # a working keyboard path.
        if not summaries:
            self.client_list.addItem(placeholder(
                "No clients match this search."
                if search else
                "No clients yet — press “Add client…” to start."
            ))
            self._sync_actions()
            return
        for summary in summaries:
            item = QListWidgetItem(self._label(summary))
            item.setData(Qt.UserRole, summary.client.id)
            self.client_list.addItem(item)
        self._sync_actions()

    def _label(self, summary: client_service.ClientSummary) -> str:
        """Name plus the number the product is about, or plainly nothing.

        A client with no weight on file gets no invented target — GFP-29's rule
        — so the row says the weight is missing instead of showing a figure
        derived from a guess.
        """
        client = summary.client
        if summary.target is None:
            return f"{client.name} — weight not on file"
        return (
            f"{client.name} — {client_service.target_label(summary.target)}"
            f"  ({client_service.weight_label(client)})"
        )

    # ----------------------------------------------------------------- #
    # Keyboard navigation (GFP-36's second acceptance criterion)
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
    # Selection
    # ----------------------------------------------------------------- #
    def selected_client_id(self) -> int | None:
        """The highlighted client's id, or ``None`` on the placeholder row."""
        item = self.client_list.currentItem()
        if item is None:
            return None
        customer_id = item.data(Qt.UserRole)
        return None if customer_id is None else int(customer_id)

    def selected_client(self) -> Customer | None:
        """The highlighted client, re-read so an edit acts on current data."""
        customer_id = self.selected_client_id()
        if customer_id is None:
            return None
        return client_service.get_client(customer_id, include_deleted=False)

    def _sync_actions(self, *_args: object) -> None:
        """Edit/Remove are live only while a real client is highlighted.

        The first guard against removing the wrong record: with nothing
        selected there is no "remove" to mis-click at all.
        """
        selected = self.selected_client_id() is not None
        self.edit_btn.setEnabled(selected)
        self.remove_btn.setEnabled(selected)

    # ----------------------------------------------------------------- #
    # Writes -- all through grocery_planner.service.clients
    # ----------------------------------------------------------------- #
    def on_add_client(self) -> None:
        dialog = ClientDialog(self)
        if dialog.exec() != QDialog.Accepted or dialog.saved is None:
            return
        self.search_edit.clear()   # a new client must be visible after adding
        self.reload()
        self.select_client(dialog.saved.id)
        self._set_message(f"Added {dialog.saved.name}.")

    def on_edit_client(self) -> None:
        client = self.selected_client()
        if client is None:
            return
        dialog = ClientDialog(self, client=client)
        if dialog.exec() != QDialog.Accepted or dialog.saved is None:
            return
        self.reload()
        self.select_client(dialog.saved.id)
        self._set_message(f"Updated {dialog.saved.name}.")

    def on_remove_client(self) -> None:
        """Confirm by name, then soft-delete, then offer Undo.

        ``confirm=True`` is passed only on the far side of an accepted
        :class:`ConfirmRemoveDialog`; the service refuses to delete without it,
        so a future caller that forgets the dialog gets an exception rather
        than a silent removal.
        """
        client = self.selected_client()
        if client is None:
            return
        dialog = ConfirmRemoveDialog(client, client_service.client_target(client), self)
        if dialog.exec() != QDialog.Accepted:
            self._set_message(f"{client.name} was not removed.")
            return
        try:
            removed = client_service.delete_client(client.id, confirm=True)
        except client_service.ClientError as exc:
            self._set_message(str(exc))
            return
        self.reload()
        self._undo_id = removed.id
        self.undo_btn.show()
        self._set_message(f"Removed {removed.name}.", keep_undo=True)

    def on_undo_remove(self) -> None:
        """Bring back the client removed a moment ago."""
        if self._undo_id is None:
            return
        restored = client_service.restore_client(self._undo_id)
        self.reload()
        self.select_client(restored.id)
        self._set_message(f"Brought back {restored.name}.")

    def _set_message(self, text: str, keep_undo: bool = False) -> None:
        """Say what just happened, and retire a stale Undo while doing it."""
        self.message.setText(text)
        if not keep_undo:
            self._undo_id = None
            self.undo_btn.hide()

    def select_client(self, customer_id: int | None) -> None:
        """Highlight one client by id, if they are in the current list."""
        for index in range(self.client_list.count()):
            if self.client_list.item(index).data(Qt.UserRole) == customer_id:
                self.client_list.setCurrentRow(index)
                return
