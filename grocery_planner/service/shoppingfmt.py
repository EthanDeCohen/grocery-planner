# ######### decohen-partners ##########
# Protein Ledger
"""Rendering a grocery list to something a human can carry (GFP-112).

Three formats, each earning its place:

* **text** — the printable list, grouped by store, aligned. This is the one you
  print and take to a shop.
* **csv** — for a spreadsheet, or for whatever consumes it later.
* **html** — the one that makes the links actually CLICKABLE, which the user
  named a v1 imperative. Plain text can hold a bare URL that *many* terminals
  linkify and CSV holds a URL column Excel *sometimes* makes clickable, but
  "many" and "sometimes" are not a feature. HTML is clickable everywhere,
  prints properly from any browser, and opens identically on macOS and Windows
  with nothing installed.

**INI was dropped**, having been asked for and then rejected on sight ("the
equal signs are ugly"). Inspecting it agreed: ``configparser`` treats ``%`` as
interpolation so a real deal named "50% off" raises; duplicate keys raise and
the same item can legitimately appear twice; and product names contain ``=``
and commas, which INI does not quote.

Cross-platform specifics, since the user asked for macOS and Windows by name:

* CSV is written with ``newline=""``. Without it Windows inserts a blank line
  between every row.
* CSV is encoded **utf-8-sig**. Windows Excel misreads plain UTF-8 and mangles
  anything non-ASCII; the BOM is harmless to Numbers and to Excel on macOS.
  This project has already been bitten by a BOM in the other direction
  (GFP-93), so it is a known-sharp edge rather than a theoretical one.
* Real CSV quoting throughout — item names contain commas already
  ("... Value Pack, 1 lb"), so a hand-rolled join would corrupt the file.
"""
from __future__ import annotations

import csv
import html
import io
from pathlib import Path

from .. import weight_basis
from .shopping import GroceryList

#: Columns for the CSV. Explicit and ordered so the file is a stable contract
#: rather than whatever the dataclass happens to expose today.
CSV_COLUMNS = [
    "store", "item", "quantity", "unit", "estimated_cost", "shelf_price",
    "grams_protein", "sku", "sku_namespace", "url", "weight_basis",
]

#: Never "Buy now". Carried over from GFP-38/GFP-99: these links reach a
#: product or ad page, and this app drives no checkout.
LINK_TEXT = "View product"


def _money(value: float | None) -> str:
    return f"${value:.2f}" if value is not None else "-"


def _header_lines(glist: GroceryList) -> list[str]:
    """The facts a printed list must carry to be trustworthy later."""
    lines = [
        f"Grocery list — {glist.client_name}",
        f"{glist.days} days from {glist.generated_on} · "
        f"{glist.target_grams_per_day:.0f} g protein/day",
    ]
    if not glist.is_complete:
        # Stated up front, not buried: a list that cannot meet the target must
        # say so where it cannot be missed.
        lines.append(
            f"NOTE: {glist.shortfall_grams:.0f} g of the period's protein target "
            "is not covered by anything currently on offer."
        )
    return lines


def to_text(glist: GroceryList) -> str:
    """The printable list: grouped by store, columns aligned."""
    out = _header_lines(glist)
    out.append("")

    if not glist.items:
        out.append("Nothing to buy — no current offer can be priced per gram of protein.")
        return "\n".join(out) + "\n"

    for store_label, items in glist.by_store():
        out.append(store_label.upper())
        out.append("-" * len(store_label))
        width = max(len(i.quantity_label) for i in items)
        for item in items:
            cost = _money(item.estimated_cost)
            mark = weight_basis.marker(
                weight_basis.basis_for(item.sold_by, item.weight_basis))
            out.append(f"  {item.quantity_label:<{width}}  {item.item_name}{mark}")
            detail = f"  {'':<{width}}  {cost}"
            if item.product_identifier:
                detail += f"  ·  SKU {item.product_identifier}"
            out.append(detail)
            if item.source_url:
                # A bare URL on its own line: most terminals and editors make
                # it clickable, and it survives copy/paste into anything.
                out.append(f"  {'':<{width}}  {item.source_url}")
        out.append("")

    out.append(f"Estimated total: {_money(glist.total_cost)}")
    # A marker with no legend is an unexplained symbol, and this page leaves the
    # screen -- it gets printed and carried. Only the states actually present.
    for mark, note in weight_basis.footnotes_for(
        [weight_basis.basis_for(i.sold_by, i.weight_basis) for i in glist.items]
    ):
        out.append(f"{mark}  {note}")
    if glist.unpriced:
        out.append(
            f"{len(glist.unpriced)} item(s) have no quantity: their package "
            "weight was never published, so nothing was guessed."
        )
    return "\n".join(out) + "\n"


