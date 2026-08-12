"""Is a by-weight price a deli counter price or a pre-packaged one? (GFP-152)

A ``soldBy=WEIGHT`` figure is not a price anybody pays. It depends either on
how much the counter cuts for you or on what the shrink-wrapped package happens
to weigh, and the two are different promises to a shopper. GFP-98 established
the pricing mechanism; this establishes the retail format, which ``soldBy``
cannot express because it covers both.

WHAT THE SPIKE FOUND (GFP-151), and why the rule is shaped like this
--------------------------------------------------------------------
Two full product payloads were dumped and diffed leaf-by-leaf, then the
candidate signal was measured over 223 real ``soldBy=WEIGHT`` products.

**The ``categories`` array is the only signal in the payload.** There is no
fulfillment flag, no prep flag, no counter indicator, no department code.
(``preparationState`` exists but lives inside ``nutritionInformation`` and
describes the nutrition panel, not the retail format.)

* 49 of 223 carry ``Deli`` -- every one genuinely counter-cut, zero false
  positives in the sample.
* 174 do not -- value packs, fresh chicken breast, whole loins.
* **3 of those 174 are real deli items misfiled under Meat & Seafood**, a 1.7%
  false-negative rate. So "not Deli" cannot be read as "confirmed packaged"
  without a second look.

**The item name is a bad primary signal and is deliberately not used as one.**
Measured the way GFP-106 measured species coverage: only 7% of by-weight names
carry a deli-ish word at all, 47% of those would MISCLASSIFY (raw "Thinly
Sliced Chicken Breast" is packaged cutlets), and it misses 84% of real deli
items -- "Boar's Head Ovengold Roasted Turkey Breast" says nothing about a
counter.

The name earns exactly one job: **detecting disagreement**. The unambiguous
retailer phrases are what caught those 3 misfiled items. Where the category
says packaged and the name says deli, we do not know -- and saying so is the
whole point of the third state.

THE RULE THAT MATTERS MOST
---------------------------
**An item we cannot classify gets UNKNOWN, never a guessed answer.** Same rule
as ``protein_kind`` (GFP-106) and ``savings.py``'s rule 1: absent stays absent.
A confident "pre-packaged" on something that turns out to be a deli counter is
worse than admitting the uncertainty, because the shopper plans around it.
"""
from __future__ import annotations

import re

#: ``deals.sold_by`` value that makes any of this relevant. Anything else is a
#: fixed price for a fixed package and gets no marker at all -- marking
#: everything would make the marker meaningless.
SOLD_BY_WEIGHT = "WEIGHT"

#: Confirmed cut-to-order at a counter.
DELI = "deli"
#: Confirmed a pre-packaged random-weight package.
PREPACKAGED = "prepackaged"
#: By weight, but which of the two is not established.
UNKNOWN = "unknown"
#: The shelf figure IS a rate -- "$4.99/lb" -- not the price of anything.
#:
#: GFP-270. Distinct from the three above, and the distinction is not
#: cosmetic. Those all describe a PACKAGE whose weight varies while the figure
#: shown is still a price you could put in a basket: a $7.41 tray is $7.41.
#: Walmart and Publix quote a **rate** instead -- Publix boneless chicken
#: breast is "$4.99/lb" and Walmart's is "$2.23/lb" -- and a rate cannot be
#: summed, compared against a budget, or paid. It is one multiplication short
#: of being a price, and the missing factor (what you actually pick up) is not
#: in the data.
#:
#: This is the GFP-98 trap in its purest form: multiplying a per-pound figure
#: by servings-per-package understated whole pork loin ~7x. A row that says
#: "$4.99" where every neighbour means "for this package" is the single
#: easiest way to make the cheapest-looking item the most wrong one.
RATE = "rate"

#: The category that identifies the deli counter. Measured at 100% precision
#: over 49 products; it is the false NEGATIVES that need the name check below.
DELI_CATEGORY = "deli"

#: The category pre-packaged fresh meat is filed under. Required rather than
#: assumed: an item in neither this nor Deli (a 7-layer bean dip appeared in
#: the sample) is something this rule was not designed for, and gets UNKNOWN.
MEAT_CATEGORY = "meat & seafood"

#: Phrases the retailer itself uses for counter-cut product. Deliberately NOT a
#: general "sliced"/"shaved" word list -- those are what produce the 47%
#: misclassification rate, because packaged cutlets are sliced too. These three
#: are what caught all 3 misfiled items in the sample.
DELI_PHRASE = re.compile(r"fresh sliced deli|deli meat|deli cheese", re.I)


