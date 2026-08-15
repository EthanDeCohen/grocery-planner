# ######### decohen-partners ##########
# Protein Ledger
"""Tie a deal's ad copy to a food we know the protein of (GFP-25).

In:  a deal row, e.g. "Boneless Pork Loin Roast or Whole Pork Tenderloin".
Out: a row in `deal_food_match` saying which food it is, how sure we are, and
     how we decided. Without this, cost-per-gram has nothing to divide by.

THREE HONESTY RULES, the same spirit as savings.py:

1. Only the headline product counts. Ads bundle alternatives under one price;
   we read the text before the first standalone "or".
2. A weak match is stored, but visibly weak. Every row carries a confidence
   and a method, so "we think" never looks like "we know". A nutritionist
   reviewing the low-confidence rows is the point of that field.
3. Something we cannot tie to anything gets NO row. Absence means unmatched,
   which is a gap someone can fix. A guess wearing a number is worse than
   nothing, because nothing is honest.

Rules, not fuzzy matching: 32 curated foods and well-known meat-cut words mean
hand-written branches are auditable -- every row's `method` points at the one
branch that made it. See `_EXCLUDE` for the real traps this was built against
("Nestle Drumstick Cones" is ice cream; a dog food listing "Chicken & Beef").

WHY MATCHES ARE KEYED ON (store, item_name) AND NOT deals.id. `deals.id` is an
autoincrement surrogate that does not survive a rescrape -- `run_scrape` deletes
every row for a store+source+ZIP and reinserts with fresh ids. A match keyed on
it would be orphaned by the next scrape. (store, item_name) is what actually
identifies the same product here. That, plus never overwriting a row marked
`match_source='manual'`, is what makes a nutritionist's correction permanent.

Every function takes an optional `conn` rather than holding one, because SQLite
connections cannot cross threads.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import db, protein_kind

CONFIDENCE_HIGH = 0.9
CONFIDENCE_MEDIUM = 0.6
CONFIDENCE_LOW = 0.3

AUTO = "auto"
MANUAL = "manual"

# --------------------------------------------------------------------------- #
# Headline extraction -- same rule as savings.parse_size (see module
# docstring): a promo naming two products under one price/row is only
# describing the first one.
# --------------------------------------------------------------------------- #
_OR_SPLIT = re.compile(r"\bor\b", re.IGNORECASE)


def headline(item_name: str | None) -> str:
    """The text before the first standalone "or", or "" if there is none."""
    if not item_name:
        return ""
    return _OR_SPLIT.split(item_name, maxsplit=1)[0].strip()


def _word(phrase: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def _has(text: str, *phrases: str) -> bool:
    return any(_word(p).search(text) for p in phrases)


# Non-food traps actually present in the real 1,573-deal database: pet food
# and treats, personal care, prepared dishes bulked out with mayo/broth,
# processed/composite meat products with no raw-cut equivalent in the
# catalog (franks, corn dogs, sausage, biscuits, sauce), a brand name that
# collides with a fish word ("Cape Cod" potato chips, not cod fish), and
# dessert novelties that happen to share a word with a meat cut ("Nestle
# Drumstick Cones" is ice cream; chicken/beef "Wet Dog Food" is not a human
# protein deal). Any of these anywhere in the headline rules the deal out
# before category detection even runs -- deliberately upstream of every
# category branch below, since none of them should be rescued by a
# low-confidence fallback either. Found by actually running the matcher over
# the real database and inspecting every match (see the PR description) --
# not guessed in advance.
# GFP-279: what remains here is deliberately ONLY the matcher's own business --
# brand collisions ("Cape Cod" chips, "Nestle Drumstick" cones) and product
# forms the curated catalog has no entry for (franks, corn dogs, sausage,
# biscuits, sauce, jerky), where the objection is "we have nothing to match this
# to", not "this is not meat".
#
# `broth` and `soup` USED to be here too, and that duplication is what caused
# the bug this ticket exists for. `protein_kind.DISQUALIFIERS` also listed them
# -- along with stock, bouillon, consommé and gravy, which this list never
# had. Two vocabularies for one idea, maintained by hand, drifted: "beef cooking
# stock" passed this filter because only `broth` was written down here, and got
# matched to beef sirloin. They are gone from here and the single vocabulary in
# `protein_kind` is consulted below instead.
_EXCLUDE = re.compile(
    r"\b(dog|cat|pet|litter|shampoo|conditioner|detergent|treat|treats|"
    r"jerky|cone|cones|dessert|salad|dip|cape cod|frank|franks|"
    r"hot dog|hotdog|corn dog|corn dogs|sausage|biscuit|sauce)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MatchResult:
    """A candidate match: which curated food, how confident, and how decided."""

    source_ref: str
    confidence: float
    method: str


# --------------------------------------------------------------------------- #
# Per-category matchers. Each takes the (already headline-only) text and
# returns a MatchResult, or None if that category's trigger word isn't
# present at all. match_item() tries them in a fixed priority order and
# takes the first hit -- a headline naming two species at once (rare, and
# arguably not a single clean product anyway) gets whichever category is
# checked first, which is a known, documented limitation rather than a
# silent one.
# --------------------------------------------------------------------------- #
_LEAN_PCT = re.compile(r"(\d{1,3})\s*%\s*lean", re.IGNORECASE)

# Beef cuts that identify the species even when the word "beef" itself is
# absent -- e.g. "85% Lean Fresh Ground Round" never says "beef", but
# "round" is a beef cut.
# GFP-280: ONE list of beef cuts, owned by protein_kind, imported here.
# This module's copy used to be the longer of two and the species decision used
# the shorter one, so routing species through protein_kind without unifying them
# would have made "T-Bone Steak" unclassifiable.
_BEEF_CUT_WORDS = protein_kind.BEEF_CUT_WORDS


def _match_beef(text: str) -> MatchResult | None:
    if not (_has(text, "beef") or _has(text, *_BEEF_CUT_WORDS)):
        return None

    lean = _LEAN_PCT.search(text)
    if lean:
        pct = int(lean.group(1))
        ref = "beef-ground-80-20" if abs(pct - 80) <= abs(pct - 93) else "beef-ground-93-7"
        exact = pct in (80, 93)
        confidence = CONFIDENCE_HIGH if exact else CONFIDENCE_MEDIUM
        method = "lean_pct_exact" if exact else "lean_pct_nearest"
        return MatchResult(ref, confidence, method)

    if _has(text, "sirloin"):
        return MatchResult("beef-sirloin-steak", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "ribeye", "rib eye"):
        return MatchResult("beef-ribeye-steak", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "chuck"):
        return MatchResult("beef-chuck-roast", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "brisket"):
        return MatchResult("beef-brisket", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "ground"):
        return MatchResult("beef-ground-80-20", CONFIDENCE_MEDIUM, "ground_no_lean_pct")
    if _has(text, "burger", "burgers", "patty", "patties"):
        return MatchResult("beef-ground-80-20", CONFIDENCE_MEDIUM, "burger_form")
    if _has(text, "stew"):
        return MatchResult("beef-chuck-roast", CONFIDENCE_MEDIUM, "stew_proxy")
    if _has(text, "roast"):
        return MatchResult("beef-chuck-roast", CONFIDENCE_MEDIUM, "roast_proxy")
    if _has(text, "steak", "steaks", "filet"):
        return MatchResult("beef-sirloin-steak", CONFIDENCE_MEDIUM, "steak_proxy")
    # "beef" (or a cut word) fired but nothing more specific -- still worth
    # surfacing, just visibly weak.
    return MatchResult("beef-sirloin-steak", CONFIDENCE_LOW, "category_fallback")


def _match_pork(text: str) -> MatchResult | None:
    # "bacon" and "baby back rib(s)" are near-universally pork in a US
    # grocery flyer even when the word "pork" itself is absent (e.g. "Food
    # Lion Sliced Bacon", "Double Slab Fully Cooked Baby Back Ribs").
    if not (_has(text, "pork") or _has(text, "bacon", "baby back rib", "baby back ribs")):
        return None

    if _has(text, "chop", "chops"):
        return MatchResult("pork-loin-chop", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "tenderloin", "tenderloins"):
        return MatchResult("pork-tenderloin", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "shoulder"):
        return MatchResult("pork-shoulder", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "ground"):
        return MatchResult("pork-ground", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "bacon"):
        return MatchResult("bacon", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "loin"):
        return MatchResult("pork-loin-chop", CONFIDENCE_MEDIUM, "loin_proxy")
    if _has(text, "rib", "ribs"):
        return MatchResult("pork-shoulder", CONFIDENCE_LOW, "ribs_proxy")
    return MatchResult("pork-loin-chop", CONFIDENCE_LOW, "category_fallback")


def _match_chicken(text: str) -> MatchResult | None:
    if not _has(text, "chicken"):
        return None

    if _has(text, "breast", "breasts", "cutlet", "cutlets", "tenderloin", "tenderloins"):
        return MatchResult("chicken-breast-skinless-boneless", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "thigh", "thighs"):
        return MatchResult("chicken-thigh-skinless-boneless", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "drumstick", "drumsticks"):
        return MatchResult("chicken-drumstick", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "wing", "wings"):
        return MatchResult("chicken-wing", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "ground"):
        return MatchResult("chicken-ground", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "burger", "burgers", "patty", "patties"):
        return MatchResult("chicken-ground", CONFIDENCE_MEDIUM, "burger_form")
    if _has(text, "whole", "rotisserie"):
        return MatchResult("chicken-whole", CONFIDENCE_MEDIUM, "whole_or_rotisserie")
    return MatchResult("chicken-whole", CONFIDENCE_LOW, "category_fallback")


def _prepared_discount(text: str, high_ref: str) -> MatchResult:
    """Cooking/coating drops confidence for ANY fish species: breading,
    stuffing or heavy sauce adds real non-protein mass (worse than "raw"
    off by a little), smoking/marinating is milder but still not the raw
    form the catalog's grams-per-100g figure describes."""
    if _has(text, "stuffed", "breaded", "battered", "sauced"):
        return MatchResult(high_ref, CONFIDENCE_LOW, "prepared_form")
    if _has(text, "smoked", "marinated"):
        return MatchResult(high_ref, CONFIDENCE_MEDIUM, "prepared_form")
    return MatchResult(high_ref, CONFIDENCE_HIGH, "cut_keyword")


