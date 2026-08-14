"""The "cheapest meat protein" strip along the bottom of the window (GFP-107).

Requested directly: alongside the trends chart, a **fixed** row per store saying
what the cheapest animal protein on offer is right now, with the kind in
parentheses.

**Why this is not the chart.** The chart answers "is protein getting cheaper over
time" and needs at least two days of history to draw anything. This answers "what
should this client buy today, and where" and needs none — one scrape is enough.
That makes it the only panel with something useful on the day the app is
installed, which matters because a fresh install otherwise shows an empty chart
and little else (GFP-104/GFP-105).

Fixed height by construction: one row per store, and a store count is what it
scales with. The numbers come from ``service.cheapest_protein_by_store``, so this
module only draws them — the CLI can print the identical thing.

**Denomination is shown, never assumed** (GFP-98). A ``soldBy=WEIGHT`` item's
price buys ONE POUND while a UNIT item's price buys the package; rendering both
as a bare "$1.49" invites a wrong buying decision from entirely correct data.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from .. import exclusions, service, weight_basis

#: Height is bounded by the store count, but a runaway registry should not be
#: able to eat the window. Beyond this the strip scrolls rather than growing.
MAX_VISIBLE_ROWS = 6


def _money(value: float) -> str:
    return f"${value:.2f}"


def _per_gram(value: float) -> str:
    """The precision the numbers actually differ by — cents would show ties."""
    return f"${value:.4f}"


#: GFP-38's rule, carried here: these links reach a product or ad page and this
#: app drives no checkout, so the label may never read "Buy now".
LINK_TEXT = "View product"


def describe(item: service.CheapestProtein) -> str:
    """One store's line, as rich text. Presentation only; no arithmetic here."""
    kind = f" <span>({item.kind})</span>" if item.kind else ""
    # GFP-98: say what the price buys. Without this a $1.49/lb cut looks cheaper
    # than a $4.99 packet when the shopper may well pay more for the cut.
    if item.sold_by == "WEIGHT" and item.price_per_unit_uom:
        price = f"{_money(item.price)} per {item.price_per_unit_uom}"
    else:
        price = _money(item.price)
    # GFP-118. A missing URL degrades to plain text rather than a dead control
    # -- the same rule gui/wheretobuy.py follows, and the reason a link here is
    # worth having at all: every scraped row now carries one, but the legacy
    # csv-import rows never will.
    # GFP-152/GFP-270: the by-weight marker, on the ITEM NAME rather than the
    # price, for the reason gui/wheretobuy.py gives -- it tells the shopper
    # something about the product they are about to pick up. A Publix rate row
    # gets ‡, because "$2.39" here means "per pound", and this strip is the
    # first thing a nutritionist reads.
    mark = weight_basis.marker(
        weight_basis.basis_for(item.sold_by, item.weight_basis)
    )
    name = f"{item.item_name}{mark}"
    if item.source_url:
        name = (
            f'{item.item_name}{mark} — '
            f'<a href="{html.escape(item.source_url, quote=True)}">{LINK_TEXT}</a>'
        )
    return (
        f"<b>{item.label}</b> — {_per_gram(item.cost_per_gram_protein)}/g protein"
        f"{kind}<br><span>{price} · {name}</span>"
    )


class CheapestMeatStrip(QWidget):
    """Fixed bottom strip: the best animal-protein buy at each store, today."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)

        self.title = QLabel("Cheapest meat protein on offer")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        layout.addWidget(self.title)

        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        # Without this the anchors render as blue text that does nothing when
        # clicked, which is worse than plain text (GFP-38).
        self.body.setOpenExternalLinks(True)
        self.body.setToolTip(
            "Opens the store's product page — this is a listing, not a checkout."
        )
        layout.addWidget(self.body)

        # Why a store the user expects is absent (GFP-300). Set once: the list
        # is a decision, not data, so it cannot change between refreshes. Shown
        # whatever the ranking says -- an empty week is exactly when someone
        # wonders where Costco went.
        self.excluded = QLabel("")
        self.excluded.setWordWrap(True)
        self.excluded.setToolTip(exclusions.why_the_markup_is_not_quantified())
        smaller = self.excluded.font()
        smaller.setPointSizeF(max(6.0, smaller.pointSizeF() - 1))
        self.excluded.setFont(smaller)
        self.excluded.setEnabled(False)   # muted: a footnote, not a finding
        self.excluded.setVisible(bool(exclusions.EXCLUDED))
        layout.addWidget(self.excluded)

        self.items: list[service.CheapestProtein] = []
        self.reload()

    # ----------------------------------------------------------------- #
    def reload(self) -> None:
        self.items = service.cheapest_protein_by_store()

        # Counted from what was just ranked, not queried separately -- two
        # derivations of "how many stores" would eventually disagree, and the
        # line would then be confidently wrong.
        self.excluded.setText(
            exclusions.summary(compared=len({item.store for item in self.items}))
        )

        if self.items:
            self.title.setVisible(True)
            self.body.setText(
                "<br>".join(describe(item) for item in self.items[:MAX_VISIBLE_ROWS])
            )
            return

        # Two different silences, told apart rather than merged -- the same rule
        # the trends pane follows. "Nothing scraped yet" is a thing to act on;
        # "this week's ad has no resolvable meat" is not the user's fault and no
        # amount of scraping fixes it today.
        self.title.setVisible(False)
        if service.has_price_history():
            self.body.setText(
                "No animal protein with a usable size in this week's offers, so "
                "there is nothing to rank yet."
            )
        else:
            self.body.setText(
                "No price data yet — use Data ▸ Run scrape… to see the cheapest "
                "meat protein at each store."
            )
