# ######### decohen-partners ##########
# Protein Ledger
"""Which animal a protein food is (GFP-106) — chicken, beef, pork, fish…

The product's headline question is cost per gram of protein, and a nutritionist
choosing what a client should buy this week wants that broken down by **what the
food actually is**. ``foods.category`` cannot answer it: see the long note in
``db_script/migration/0015_GFP-106.ddl`` for why (two vocabularies, and the rows
deals match to only ever say ``'Meat'``).

So this module derives the kind and stores it in ``foods.protein_kind``, in four
states — a specific kind, :data:`OTHER` (not meat), :data:`UNKNOWN` (meat but we
cannot tell which), and ``NULL`` meaning *not yet classified*. Keeping the last
two apart is what lets :func:`classify_all` be cheap on every run after the
first: it only ever looks at ``NULL`` rows.

**Seafood counts as meat here**, by explicit product decision — fish and
shellfish compete with chicken and pork for "cheapest protein on offer".

Why not a substring match
-------------------------
Because every one of these is a real product name that a naive ``'beef' in
name`` gets wrong, and each would quietly corrupt the panel this feeds:

======================================  ===========================================
name                                    naive answer / correct answer
======================================  ===========================================
``Beefsteak Tomato``                    beef / not meat at all
``Chicken of the Sea Tuna``             chicken / fish
``Turkey Bacon``                        pork (via "bacon") / turkey
``Beef Flavored Ramen Noodles``         beef / not a cut of meat
``Chicken Broth``                       chicken / not a protein buy
======================================  ===========================================

The defence is ordering plus disqualifiers, not cleverness: product **forms**
that are never a cut of meat (broth, seasoning, chips, pet food) are rejected
before any kind rule runs, and the kind rules themselves are ordered
most-specific-first so ``turkey`` claims "turkey bacon" before ``pork`` can.

This follows the precedent ``savings.parse_size`` set in GFP-69: a rule that
cannot be trusted returns nothing rather than something. An item we cannot
classify is :data:`UNKNOWN`, never a guess — a mislabelled cut is worse than an
unlabelled one, because the label is what a nutritionist would act on.

Store-agnostic (GFP-32): nothing here may branch on which store a food came
from. A name is a name.
"""
from __future__ import annotations

import re
import sqlite3

from . import db

#: Classified, and it is not meat — dairy, plant protein, supplements.
OTHER = "other"
#: Classified, but the kind could not be determined. NOT a guess, and NOT NULL:
#: NULL means "never looked", which is what keeps re-runs cheap.
UNKNOWN = "unknown"

#: Words that name a CUT of beef and no species at all. An unqualified
#: "Porterhouse" is beef; nothing else is sold under that name.
#:
#: Public, and the single source of this vocabulary (GFP-280). ``matching``
#: imports it to decide which cut a beef item is, and this module builds its
#: species rule from it, so the two cannot disagree about what counts as a beef
#: cut. They previously did: this list is the longer one, and it lived in
#: ``matching``.
BEEF_CUT_WORDS: tuple[str, ...] = (
    "ground round", "sirloin", "ribeye", "rib eye", "chuck roast", "chuck",
    "brisket", "t-bone", "porterhouse", "new york strip", "london broil",
    "filet mignon", "top round", "bottom round", "flank steak",
    "skirt steak", "prime rib", "steak", "steaks",
)

_BEEF_CUT_PATTERNS: tuple[str, ...] = tuple(
    rf"\b{re.escape(word)}\b" for word in BEEF_CUT_WORDS
)