def _match_fish(text: str) -> MatchResult | None:
    if _has(text, "salmon"):
        return _prepared_discount(text, "salmon-atlantic")

    if _has(text, "tuna"):
        if _has(text, "canned") and _has(text, "water"):
            return MatchResult("tuna-canned-water", CONFIDENCE_HIGH, "cut_keyword")
        if _has(text, "canned"):
            return MatchResult("tuna-canned-water", CONFIDENCE_MEDIUM, "canned_unknown_packing")
        return _prepared_discount(text, "tuna-yellowfin")

    if _has(text, "tilapia"):
        return _prepared_discount(text, "tilapia")
    if _has(text, "cod"):
        return _prepared_discount(text, "cod")
    if _has(text, "shrimp"):
        return _prepared_discount(text, "shrimp")
    return None


def _match_tofu(text: str) -> MatchResult | None:
    if not _has(text, "tofu"):
        return None
    if _has(text, "extra firm"):
        return MatchResult("tofu-extra-firm", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "firm"):
        return MatchResult("tofu-firm", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "silken", "soft"):
        return MatchResult("tofu-silken", CONFIDENCE_HIGH, "cut_keyword")
    if _has(text, "medium"):
        return MatchResult("tofu-medium", CONFIDENCE_HIGH, "cut_keyword")
    return MatchResult("tofu-firm", CONFIDENCE_MEDIUM, "category_fallback")


