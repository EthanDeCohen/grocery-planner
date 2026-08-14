"""Stores deliberately NOT listed, and why (GFP-300).

A store missing from the rankings is indistinguishable from a store that has no
good offer this week -- unless we say so. That is the no-silent-caps rule
(GFP-257's ``ScrapePlan.excluded``) applied to sources we chose to leave out
rather than ones a ZIP happens not to reach.

The distinction matters to the user in front of a client. "Costco isn't here"
invites the question "did you check?", and the honest answer is that we checked,
found the reachable price is not the real price, and left it out on purpose.

DATA, NOT UI TEXT. The reason lives here so the GUI, the CLI and any future
front end say the same thing, and so adding the next exclusion is one entry
rather than a hunt through widgets.

WHAT BELONGS HERE. Only stores excluded by a DECISION -- a price we cannot
trust, a membership we cannot assume. A store that is merely unscraped, or that
does not serve the user's ZIP, is a different fact and already has its own
vocabulary (``scraper_status``, ``ScrapePlan.excluded``). Do not use this to
paper over a broken scraper.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExcludedStore:
    """A store left out on purpose, in terms a user can act on."""

    #: Display name, as the user would say it.
    name: str
    #: One line. Why the number we could get is not the number that matters,
    #: and what to do instead. Kept short deliberately -- this sits under a
    #: ranking, not in a help page, and a paragraph there is noise.
    reason: str


#: Costco: measured 2026-08-14 (GFP-298). `sameday.costco.com` is an Instacart
#: white-label the existing client could almost certainly drive, and its own
#: pricing panel says "Item prices are marked up higher than your local Costco
#: warehouse." Costco's whole value to this project is the warehouse unit price,
#: so the only reachable figure is definitionally the wrong one. Same-Day also
#: requires a membership, which the app cannot assume a client holds.
EXCLUDED: tuple[ExcludedStore, ...] = (
    ExcludedStore(
        name="Costco",
        reason=(
            "online prices are marked up over warehouse - check in store for "
            "the real low"
        ),
    ),
)


def summary(compared: int | None = None) -> str:
    """One line: how many stores were compared, and who was left out and why.

    ``compared`` is the number of stores the ranking above actually drew on.
    It is passed in rather than queried here so this module stays free of the
    database -- and because the caller already knows the answer it just
    rendered, so re-deriving it risks the two disagreeing.

    EVERY NUMBER HERE IS COUNTED, NEVER ESTIMATED. It is tempting to state the
    size of Costco's markup (published third-party checks put it at 10-24%),
    but that figure is not ours, varies by item, location and Instacart+
    status, and would be quoted in the app's own voice as though measured. The
    app says what it counted. See :func:`why_the_markup_is_not_quantified`.

    Empty when there is nothing to say -- a line reading "nothing excluded" is
    a line the eye learns to skip, and this one has to be read on the day it
    finally matters.
    """
    parts: list[str] = []
    if compared:
        parts.append(f"Compared {compared} store{'s' if compared != 1 else ''}.")
    # ASCII only: this line is meant to be printable by the CLI too, and an em
    # dash does not survive the default Windows console code page (GFP-289).
    parts += [f"Not listed: {s.name} - {s.reason}." for s in EXCLUDED]
    return "  ".join(parts)


def why_the_markup_is_not_quantified() -> str:
    """The long answer, for a tooltip -- and a note to whoever tries next.

    Measuring a markup needs two independent prices for the SAME item at the
    same store. The database has 7 stores carrying 2-3 feeds each, so the
    prices are there. What is missing is a way to tell that two rows describe
    one product: every source uses its own private id namespace
    (``flipp.item_id``, ``kroger.product_id``, ``publix.item_id``,
    ``wholefoods.asin``, ...) and **not one carries a UPC or GTIN**. Measured
    2026-08-14: zero (store, item_name) pairs appear under more than one
    source.

    So this is blocked on a shared identifier, not on access. See GFP-299.
    """
    return (
        "We do not publish a markup percentage because we cannot measure one: "
        "no two of our sources share a product identifier, so the same item "
        "cannot be priced twice and compared. Published third-party estimates "
        "range from 10% to 24% depending on item, location and membership."
    )