#: Every specific kind, most specific FIRST. Order is load-bearing — see the
#: module docstring's table: fish must beat chicken ("Chicken of the Sea"), and
#: turkey must beat pork ("Turkey Bacon").
KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Fish leads so the "Chicken of the Sea" brand cannot be read as poultry.
    ("fish", (
        r"chicken of the sea",
        r"\b(salmon|tuna|tilapia|cod|halibut|trout|mahi|snapper|catfish"
        r"|haddock|pollock|sardines?|anchov\w*|herring|mackerel|barramundi)\b",
        r"\bfish\b",
    )),
    ("shellfish", (
        r"\b(shrimp|prawns?|crabs?|lobsters?|scallops?|clams?|mussels?"
        r"|oysters?|squid|calamari|crawfish|crayfish)\b",
        r"\bsnow\s+(crab\s+)?legs?\b",   # "Seafood Snow Legs" is snow crab
    )),
    # Turkey and chicken both precede pork so "turkey bacon" / "chicken sausage"
    # are read as the bird, not as the generic cured-pork words they contain.
    ("turkey", (r"\bturkeys?\b",)),
    ("chicken", (r"\bchickens?\b", r"\bcornish hens?\b", r"\bpoultry\b")),
    ("lamb", (r"\blambs?\b", r"\bmutton\b")),
    # GFP-135: AN EXPLICIT SPECIES NAME BEATS A CUT WORD.
    #
    # This is the same principle the turkey/chicken rules above already apply
    # to "turkey bacon"; it had simply never been applied between beef and
    # pork. Beef's cut words -- steak, ribeye, sirloin, brisket, chuck -- name
    # no species at all, so with beef ahead of pork, "Pork Boston Butt Steak"
    # was classified BEEF and the word "pork" in the same name never got its
    # turn. Measured on real data: three foods, including "Villari Prime
    # Boneless Pork Ribeye".
    #
    # Not cosmetic. protein_kind drives `gplan cheapest`, the animal-protein
    # series on both charts, and (after GFP-134) the preference filter -- so a
    # client who ticked "beef" was being offered pork, which for a religious or
    # medical restriction is a serious error rather than an untidy one.
    ("pork", (r"\bpork\b",)),
    ("beef", (r"\bbeef\b", r"\bveal\b")),
    # Only now the cut words, which name a cut and not an animal. Reaching one
    # of these means no species was named, and beef is the right default there:
    # an unqualified "Ribeye Steak" is beef.
    #
    # `burger` is safe here only because the plant-based disqualifier above runs
    # first ("Beyond Burger") and the bakery one catches "Hamburger Buns".
    #
    # GFP-280: built from :data:`BEEF_CUT_WORDS` rather than spelled out here,
    # because `matching` needs the same list to decide which cut a beef item is
    # and had accumulated a longer one -- t-bone, porterhouse, london broil,
    # filet mignon, prime rib. Two lists of beef cuts, and the shorter one
    # deciding species, is how "T-Bone Steak" would have become unclassifiable
    # the moment species was routed through this module.
    ("beef", _BEEF_CUT_PATTERNS),
    # The cured-pork words last: `sausage` and `bacon` are only pork once the
    # birds above have had their chance at the name.
    ("pork", (
        r"\bbacon\b", r"\bhams?\b", r"\bprosciutto\b",
        r"\bpancetta\b", r"\bchorizo\b", r"\bsausages?\b", r"\bpepperoni\b",
    )),
)

#: Just the kind names, in rule order.
KINDS: tuple[str, ...] = tuple(kind for kind, _ in KIND_RULES)

#: Kinds that count as meat for the "cheapest meat protein" panel (GFP-107).
#: Seafood is included by product decision, so this is currently every kind —
#: named explicitly anyway, because the day a non-meat kind is added (egg, soy)
#: the panel must not silently start including it.
MEAT_KINDS: frozenset[str] = frozenset(KINDS)