def _match_whey(text: str) -> MatchResult | None:
    if _has(text, "whey"):
        if _has(text, "isolate"):
            return MatchResult("whey-protein-isolate", CONFIDENCE_HIGH, "cut_keyword")
        if _has(text, "hydrolysate"):
            return MatchResult("whey-protein-hydrolysate", CONFIDENCE_HIGH, "cut_keyword")
        if _has(text, "concentrate"):
            return MatchResult("whey-protein-concentrate", CONFIDENCE_HIGH, "cut_keyword")
        if _has(text, "blend"):
            return MatchResult("whey-protein-blend", CONFIDENCE_HIGH, "cut_keyword")
        if _has(text, "shake", "shakes", "ready-to-drink", "ready to drink", "rtd"):
            return MatchResult("whey-protein-rtd-shake", CONFIDENCE_HIGH, "cut_keyword")
        return MatchResult("whey-protein-concentrate", CONFIDENCE_MEDIUM, "whey_no_form")

    # No "whey" at all: a protein shake/powder that doesn't say what its
    # protein source is might not be whey (casein, soy, pea, ...) -- worth
    # surfacing for cost-per-gram-of-protein, but only ever as a weak guess.
    if _has(text, "protein shake", "protein shakes", "protein powder", "protein drink", "protein drinks"):
        return MatchResult("whey-protein-rtd-shake", CONFIDENCE_LOW, "protein_uncertain_source")
    return None


