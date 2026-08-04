"""Where-to-buy column (GFP-38): the right column of the client detail page.

For each line of the daily bill, which store it came from and — where GFP-15
captured one — a corroborating link back to the real weekly ad.

**"View ad", never "Buy now".** Flipp is a flyer aggregator: a captured link
resolves to a weekly ad page or a product listing, not a checkout, and there
is no cart anywhere in this product. Labelling it as a purchase would promise
something the link cannot do. See ``scrapers/base.py``, which says the same
thing at the point the URL is built.

**A missing link degrades to plain text, never a dead control.** This is the
ticket's own acceptance criterion, and it is currently the *only* path that
runs: ``source_url`` is populated solely by the Flipp scraper, while the
Kroger API and Whole Foods scrapers write ``None`` outright, so every row in
the database today has no link at all. The column is still worth having --
the store tag alone answers "where do I buy this?" -- and it lights up on its
own the day a link-bearing scrape lands. A dead "View ad" button that does
nothing would be worse than the plain text it replaces.

**The ad clipping is not fetched.** ``image_url`` is carried through to here
(see :class:`bill.BillLine`) but deliberately not downloaded: this app is
local-first and does no network I/O to paint a window. Rendering the clipping
belongs with a deliberate decision about caching and offline behaviour, not
with a column that happens to have a URL in hand.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import bill, weight_basis
from ..stores import BY_KEY

#: The only acceptable link text. Named so a future edit has to argue with it.
LINK_TEXT = "View ad"


def _store_name(store_key: str) -> str:
    store = BY_KEY.get(store_key)
    return store.display_name if store else store_key


#: GFP-98's ``soldBy`` value meaning "this price buys one unit of WEIGHT".
SOLD_BY_WEIGHT = "WEIGHT"


def _denomination_note(line: bill.BillLine) -> str:
    """" · sold by weight (per lb)" for a per-weight item, else nothing.

    GFP-50 inherited GFP-98's rule that a per-weight price must be visibly
    tagged. The bill PANEL needs no such tag — every figure it shows is an
    amortised $/day derived from $/g protein, which is denomination-neutral by
    construction, so the "$2.49 loin looks cheaper than a $4.99 packet" trap
    cannot arise there. This row is different: it is the buying instruction, and
    a shopper heading to the counter needs to know before they get there that
    they are paying by the pound rather than for a package.

    Silent when the source does not state a denomination (every Flipp and CSV
    row), because "sold by weight" and "we were not told" are different facts.
    """
    if line.sold_by != SOLD_BY_WEIGHT:
        return ""
    unit = f" (per {line.price_per_unit_uom})" if line.price_per_unit_uom else ""
    return f"  ·  sold by weight{unit}"


def _marker(line: bill.BillLine) -> str:
    """The GFP-152 by-weight marker for this line, or "".

    Placed on the ITEM NAME rather than beside the price: a shopper reading
    "Boar's Head Deluxe Ham*" is being told something about the product they
    are about to ask for, which is what they act on at the counter.
    """
    return weight_basis.marker(
        weight_basis.basis_for(line.sold_by, line.weight_basis)
    )


class WhereToBuyPane(QWidget):
    """One row per bill line: the store, and a link to the ad when there is one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.title = QLabel("Where to buy")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)

        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        # A scroll area, because the bill's length is data-dependent and a
        # long one must not push the column's own layout out of shape.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.rows_container)
        layout.addWidget(self.scroll, 1)

        # GFP-152. BELOW the list rather than above it: it explains marks the
        # reader has already met, and a legend for symbols nobody has seen yet
        # is something to scroll past.
        self.legend = QLabel("")
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet("color: #666; font-size: 11px;")
        self.legend.setVisible(False)
        layout.addWidget(self.legend)

        self.clear()

    # ----------------------------------------------------------------- #
    def _reset_rows(self) -> None:
        """Remove every row, immediately.

        ``takeAt`` only detaches a widget from the layout and ``deleteLater``
        only schedules destruction, so on their own a re-render leaves the
        previous rows still parented to the container and still painted --
        which showed each item twice. ``setParent(None)`` is what actually
        takes them off screen now, which matters because this pane re-renders
        on every preference toggle.
        """
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def clear(self) -> None:
        self._reset_rows()
        self.subtitle.setText("No client selected.")
        self.linked_count = 0
        self.legend.setVisible(False)

    def set_lines(self, lines: list[bill.BillLine]) -> None:
        """Render one row per contributing line of the bill."""
        self._reset_rows()
        if not lines:
            self.subtitle.setText("Nothing in the bill yet, so nowhere to buy.")
            self.linked_count = 0
            self.legend.setVisible(False)
            return

        self.linked_count = sum(1 for line in lines if line.source_url)
        stores = sorted({_store_name(line.store) for line in lines})
        self.subtitle.setText(
            f"{len(lines)} item{'s' if len(lines) != 1 else ''} across "
            f"{', '.join(stores)}."
        )

        for line in lines:
            self.rows_layout.addWidget(self._row(line))

        self._set_legend(lines)

    def _set_legend(self, lines: list[bill.BillLine]) -> None:
        """Explain only the markers actually on screen (GFP-152).

        A legend describing three markers when one is showing is noise, and
        noise is how a caveat stops being read.
        """
        present = [
            weight_basis.basis_for(line.sold_by, line.weight_basis)
            for line in lines
        ]
        entries = weight_basis.footnotes_for(present)
        self.legend.setText(
            "\n".join(f"{mark}  {note}" for mark, note in entries)
        )
        self.legend.setVisible(bool(entries))

    def _row(self, line: bill.BillLine) -> QWidget:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 8)

        name = QLabel(
            line.item_name + _marker(line) + _denomination_note(line)
        )
        name.setWordWrap(True)
        row_layout.addWidget(name)

        if line.source_url:
            # Rich text so Qt renders a real, openable hyperlink; the label
            # text is fixed at LINK_TEXT so no caller can relabel it "Buy now".
            link = QLabel(
                f'{_store_name(line.store)} — '
                f'<a href="{line.source_url}">{LINK_TEXT}</a>'
            )
            link.setTextFormat(Qt.RichText)
            link.setOpenExternalLinks(True)
            link.setToolTip(
                "Opens the store's weekly ad or product page — this is a flyer "
                "listing, not a checkout."
            )
        else:
            # Plain text, not a disabled button: there is nothing to click, and
            # a greyed-out control would imply there might be later.
            link = QLabel(f"{_store_name(line.store)} — no ad link captured")
            link.setTextFormat(Qt.PlainText)
        link.setWordWrap(True)
        row_layout.addWidget(link)
        return row