#: Product FORMS that are never a cut of meat, however much meat vocabulary the
#: name carries. Checked before any kind rule. Each entry is a trap that a
#: substring match falls into — see the module docstring.
#:
#: **These must name the product, not a preparation of it.** A first pass here
#: included ``rub``, ``popcorn`` and ``base``, and each vetoed real meat found in
#: the live catalog: "Dry-**Rub** Seasoned Chicken Thighs" is chicken, "**Popcorn**
#: Shrimp" is shrimp. A disqualifier that fires on how a cut was prepared throws
#: away the very rows this feature exists to rank, so the bar is "the product IS
#: this thing", never "the word appears".
#: Forms whose protein figure must NEVER drive a purchase (GFP-271). A stock
#: cube carries a protein number and costs almost nothing, so on a
#: cheapest-per-gram ranking it beats every real food — but nobody eats 180 g of
#: protein as bouillon. These are not merely "not meat": they are not a protein
#: BUY at all, whatever a client's preferences are.
#:
#: Kept as its own tuple, and spliced into :data:`DISQUALIFIERS` below, so the
#: two can never drift apart. The distinction matters because the two lists are
#: NOT interchangeable: ``DISQUALIFIERS`` also rejects plant-based analogues,
#: which are perfectly good protein for a vegan client and must stay buyable.
#: Filtering a bill by ``DISQUALIFIERS`` would quietly delete their entire diet.
NOT_A_PROTEIN_BUY: tuple[str, ...] = (
    r"\b(broth|stock|bouillon|consomm\w*)\b",  # "Chicken Broth" is not a protein buy
    r"\b(seasonings?|marinades?|gravy)\b",
)

DISQUALIFIERS: tuple[str, ...] = (
    r"beefsteak tomato",                       # produce wearing a beef name
    # Plant-based analogues carry meat words deliberately. They are protein, but
    # they are not meat, and letting "Beyond Burger" win a meat ranking would be
    # a straightforwardly wrong answer to "what meat is cheapest".
    r"\b(plant[\s-]?based|meatless|vegan|veggie|impossible)\b",
    r"\bbeyond\s+(meat|burgers?|beef|sausages?|chicken|steak)\b",
    *NOT_A_PROTEIN_BUY,
    r"\b(ramen|noodles?|soups?)\b",            # "Beef Flavored Ramen"
    r"\b(chips?|crisps?|crackers?)\b",
    r"\btallow\b",                             # "Beef Tallow Fries" is fries
    r"\b(dog|cat|pet)\s+(food|treats?|chews?)\b",
    # GFP-274: A LEGUME IS NEVER A CUT OF MEAT, however its name is flavoured.
    #
    # The rules above defend against meat words used as a BRAND ("Chicken of the
    # Sea") or a FORM ("Beef Flavored Ramen"). This is the third shape: a meat
    # word used as a flavour MODIFIER on a non-meat head noun. Measured live:
    # "Hanover Brown Sugar & Bacon Baked Beans, 16 oz" matched `bacon` and was
    # served as GIANT's cheapest PORK. "Pork and Beans" is the same trap.
    #
    # Numeric guards can never catch this. Beans carry real protein at a real
    # price, so the density is entirely plausible -- the row is wrong in KIND,
    # not in magnitude, and `plausible_density()` has no opinion about kind.
    #
    # Safe as an absolute veto because no cut of meat is named for a legume, so
    # this cannot throw away a real meat row the way `rub` and `popcorn` did.
    r"\b(beans?|lentils?|chickpeas?|garbanzos?|hummus|falafel)\b",
)

#: Bakery forms, which are only decisive when nothing STRONGER contradicts them.
#: Kept apart from :data:`DISQUALIFIERS` because they are words meat products
#: legitimately use: the live catalog has "Ground **Turkey Roll**" and "Everything
#: **Bagel** Smoked Salmon", both real meat, and an absolute bakery veto threw
#: both away. So a named species always beats a bakery word; only the ambiguous
#: burger/ham family loses to one.
WEAK_FORM_DISQUALIFIERS: tuple[str, ...] = (
    r"\b(buns?|bread|rolls?|bagels?|tortillas?|wraps?)\b",
)

#: Kind signals weak enough to lose to a bakery word. "Hamburger Buns" is bread;
#: "Prime Pub Burger" is beef. The only thing telling them apart is whether a
#: bakery form is also present, which is exactly what makes this tier necessary.
WEAK_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("beef", (r"\b(ham)?burgers?\b",)),
)