# GFP-280: SPECIES IS DECIDED ONCE, BY protein_kind. These choose only the CUT,
# and are called only when the species is already settled -- so there is no
# longer a priority order between them, which is what removes the bug.
#
# The old fixed order (beef, pork, chicken, fish, ...) meant the first matcher
# whose trigger word appeared won outright. Measured 2026-08-12:
#   "Villari Prime Boneless Pork Ribeye" hit _match_beef on "ribeye" and never
#   reached _match_pork; "Chicken of the Sea ... Salmon" hit _match_chicken.
_CUT_MATCHER_FOR_KIND = {
    "beef": _match_beef,
    "pork": _match_pork,
    "chicken": _match_chicken,
    "fish": _match_fish,
    # `_match_fish` owns shrimp too -- the curated catalog has a `shrimp` food
    # (kind: shellfish), so shellfish is NOT one of the species below. Assuming
    # it was cost one test, which is exactly the guard GFP-285 asks for.
    "shellfish": _match_fish,
    # GFP-295 gave tofu and whey a kind of their own. They were previously
    # reached through _NON_ANIMAL_MATCHERS below, which only runs when
    # kind_from_name returns None -- so without these two lines they stopped
    # matching entirely the moment they stopped being "not an animal".
    #
    # Each matcher is narrower than its kind, and that is correct: `plant` also
    # covers beans and peanut butter, and there is no curated food for those,
    # so _match_tofu simply returns None for them. Same for yogurt under dairy.
    "plant": _match_tofu,
    "dairy": _match_whey,
}

#: Kinds protein_kind can name that the CURATED catalog has no food for. There
#: is no `turkey-breast` slug -- every turkey food is retailer-specific
#: (kroger-*, wholefoods-*), which a source_ref cannot point at.
#:
#: Declining is the honest answer and is the entire fix: "Butterball Turkey
#: Bacon" previously matched `bacon` at 0.9 confidence and was served as PORK.
#: Unmatched is strictly better than confidently wrong, especially for a client
#: whose restriction is religious or medical (GFP-135).
#: GFP-295 adds `egg` here for the same reason turkey and lamb are: the rules
#: recognise it and the catalog has nothing to point it at. See GFP-290 -- these
#: are catalog gaps, not matcher gaps, and every one is a declined match.
_KINDS_WITHOUT_A_CURATED_FOOD = frozenset({"turkey", "lamb", "egg"})

#: Matchers for things that are not an animal species at all, so
#: `kind_from_name` returns None for them. They must still get their turn --
#: dispatching on species alone would silently drop tofu and whey entirely.
_NON_ANIMAL_MATCHERS = (_match_tofu, _match_whey)