def to_csv(glist: GroceryList) -> str:
    """CSV text. Write it with :func:`write` so the encoding is right."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in glist.items:
        writer.writerow([
            item.store_label,
            item.item_name,
            "" if item.quantity is None else f"{item.quantity:g}",
            item.quantity_unit,
            "" if item.estimated_cost is None else f"{item.estimated_cost:.2f}",
            "" if item.shelf_price is None else f"{item.shelf_price:.2f}",
            f"{item.grams_protein:.0f}",
            item.product_identifier or "",
            item.product_identifier_ns or "",
            item.source_url or "",
            # The basis as DATA, not as a dagger: a glyph in a CSV cell is not
            # something a spreadsheet or an ordering system can act on.
            weight_basis.basis_for(item.sold_by, item.weight_basis) or "",
        ])
    return buffer.getvalue()


def to_html(glist: GroceryList) -> str:
    """The clickable, printable format.

    Self-contained: styles inline, no external asset, so it opens from a file
    on any machine with no network. Everything user-supplied goes through
    ``html.escape`` -- product names contain ``&`` routinely ("Bell & Evans"),
    and an unescaped one would corrupt the page.
    """
    esc = html.escape
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>Grocery list — {esc(glist.client_name)}</title>",
        "<style>",
        "body{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;",
        "margin:2rem auto;max-width:44rem;color:#1a1a1a}",
        "h1{font-size:1.4rem;margin:0 0 .25rem}",
        "h2{font-size:1.05rem;margin:1.6rem 0 .4rem;border-bottom:1px solid #ddd;"
        "padding-bottom:.2rem}",
        ".meta{color:#555;margin:0 0 .5rem}",
        ".note{background:#fff6e0;border-left:3px solid #e0a800;padding:.5rem .75rem;"
        "margin:.75rem 0}",
        "ul{list-style:none;padding:0;margin:0}",
        "li{padding:.45rem 0;border-bottom:1px solid #f0f0f0}",
        ".qty{display:inline-block;min-width:5.5rem;font-weight:600}",
        ".cost{color:#444}.sku{color:#777;font-size:.85em}",
        ".total{margin-top:1.5rem;font-size:1.1rem;font-weight:600}",
        "a{color:#0b5fff}",
        "@media print{a{color:#000}.note{border-color:#000}}",
        "</style></head><body>",
        f"<h1>Grocery list — {esc(glist.client_name)}</h1>",
        f'<p class="meta">{glist.days} days from {esc(glist.generated_on)} · '
        f"{glist.target_grams_per_day:.0f} g protein/day</p>",
    ]
    if not glist.is_complete:
        parts.append(
            f'<p class="note">{glist.shortfall_grams:.0f} g of the period\'s '
            "protein target is not covered by anything currently on offer.</p>"
        )

    if not glist.items:
        parts.append("<p>Nothing to buy — no current offer can be priced per "
                     "gram of protein.</p></body></html>")
        return "\n".join(parts)

    for store_label, items in glist.by_store():
        parts.append(f"<h2>{esc(store_label)}</h2><ul>")
        for item in items:
            mark = weight_basis.marker(
                weight_basis.basis_for(item.sold_by, item.weight_basis))
            row = [f'<span class="qty">{esc(item.quantity_label)}</span>',
                   esc(item.item_name) + esc(mark)]
            if item.source_url:
                row.append(
                    f' — <a href="{esc(item.source_url)}" '
                    f'rel="noopener noreferrer" target="_blank">{LINK_TEXT}</a>'
                )
            line = "".join(row)
            sub = [f'<span class="cost">{_money(item.estimated_cost)}</span>']
            if item.product_identifier:
                sub.append(f'<span class="sku">SKU {esc(item.product_identifier)}</span>')
            parts.append(f'<li>{line}<br>{" · ".join(sub)}</li>')
        parts.append("</ul>")

    parts.append(f'<p class="total">Estimated total: {_money(glist.total_cost)}</p>')
    # Same rule as the text renderer: only the markers actually on the page.
    for mark, note in weight_basis.footnotes_for(
        [weight_basis.basis_for(i.sold_by, i.weight_basis) for i in glist.items]
    ):
        parts.append(f'<p class="footnote">{esc(mark)}  {esc(note)}</p>')
    if glist.unpriced:
        parts.append(
            f'<p class="note">{len(glist.unpriced)} item(s) have no quantity: '
            "their package weight was never published, so nothing was guessed.</p>"
        )
    parts.append("</body></html>")
    return "\n".join(parts)


#: Renderer per format name, so callers never branch on a string themselves.
RENDERERS = {"text": to_text, "csv": to_csv, "html": to_html}
#: The file extension each format should be written with.
EXTENSIONS = {"text": ".txt", "csv": ".csv", "html": ".html"}


def render(glist: GroceryList, fmt: str) -> str:
    try:
        return RENDERERS[fmt](glist)
    except KeyError:
        raise ValueError(
            f"unknown format {fmt!r}; expected one of {', '.join(sorted(RENDERERS))}"
        ) from None


def write(glist: GroceryList, path: str | Path, fmt: str) -> Path:
    """Write ``glist`` to ``path``. Returns the path actually written.

    CSV gets ``utf-8-sig`` and no newline translation; the others get plain
    UTF-8 with the platform's own line endings, since they are read by humans
    and editors rather than by Excel. See the module docstring.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render(glist, fmt)
    if fmt == "csv":
        target.write_text(content, encoding="utf-8-sig", newline="")
    else:
        target.write_text(content, encoding="utf-8")
    return target
