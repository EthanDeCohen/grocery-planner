"""Link a store's PROMO feed to its CATALOGUE feed (GFP-248).

The gap this closes
-------------------
A store may have two sources (see ``scrapers/__init__.py``): a Flipp weekly ad
carrying promotional prices, and a catalogue feed carrying sizes and nutrition
the ad never has. ``deals`` holds both. Nothing joined them.

Measured on the live database, for the one store that already had both feeds:
406 distinct Flipp names, 977 distinct catalogue names, **zero exact overlap**.
The ad says ``"Gatorade"``; the catalogue says ``"Harris Teeter Boneless
Chicken Breast Value Pack, 1 lb"``.

That zero is expensive. ``savings.cost_per_gram_protein`` reads a weight-based
size out of the ITEM NAME, so a promotional name -- which carries no size -- can
never reach a $/g-protein figure. The catalogue row for the same product has the
size, at full price. The product was therefore ranking regular prices above sale
prices on the one metric it exists to compute.

How a link is established, and why it is this strict
----------------------------------------------------
Two names are linked only when **both independently match the same food**
through ``matching.match_item`` AND their names share meaningful words. Either
signal alone is not enough:

- Food agreement alone would link "Chicken Wings" to "Chicken Breast Value
  Pack" -- both are chicken, neither is the other.
- Word overlap alone would link "Gatorade" to "Gatorade Protein Shake", which
  are different products with different nutrition.

Requiring both, and taking the best-overlapping candidate, keeps this to joins a
human would agree with. Where nothing clears the bar there is **no link** --
savings.py's rule 1 applies here as everywhere: an uncertain figure is absent,
never guessed. A wrong size is worse than a missing one, because a wrong size
produces a confident $/g number that sends someone to a shop.

This is derived data. It is rebuilt after every ingest and holds nothing that
cannot be recomputed from ``deals`` and ``deal_food_match``.

Store-agnostic by construction (GFP-32): nothing here reads a store's identity
except as an opaque half of a key. It applies unchanged to ``harristeeter``
(Flipp + Kroger API) and ``foodlion`` (Flipp + PRISM catalogue).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import db, matching, savings

#: Words that carry no product identity. Dropped before overlap is measured so
#: "Fresh Boneless Chicken Breast" and "Chicken Breast, 1 lb" are recognised as
#: the same thing rather than penalised for packaging adjectives.
STOPWORDS = frozenset("""
a an and or the of with in for at by to from on plus new pack packs package
value size large small medium family fresh frozen natural premium original
brand select choice each ct count oz lb lbs pound pounds bag box btl bottle
jar can cans pkg container tray sliced whole half free reduced less lean
""".split())

#: Below this, the two names do not describe the same product with enough
#: confidence to lend a size. Tuned to accept "Boneless Chicken Breast" ->
#: "Harris Teeter Boneless Skinless Chicken Breasts Small Pack, 1 lb" while
#: rejecting pairs that merely share a protein.
MIN_OVERLAP = 0.6

#: A single shared word is a coincidence, not an identification. "Chicken
#: Kitchen" and "Fresh Chicken Hatch Chile" share exactly one, and linking them
#: would lend a chile dish's package weight to something else entirely.
MIN_SHARED_WORDS = 2

#: A word appearing in more than this share of one store's item names is that
#: store's own furniture -- its house brand, most obviously -- and identifies
#: nothing within it. Derived per store from the data rather than hardcoded,
#: because "Harris Teeter" is noise at Harris Teeter and a meaningful brand
#: name anywhere else, and because GFP-32 forbids naming a store in the engine.
MAX_DOCUMENT_FREQUENCY = 0.15

#: Document frequency is a statistic and needs a sample. Below this many names
#: every word looks common -- in a corpus of two, one appearance is 50% -- and
#: the discounting would throw away the very words that identify a product. A
#: store with a handful of listings is compared on its words as they are.
MIN_CORPUS_FOR_HOUSE_WORDS = 20

#: A borrowed size is real evidence -- the catalogue measured this product --
#: but it is one inference removed from the row being priced, so it never
#: claims the certainty of a size read directly off the name.
#: Pairs a single product cannot be both halves of. Word overlap is blind to
#: negation -- "Boneless Pork Loin" and "Bone-in Center Cut Pork Loin Chops"
#: share almost every word while describing opposite things, and a bone-in
#: package weight lent to a boneless price overstates the protein in it. Cheap
#: rule, and it catches the errors that a proportional score never will.
CONTRADICTIONS = (
    (("boneless", "bonles"), ("bonein", "bone")),
    (("cooked",), ("raw", "uncooked")),
)

LINK_CONFIDENCE = matching.CONFIDENCE_MEDIUM
LINK_METHOD = "cross_source_name"

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Link:
    """One promo row's borrowed identity."""

    item_name: str
    linked_item_name: str
    food_id: int | None
    confidence: float
    method: str