def match_item(item_name: str | None) -> MatchResult | None:
    """Best-effort match for one item name. Pure function, no DB access.

    Directly testable against real item names without a connection;
    :func:`match_deals` is the thin persistence layer built on top of it.
    """
    text = headline(item_name)
    if not text or _EXCLUDE.search(text):
        return None
    # GFP-279: ONE vocabulary for "this is not a cut of meat", and it lives in
    # protein_kind. Adding a word there now vetoes it here automatically, which
    # is the property this check exists to buy -- a guard that asserts a
    # relationship instead of restating a spelling (GFP-179).
    #
    # Measured before the fix: "Hanover Brown Sugar & Bacon Baked Beans" matched
    # 'Bacon, raw' at 0.9 confidence, and inherited BACON'S protein density.
    # Confidence was never the problem -- the matcher was very sure, and wrong
    # about what the thing was.
    #
    # `is_disqualified`, not `is_not_a_protein_buy`, and the difference is
    # deliberate: mapping "Beyond Burger" onto beef-sirloin-steak would give a
    # plant patty beef's density, so the matcher must refuse analogues too. The
    # bill uses the narrower predicate because there the same analogue is a
    # perfectly good thing to buy. Same vocabulary, different question.
    if protein_kind.is_disqualified(text):
        return None

    # Species first, from the one module that owns that question.
    kind = protein_kind.kind_from_name(text)
    if kind is not None:
        cut_matcher = _CUT_MATCHER_FOR_KIND.get(kind)
        if cut_matcher is None:
            return None           # named species, nothing curated to point at
        return cut_matcher(text)  # cut only; species is already settled

    # No animal species named. Tofu and whey answer a different question and
    # still get their turn.
    for matcher in _NON_ANIMAL_MATCHERS:
        result = matcher(text)
        if result is not None:
            return result
    return None


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _catalog_food_ids(conn: sqlite3.Connection) -> dict[str, int]:
    """``slug -> foods.id`` for the whole catalog, whatever the provenance.

    Keyed on ``slug``, not ``source_ref``, and deliberately NOT filtered by
    ``source`` (GFP-68). ``gplan nutrition sync`` supersedes a curated row in
    place -- flipping ``source`` to ``usda`` and replacing ``source_ref`` with
    the FDC id -- so keying on either of those made a food vanish from this
    vocabulary the moment its protein figure got a better source. Which is
    backwards: a sourced food is the one we most want to match.
    """
    return {
        row["slug"]: row["id"]
        for row in conn.execute(
            "SELECT id, slug FROM foods WHERE slug IS NOT NULL"
        )
    }