#: Catalog categories that settle the question before a name is even read.
#: 'Meat'/'Seafood' are NOT here — those are exactly the rows whose kind has to
#: come from the name.
NON_MEAT_CATEGORIES: frozenset[str] = frozenset({
    "dairy", "plant protein", "supplements", "tofu", "whey", "produce", "bakery",
})

#: USDA's own lowercase categories (GFP-24) already name the kind, so they are
#: trusted directly rather than re-derived from a name like "Cod, raw".
CATEGORY_KINDS: dict[str, str] = {
    "beef": "beef", "chicken": "chicken", "pork": "pork", "fish": "fish",
    "turkey": "turkey", "lamb": "lamb", "shellfish": "shellfish",
}

#: Categories whose rows are meat but unspecified — the ones needing the name.
_MEAT_CATEGORIES: frozenset[str] = frozenset({"meat", "seafood"})

_DISQUALIFIER_RE = re.compile("|".join(DISQUALIFIERS), re.IGNORECASE)
_NOT_A_PROTEIN_BUY_RE = re.compile("|".join(NOT_A_PROTEIN_BUY), re.IGNORECASE)
_WEAK_FORM_RE = re.compile("|".join(WEAK_FORM_DISQUALIFIERS), re.IGNORECASE)
_KIND_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile("|".join(patterns), re.IGNORECASE)) for kind, patterns in KIND_RULES
)
_WEAK_KIND_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile("|".join(patterns), re.IGNORECASE))
    for kind, patterns in WEAK_KIND_RULES
)


def is_disqualified(name: str | None) -> bool:
    """True when the NAME describes a product that is never meat, full stop.

    Bakery words are NOT consulted here — they only disqualify a name that has
    no stronger signal, which :func:`kind_from_name` handles.
    """
    return bool(name) and _DISQUALIFIER_RE.search(name) is not None


def is_not_a_protein_buy(name: str | None) -> bool:
    """True when this row's protein figure must never drive a purchase (GFP-271).

    Narrower than :func:`is_disqualified` on purpose, and the narrowness is the
    whole point. ``is_disqualified`` answers "is this a cut of meat?", so it also
    rejects plant-based analogues -- correct for a meat ranking, catastrophic as
    a bill filter, because a vegan client's entire diet is analogues.

    This answers a different question: "could anyone sanely buy this AS protein?"
    Stock, bouillon, gravy and seasoning all carry a protein number and cost
    almost nothing, which is exactly the combination that wins a cheapest-per-
    gram ranking. The optimiser returned one line of beef cooking stock as a
    client's entire 180 g day; this is the predicate that stops it.
    """
    return bool(name) and _NOT_A_PROTEIN_BUY_RE.search(name) is not None


def kind_from_name(name: str | None) -> str | None:
    """The kind a product name states, or ``None`` if it states none.

    Returns ``None`` — not :data:`UNKNOWN` — because "this name says nothing"
    is a different answer from "this food's kind is undetermined"; only
    :func:`classify` knows enough to decide the latter.

    Two tiers, in order: a named species wins outright, and only if there is no
    species does a bakery form get to rule the product out. That order is what
    keeps "Ground Turkey Roll" turkey while "Hamburger Buns" stays bread.
    """
    if not name or is_disqualified(name):
        return None
    for kind, pattern in _KIND_RES:
        if pattern.search(name):
            return kind
    if _WEAK_FORM_RE.search(name):
        return None
    for kind, pattern in _WEAK_KIND_RES:
        if pattern.search(name):
            return kind
    return None


def is_not_meat(name: str | None) -> bool:
    """True when the name positively rules meat out (either disqualifier tier).

    Distinct from "we could not tell": a bread roll is a determined answer, and
    recording it as :data:`UNKNOWN` would leave the catalog looking less
    classified than it is.
    """
    if not name:
        return False
    if is_disqualified(name):
        return True
    return bool(_WEAK_FORM_RE.search(name)) and kind_from_name(name) is None


