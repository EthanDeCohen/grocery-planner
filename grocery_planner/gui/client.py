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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import db, service, targets
from ..customers import CustomerRepository
from .billpanel import BillPanel
from .clienttrend import ClientTrendPane
from ..budget import DAYS_PER_WEEK
from .selectionpanel import SelectionPanel
from .biometrics import BiometricsPanel
from .wheretobuy import WhereToBuyPane

def _format_for(path: str, selected_filter: str) -> str:
    """Which renderer to use, from the filename the user chose.

    The extension wins because it is what the saved file will actually be --
    a user who types "list.csv" into an HTML-filtered dialog means CSV. The
    dialog filter is only the fallback when they typed no extension at all,
    and HTML is the final default because it is the format with genuinely
    clickable links (GFP-112).
    """
    lowered = path.lower()
    for fmt, ext in service.EXTENSIONS.items():
        if lowered.endswith(ext):
            return fmt
    if "csv" in selected_filter.lower():
        return "csv"
    if "text" in selected_filter.lower():
        return "text"
    return "html"


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

        # GFP-112: the action the whole page exists to enable. Everything else
        # here TELLS the nutritionist something; this is the only control that
        # produces something they can hand to a client.
        self.grocery_btn = QPushButton("Create grocery list…")
        self.grocery_btn.setToolTip(
            "Turn this client's protein plan into a shopping list you can "
            "print, save as CSV, or open in a browser with clickable links."
        )
        self.grocery_btn.setAutoDefault(False)
        self.grocery_btn.clicked.connect(self.on_create_grocery_list)
        header.addWidget(self.grocery_btn)
        layout.addLayout(header)

        self.target_label = QLabel("")
        layout.addWidget(self.target_label)

        # --- the three columns ------------------------------------------- #
        self.biometrics = BiometricsPanel()
        self.bill_panel = BillPanel()
        self.where_to_buy = WhereToBuyPane()

        # GFP-137: biometrics goes in a DRAWER. It is a form a nutritionist
        # edits occasionally and reads rarely, whereas the bill and the chart
        # are what they look at during a consultation -- so it is the right
        # column to be able to put away. Before this the page needed ~1500px
        # to show everything and the CHART was what lost, which is the pane
        # that needs width most: a squeezed time series is unreadable in a way
        # a squeezed form is not.
        self.biometrics_drawer = QWidget()
        drawer_layout = QVBoxLayout(self.biometrics_drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        self.biometrics_toggle = QToolButton()
        self.biometrics_toggle.setText("Client details")
        self.biometrics_toggle.setCheckable(True)
        self.biometrics_toggle.setChecked(True)
        self.biometrics_toggle.setArrowType(Qt.DownArrow)
        self.biometrics_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.biometrics_toggle.toggled.connect(self._toggle_biometrics)
        drawer_layout.addWidget(self.biometrics_toggle)
        drawer_layout.addWidget(self.biometrics, 1)

        self.selection_panel = SelectionPanel()
        self.selection_panel.changed.connect(self._on_selection_changed)

        self.columns = QSplitter()
        self.columns.addWidget(self.biometrics_drawer)
        self.columns.addWidget(self.selection_panel)
        self.columns.addWidget(self.bill_panel)
        # GFP-123: where-to-buy is no longer a column. It was a second panel
        # listing the SAME items as the bill in different words, and two
        # bordered boxes of equal weight read as two unrelated things. The
        # store and the ad link now sit on each item's own row inside the bill.
        #
        # The pane itself is kept and still fed, because it is the only place
        # GFP-38's per-line denomination notes ("sold by weight (per lb)")
        # live; it is simply not in the layout. Hiding rather than deleting
        # keeps that behaviour available for a ticket that wants it back
        # without resurrecting it from history.
        self.where_to_buy.setVisible(False)

        # GFP-129: this client's prices against everybody's. Takes the third
        # column the where-to-buy pane vacated -- the page keeps two panes of
        # content, and the chart is the thing that page previously could not
        # answer at all, being entirely a snapshot.
        self.trend = ClientTrendPane()
        self.columns.addWidget(self.trend)
        # Every pane can shrink; none of them may vanish entirely, which is
        # what made the chart unreadable at a normal window size.
        for index in range(self.columns.count()):
            self.columns.setStretchFactor(index, 1)
        self.columns.setChildrenCollapsible(False)
        layout.addWidget(self.columns, 1)

        # A biometric save changes the target, which changes the bill, which
        # changes where to buy. One edit, one chain, no manual refresh.
        self.biometrics.client_changed.connect(self._on_biometrics_saved)
        # A preference toggle recomputes the bill inside BillPanel itself, so
        # the third column follows the bill's own signal rather than this page
        # having to guess when a checkbox changed something.
        self.bill_panel.bill_changed.connect(self._sync_where_to_buy)
        self.bill_panel.bill_changed.connect(self.trend.reload)

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
            self.trend.clear()
            self.selection_panel.clear()
            return False

        self.customer_id = customer_id
        self.name_label.setText(customer.name)
        self._render_target(customer, conn)
        self.biometrics.set_client(customer_id)
        # The selection panel first: the bill is computed FROM it, so loading
        # the other way round would price one client with the last one's
        # preferences for an instant.
        self.selection_panel.set_client(customer_id)
        self._push_selection(customer)
        self.bill_panel.set_client(customer_id)
        self.trend.set_client(customer_id)
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

    def _toggle_biometrics(self, open_: bool) -> None:
        """Open or close the details drawer, and say which state it is in."""
        self.biometrics.setVisible(open_)
        self.biometrics_toggle.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)

    def _push_selection(self, customer=None) -> None:
        """Hand the panel's constraints and objective to the bill.

        The weekly budget becomes a DAILY one here because the bill is a daily
        figure. Dividing rather than comparing weekly totals keeps one unit in
        the engine -- see budget.py for the week-level view.
        """
        target = customer if customer is not None else None
        weekly = getattr(target, "weekly_budget", None)
        daily = None if not weekly else weekly / DAYS_PER_WEEK
        selection = self.selection_panel.selection(daily_budget=daily)
        self.bill_panel.set_selection(
            self.selection_panel.checked_categories(), selection,
        )
        # GFP-144: the SAME Selection object goes to the chart. Handing the
        # bill one set of constraints and the chart another is exactly how the
        # two came to contradict each other on screen.
        self.trend.set_selection(selection)

    def _on_selection_changed(self) -> None:
        conn = db.connect()
        customer = (
            CustomerRepository.get(self.customer_id, conn=conn)
            if self.customer_id is not None else None
        )
        self._push_selection(customer)      # this reloads the chart too

    def _sync_where_to_buy(self) -> None:
        """Hand the bill's current lines to the where-to-buy column."""
        comparison = self.bill_panel.comparison
        self.where_to_buy.set_lines(comparison.constrained.lines if comparison else [])

    def reload(self) -> None:
        """Recompute everything for the current client — e.g. after a scrape."""
        if self.customer_id is not None:
            self.show_client(self.customer_id)

    # ----------------------------------------------------------------- #
    # GFP-112 — the grocery list
    # ----------------------------------------------------------------- #
    def on_create_grocery_list(self) -> None:
        """Ask where to save, then write the list in the chosen format.

        The format is taken from the file extension the user picks rather than
        from a separate dropdown: they are already choosing a filename, and a
        second control asking the same question twice is the kind of chrome
        GFP-104 removed from the trends pane.
        """
        if self.customer_id is None:
            return
        customer = CustomerRepository.get(self.customer_id, conn=db.connect())
        if customer is None:
            self.target_label.setText("That client is no longer on file.")
            return

        glist = service.grocery_list_for(customer, conn=db.connect())
        if glist is None:
            # Same rule as the rest of the page: no weight, no invented target.
            self.target_label.setText(
                f"{customer.name} has no weight on file, so there is no protein "
                "target to shop for."
            )
            return

        safe_name = "".join(
            c for c in customer.name if c.isalnum() or c in " -_"
        ).strip().replace(" ", "-").lower() or "client"
        path, selected = QFileDialog.getSaveFileName(
            self,
            "Save grocery list",
            f"{safe_name}-grocery-list.html",
            "Web page, clickable links (*.html);;Text file (*.txt);;CSV (*.csv)",
        )
        if not path:
            return

        fmt = _format_for(path, selected)
        written = service.write_grocery_list(glist, path, fmt)
        count = len(glist.items)
        self.target_label.setText(
            f"Wrote {count} item{'' if count == 1 else 's'} for {glist.days} days "
            f"to {written}"
        )