def classify(
    sold_by: str | None,
    categories: object = None,
    item_name: str | None = None,
) -> str | None:
    """Which of the three states this item is in, or ``None`` for no marker.

    ``None`` means the question does not apply: a fixed-price fixed-package
    item, or a source that never told us the denomination at all. That is
    distinct from :data:`UNKNOWN`, which means "priced by weight and we could
    not tell you which kind" -- a fact worth showing.

    ``categories`` accepts a list or a JSON/comma string, because the value
    survives a round trip through SQLite as text.
    """
    if (sold_by or "").strip().upper() != SOLD_BY_WEIGHT:
        return None

    names = _category_names(categories)
    name = item_name or ""

    if DELI_CATEGORY in names:
        return DELI

    # The category says packaged. Believe it only if the NAME does not
    # unambiguously contradict it -- this is the 1.7% the spike measured, and
    # the one job the name is trusted with.
    if DELI_PHRASE.search(name):
        return UNKNOWN

    if MEAT_CATEGORY in names:
        return PREPACKAGED

    # Priced by weight, but categorised as something this rule has never been
    # measured against, or not categorised at all. Not a guess.
    return UNKNOWN


def _category_names(categories: object) -> set[str]:
    """Category names, lowercased, from whatever shape they arrive in."""
    if categories is None:
        return set()
    if isinstance(categories, str):
        text = categories.strip()
        if not text:
            return set()
        if text.startswith("["):
            import json

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return {str(c).strip().lower() for c in parsed if str(c).strip()}
        return {part.strip().lower() for part in text.split(",") if part.strip()}
    if isinstance(categories, (list, tuple, set)):
        return {str(c).strip().lower() for c in categories if str(c).strip()}
    return set()


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
#: The marker shown beside a figure. A dagger rather than three asterisks for
#: the unknown case so it does not read as "even more of the same thing" -- it
#: is a different statement, not a stronger one.
#: One glyph per state, and **no glyph a prefix of another** -- see
#: :func:`_assert_unambiguous`. That invariant is why ``**`` is gone: it made
#: an item ending "Chicken**" impossible to read unambiguously, since it is
#: equally "prepackaged" or "deli marked twice", and in any Markdown-rendered
#: surface it silently turns the name bold instead of marking it.
#:
#: The sequence is the classic typographic footnote order (* † ‡ §), so the
#: four read as one family rather than two competing ones. ‡ stays on RATE
#: because that is the state a shopper most needs to notice: † says "this
#: price may move a little", ‡ says "this is not a price at all yet".
MARKERS: dict[str, str] = {
    DELI: "*",
    PREPACKAGED: "§",
    UNKNOWN: "†",
    RATE: "‡",
}

#: What each marker means, in a shopper's terms rather than the schema's.
FOOTNOTES: dict[str, str] = {
    DELI: "Deli item — price depends on weight cut for you.",
    PREPACKAGED: (
        "Pre-packaged by weight — may be slightly over or under depending on "
        "store packaging."
    ),
    UNKNOWN: (
        "Estimate only — this may be deli or pre-packaged; checkout price may "
        "vary."
    ),
    RATE: (
        "Price is per pound, not per package — what you pay depends on the "
        "weight you pick up."
    ),
}


def _assert_unambiguous() -> None:
    """No marker may be a prefix of another. Checked at import, not in a test.

    A test would catch it in CI; this catches it in the editor, and the cost of
    getting it wrong is silent rather than loud -- a legend that cannot be read
    back to a single state does not raise anything, it just misinforms someone
    holding a shopping list.
    """
    glyphs = list(MARKERS.values())
    if len(set(glyphs)) != len(glyphs):
        raise AssertionError(f"duplicate by-weight markers: {glyphs}")
    for one in glyphs:
        for other in glyphs:
            if one != other and other.startswith(one):
                raise AssertionError(
                    f"marker {one!r} is a prefix of {other!r}; an item ending "
                    f"{other!r} could be read either way"
                )


_assert_unambiguous()


def basis_for(sold_by: str | None, stored: str | None) -> str | None:
    """The state to DISPLAY, given what was stored against the deal.

    Stored classifications come from :func:`classify` at scrape time, where the
    ``categories`` evidence exists. This covers the two cases where none was:

    * **Rows scraped before GFP-152.** They carry ``sold_by='WEIGHT'`` and a
      NULL basis. Showing no marker would be wrong -- the price genuinely does
      depend on weight -- so they fall to UNKNOWN, which is exactly what the
      dagger says. The feature therefore works on existing data without
      waiting for a re-scrape.
    * **Sources that never state a denomination** (every Flipp and csv row):
      ``sold_by`` is NULL, the question does not arise, and there is no
      marker at all.
    """
    if stored in MARKERS:
        return stored
    if (sold_by or "").strip().upper() == SOLD_BY_WEIGHT:
        return UNKNOWN
    return None


def marker(basis: str | None) -> str:
    """The marker for ``basis``, or ``""`` when no marker applies."""
    return MARKERS.get(basis or "", "")


def footnote(basis: str | None) -> str:
    return FOOTNOTES.get(basis or "", "")


def footnotes_for(bases) -> list[tuple[str, str]]:
    """(marker, footnote) for the states actually present, in a stable order.

    Only the ones present: a legend explaining three markers when one is on
    screen is noise, and noise is how a caveat stops being read.
    """
    present = {b for b in bases if b in MARKERS}
    return [
        (MARKERS[state], FOOTNOTES[state])
        for state in (DELI, PREPACKAGED, UNKNOWN, RATE)
        if state in present
    ]