def classify(name: str | None, category: str | None = None) -> str:
    """The stored value for one food: a kind, :data:`OTHER`, or :data:`UNKNOWN`.

    Order, and why:

    1. A category that rules meat out entirely wins outright — a 'Supplements'
       row is not a cut of beef however its flavour is branded.
    2. Then the NAME, because it is the more specific evidence. A catalog row
       categorised 'fish' but named "Shrimp Salad" is shellfish; deferring to
       the category there produced exactly that error against the live data.
    3. Then the category as a fallback, for USDA rows (GFP-24) whose lowercase
       category already names the kind.
    4. Otherwise unknown — never a guess.
    """
    normalised = (category or "").strip().lower()

    if normalised in NON_MEAT_CATEGORIES:
        return OTHER

    kind = kind_from_name(name)
    if kind is not None:
        return kind
    # Before the 'Meat' fallback below: the catalog files bread rolls and
    # plant-based patties under 'Meat', and calling those UNKNOWN would report
    # a determined answer as an open question.
    if is_not_meat(name):
        return OTHER
    if normalised in CATEGORY_KINDS:
        return CATEGORY_KINDS[normalised]
    if normalised in _MEAT_CATEGORIES:
        # Known to be meat, kind not stated. This is the honest remainder.
        return UNKNOWN
    return UNKNOWN


def classify_all(conn: sqlite3.Connection | None = None, *, reclassify: bool = False) -> dict:
    """Fill in ``foods.protein_kind``. Returns a per-kind count of what it wrote.

    Only touches rows where the column ``IS NULL``, so running it repeatedly is
    cheap and cannot drift — that is the whole reason :data:`UNKNOWN` is stored
    explicitly rather than left as ``NULL``. Pass ``reclassify=True`` to redo
    every row, which is what you want after editing the rules above.
    """
    own = conn or db.connect()
    where = "" if reclassify else " WHERE protein_kind IS NULL"
    rows = own.execute(f"SELECT id, name, category FROM foods{where}").fetchall()

    written: dict[str, int] = {}
    for row in rows:
        value = classify(row["name"], row["category"])
        own.execute("UPDATE foods SET protein_kind = ? WHERE id = ?", (value, row["id"]))
        written[value] = written.get(value, 0) + 1
    own.commit()
    return written


def coverage(conn: sqlite3.Connection | None = None) -> dict:
    """How the catalog currently breaks down, for reporting the 83% honestly.

    ``unclassified`` counts rows never looked at; ``unknown`` counts rows looked
    at and undetermined. Reporting them as one number would hide whether the
    classifier has actually run.
    """
    own = conn or db.connect()
    counts = {
        row["protein_kind"]: row["n"]
        for row in own.execute(
            "SELECT protein_kind, COUNT(*) AS n FROM foods GROUP BY protein_kind"
        )
    }
    meat = sum(counts.get(kind, 0) for kind in MEAT_KINDS)
    return {
        "total": sum(counts.values()),
        "by_kind": {k: v for k, v in counts.items() if k is not None},
        "meat": meat,
        "other": counts.get(OTHER, 0),
        "unknown": counts.get(UNKNOWN, 0),
        "unclassified": counts.get(None, 0),
    }


def ensure_classified(conn: sqlite3.Connection | None = None) -> int:
    """Classify anything new, cheaply. Returns how many rows were filled in.

    Callers that need the kind (the GFP-107 panel, the CLI) call this rather
    than requiring anyone to remember a manual step: a scrape that adds foods
    would otherwise leave them invisible to a kind filter until someone noticed.
    """
    own = conn or db.connect()
    pending = own.execute(
        "SELECT COUNT(*) FROM foods WHERE protein_kind IS NULL"
    ).fetchone()[0]
    if not pending:
        return 0
    return sum(classify_all(own).values())