def _singular(word: str) -> str:
    """Fold a trailing plural ``s``, so "Breasts" and "Breast" are one word.

    Grocery names alternate between the two constantly -- a catalogue says
    "Chicken Breasts", an ad says "Chicken Breast" -- and treating them as
    different words was measurably losing real links.

    Words ending in ``ss`` are left alone: "boneless" and "skinless" are
    adjectives, not plurals, and stripping them would produce nonsense. Short
    words are left alone too, where a trailing ``s`` is more likely to be part
    of the word than a plural marker.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(name: str) -> set[str]:
    """Identity-bearing words of a product name, lowercased and de-pluralised.

    Pure digits go too: "1 lb" and "16 oz" are size, and size is precisely what
    the promo side does not have, so counting numbers would penalise exactly
    the pairs this exists to find.
    """
    return {
        _singular(w) for w in _WORD.findall(name.lower())
        if w not in STOPWORDS and not w.isdigit()
    }


def house_words(names: list[str], max_df: float = MAX_DOCUMENT_FREQUENCY) -> set[str]:
    """Words so common within ONE store's names that they identify nothing there.

    A store's house brand is the clearest case: "Harris Teeter" appears on a
    large share of Harris Teeter's own listings, so counting it as shared
    identity made "Harris Teeter Bacon Bits" look like "Harris Teeter Premium
    Turkey Bacon". Derived from the data per store rather than hardcoded --
    GFP-32 forbids the engine knowing any store's name, and a brand that is
    noise inside one chain is a real signal inside another.
    """
    if len(names) < MIN_CORPUS_FOR_HOUSE_WORDS:
        return set()
    counts: dict[str, int] = {}
    for name in names:
        for word in _words(name):
            counts[word] = counts.get(word, 0) + 1
    ceiling = max_df * len(names)
    return {w for w, n in counts.items() if n > ceiling}


def overlap(promo: str, catalogue: str, ignore: set[str] | None = None) -> float:
    """How much of the PROMO name is accounted for by the catalogue name.

    Deliberately asymmetric. A catalogue name is long and specific ("Harris
    Teeter Boneless Skinless Chicken Breasts Small Pack, 1 lb") while a
    promotional name is short ("Boneless Chicken Breast"). A symmetric measure
    such as Jaccard would punish the catalogue for being descriptive, which is
    the very property that makes it useful. The question worth asking is "is
    the promoted thing contained in this catalogue entry?", so the denominator
    is the promo side.

    ``ignore`` drops that store's house words (see :func:`house_words`) from
    both sides before measuring.
    """
    skip = ignore or set()
    p = _words(promo) - skip
    if not p:
        return 0.0
    return len(p & (_words(catalogue) - skip)) / len(p)


def contradicts(promo: str, catalogue: str) -> bool:
    """Do these two names assert opposite things about the same product?

    Checked on the raw text rather than the tokenised words, because the
    distinguishing forms ("bone-in", "bone in") tokenise apart and would be
    invisible to a set comparison.
    """
    a, b = promo.lower().replace("-", ""), catalogue.lower().replace("-", "")
    for left, right in CONTRADICTIONS:
        a_left = any(w in a for w in left)
        b_left = any(w in b for w in left)
        a_right = any(w in a for w in right) and not a_left
        b_right = any(w in b for w in right) and not b_left
        if (a_left and b_right) or (a_right and b_left):
            return True
    return False


def shared_words(promo: str, catalogue: str, ignore: set[str] | None = None) -> int:
    """How many identity-bearing words the two names actually share."""
    skip = ignore or set()
    return len((_words(promo) - skip) & (_words(catalogue) - skip))


def _has_weight_size(item_name: str) -> bool:
    """Does a size parse out of this name, in a unit that converts to grams?

    The same test ``savings.cost_per_gram_protein`` applies, asked here so the
    two sides of a link are exactly "the row that fails that test" and "the row
    that passes it".
    """
    size = savings.parse_size(item_name)
    return size is not None and size.base_unit == savings.WEIGHT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidates_for(
    promo: str,
    catalogue_names: list[str],
    min_overlap: float = MIN_OVERLAP,
    ignore: set[str] | None = None,
) -> str | None:
    """The best catalogue name for ``promo``, or ``None`` if none clears the bar.

    Two bars, not one. A high proportional overlap is easy to reach when the
    promo name is two words long, so a candidate must ALSO share at least
    :data:`MIN_SHARED_WORDS` of them -- one word in common is a coincidence,
    not an identification.

    Ties break on the SHORTEST candidate. Among catalogue entries that describe
    the promoted item equally well, the least embellished one is the most
    likely to be the plain product rather than a variant -- and a shorter name
    carries fewer unmatched words, so it is the more conservative borrow.
    """
    best: tuple[float, int, str] | None = None
    for name in catalogue_names:
        if contradicts(promo, name):
            continue
        if shared_words(promo, name, ignore) < MIN_SHARED_WORDS:
            continue
        score = overlap(promo, name, ignore)
        if score < min_overlap:
            continue
        key = (-score, len(name), name)
        if best is None or key < best:
            best = key
    return best[2] if best else None


def build_links(
    conn: sqlite3.Connection | None = None, min_overlap: float = MIN_OVERLAP,
) -> dict[str, Any]:
    """Rebuild ``deal_source_link`` for every store. Returns summary counts.

    Rebuilt rather than updated: the links are derived from ``deals``, which is
    replaced wholesale on every scrape, so a link surviving the row it was
    computed from would be a stale claim about a product that may no longer be
    on offer.
    """
    own = conn or db.connect()
    rows = own.execute(
        "SELECT DISTINCT d.store, d.item_name, m.food_id "
        "FROM deals d "
        "LEFT JOIN deal_food_match m "
        "  ON m.store = d.store AND m.item_name = d.item_name "
        "WHERE d.item_name IS NOT NULL AND d.item_name <> ''"
    ).fetchall()

    # Split each store's names by the only distinction that matters here:
    # whether a size can be read off the name at all.
    sized: dict[str, list[str]] = {}
    unsized: dict[str, list[tuple[str, int | None]]] = {}
    food_of: dict[tuple[str, str], int | None] = {}
    for row in rows:
        store, name, food_id = row["store"], row["item_name"], row["food_id"]
        food_of[(store, name)] = food_id
        if _has_weight_size(name):
            sized.setdefault(store, []).append(name)
        else:
            unsized.setdefault(store, []).append((name, food_id))

    linked: list[tuple[str, str, str, int | None, float, str, str]] = []
    considered = no_food = no_candidate = 0
    now = _now()

    for store, needy in unsized.items():
        pool = sized.get(store, [])
        if not pool:
            continue
        # This store's own furniture, measured across everything it lists --
        # both feeds, so a house brand that saturates the catalogue is
        # discounted when comparing against the ad too.
        house = house_words(pool + [n for n, _ in needy])
        # Index the sized pool by the food it matched, so a promo row is only
        # ever compared against catalogue entries for the SAME food. This is
        # what stops "Chicken Wings" borrowing a chicken breast's package.
        by_food: dict[int, list[str]] = {}
        for name in pool:
            fid = food_of.get((store, name))
            if fid is not None:
                by_food.setdefault(fid, []).append(name)

        for name, food_id in needy:
            considered += 1
            if food_id is None:
                # Unmatched on the promo side: there is no food to agree on,
                # so there is no evidence beyond words. Not enough.
                no_food += 1
                continue
            best = candidates_for(
                name, by_food.get(food_id, []), min_overlap, ignore=house)
            if best is None:
                no_candidate += 1
                continue
            linked.append(
                (store, name, best, food_id, LINK_CONFIDENCE, LINK_METHOD, now))

    own.execute("DELETE FROM deal_source_link")
    own.executemany(
        "INSERT INTO deal_source_link"
        "(store, item_name, linked_item_name, food_id, confidence, method, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        linked,
    )
    own.commit()

    return {
        "stores": len(unsized),
        "considered": considered,
        "linked": len(linked),
        "no_food_match": no_food,
        "no_candidate": no_candidate,
    }


def get_link(
    store: str | None, item_name: str | None, conn: sqlite3.Connection | None = None,
) -> Link | None:
    """The catalogue row lending its size to this promo row, if any."""
    if not store or not item_name:
        return None
    own = conn or db.connect()
    try:
        row = own.execute(
            "SELECT item_name, linked_item_name, food_id, confidence, method "
            "FROM deal_source_link WHERE store = ? AND item_name = ?",
            (store, item_name),
        ).fetchone()
    except sqlite3.OperationalError:
        # The table is created by migration 0021. A caller running against a
        # database that predates it must degrade to today's behaviour rather
        # than fail -- this feature is additive by design.
        return None
    if row is None:
        return None
    return Link(
        item_name=row["item_name"],
        linked_item_name=row["linked_item_name"],
        food_id=row["food_id"],
        confidence=row["confidence"],
        method=row["method"],
    )