def match_deals(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Match every distinct ``(store, item_name)`` in ``deals`` and persist
    the result into ``deal_food_match``. Returns summary counts.

    Runs over DISTINCT ``(store, item_name)`` pairs rather than every
    ``deals`` row: that pair, not ``deals.id``, is a product's stable
    identity in this schema (see the module docstring). A row a
    nutritionist has already corrected (``match_source='manual'``) is never
    touched -- re-running this (e.g. after every rescrape) must not
    silently undo a human decision.
    """
    own = conn or db.connect()
    food_ids = _catalog_food_ids(own)
    manual_keys = {
        (row["store"], row["item_name"])
        for row in own.execute(
            "SELECT store, item_name FROM deal_food_match WHERE match_source=?", (MANUAL,)
        )
    }
    pairs = own.execute(
        "SELECT DISTINCT store, item_name FROM deals "
        "WHERE item_name IS NOT NULL AND item_name <> ''"
    ).fetchall()

    now = _now()
    matched = unmatched = skipped_manual = missing_food = 0
    by_confidence = {"high": 0, "medium": 0, "low": 0}
    upsert_rows: list[tuple[str, str, int, float, str, str]] = []
    retract_keys: list[tuple[str, str]] = []

    for row in pairs:
        store, item_name = row["store"], row["item_name"]
        if (store, item_name) in manual_keys:
            skipped_manual += 1
            continue
        result = match_item(item_name)
        if result is None:
            unmatched += 1
            # GFP-279: an item the rules now REJECT must lose the match it was
            # given when they were laxer. Without this the veto only governs
            # items seen for the first time, so tightening a rule fixes the
            # future and leaves the past exactly as wrong as it was -- measured
            # here: re-running after the fix changed the match count by 0 and
            # left beans still pointing at 'Bacon, raw' with bacon's density.
            retract_keys.append((store, item_name))
            continue
        food_id = food_ids.get(result.source_ref)
        if food_id is None:
            # A rule points at a source_ref the catalog doesn't (or no
            # longer) has -- treat as unmatched rather than raising, since a
            # bulk match run over 1,500+ rows should not abort on one bad
            # reference.
            missing_food += 1
            unmatched += 1
            continue
        matched += 1
        if result.confidence >= CONFIDENCE_HIGH:
            by_confidence["high"] += 1
        elif result.confidence >= CONFIDENCE_MEDIUM:
            by_confidence["medium"] += 1
        else:
            by_confidence["low"] += 1
        upsert_rows.append((store, item_name, food_id, result.confidence, result.method, now))

    # Retract before upserting, so `retracted` counts only removals and is not
    # muddled by rows the upsert then re-adds. `match_source <> MANUAL` is the
    # load-bearing clause: a nutritionist's correction is never undone, and
    # neither is a retailer-supplied match. "BUSH'S Original Baked Beans"
    # arrives via kroger_api_direct pointing at a Plant Protein food -- beans
    # matched to beans, entirely correct, and it must survive a rule that
    # rejects the NAME.
    retracted = 0
    if retract_keys:
        before = own.execute("SELECT COUNT(*) FROM deal_food_match").fetchone()[0]
        own.executemany(
            "DELETE FROM deal_food_match "
            f"WHERE store=? AND item_name=? AND match_source <> '{MANUAL}'",
            retract_keys,
        )
        retracted = before - own.execute(
            "SELECT COUNT(*) FROM deal_food_match"
        ).fetchone()[0]

    own.executemany(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        f"VALUES (?, ?, ?, ?, ?, '{AUTO}', ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=excluded.confidence, "
        f"method=excluded.method, match_source='{AUTO}', updated_at=excluded.updated_at "
        f"WHERE deal_food_match.match_source <> '{MANUAL}'",
        upsert_rows,
    )
    own.commit()

    return {
        "distinct_items": len(pairs),
        "matched": matched,
        "unmatched": unmatched,
        "retracted": retracted,
        "skipped_manual": skipped_manual,
        "missing_food_reference": missing_food,
        "by_confidence": by_confidence,
    }


def correct_match(
    store: str, item_name: str, food_id: int, conn: sqlite3.Connection | None = None
) -> None:
    """Nutritionist correction: pin ``(store, item_name)`` to ``food_id`` for good.

    See the module docstring for why the key is ``(store, item_name)`` and
    not a ``deals.id`` -- this is what makes the correction survive both a
    rescrape and a future re-run of :func:`match_deals`.
    """
    own = conn or db.connect()
    now = _now()
    own.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        f"VALUES (?, ?, ?, 1.0, 'manual_correction', '{MANUAL}', ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=excluded.food_id, confidence=1.0, method='manual_correction', "
        f"match_source='{MANUAL}', updated_at=excluded.updated_at",
        (store, item_name, food_id, now),
    )
    own.commit()


def reject_match(store: str, item_name: str, conn: sqlite3.Connection | None = None) -> None:
    """Nutritionist correction: pin ``(store, item_name)`` to "no match" for good.

    Distinguishes "a human looked at this and it is not a protein deal"
    from an absent row, which just means nobody -- human or matcher -- has
    looked at it yet (see the module docstring's honesty rule 3).
    """
    own = conn or db.connect()
    now = _now()
    own.execute(
        "INSERT INTO deal_food_match"
        "(store, item_name, food_id, confidence, method, match_source, updated_at) "
        f"VALUES (?, ?, NULL, NULL, 'manual_reject', '{MANUAL}', ?) "
        "ON CONFLICT(store, item_name) DO UPDATE SET "
        "food_id=NULL, confidence=NULL, method='manual_reject', "
        f"match_source='{MANUAL}', updated_at=excluded.updated_at",
        (store, item_name, now),
    )
    own.commit()


def get_match(
    store: str, item_name: str, conn: sqlite3.Connection | None = None
) -> sqlite3.Row | None:
    """The current ``deal_food_match`` row for ``(store, item_name)``, if any."""
    own = conn or db.connect()
    return own.execute(
        "SELECT * FROM deal_food_match WHERE store=? AND item_name=?", (store, item_name)
    ).fetchone()
