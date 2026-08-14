# ######### decohen-partners ##########
# Protein Ledger
"""Measure the matcher instead of counting it (GFP-281).

``match_deals`` reports ``matched: 1292, unmatched: 7217``. That is throughput.
It says nothing about whether any of the 1292 are *right*, and until this module
existed every rule change was validated by reading a dozen rows of
``gp cheapest`` and judging that they looked plausible. GFP-274 is what that
costs: a tin of baked beans was GIANT's cheapest pork, at confidence 0.9, and it
took a screenshot to notice.

THE LABELS ALREADY EXIST
------------------------
Retailer-direct scrapers write ``deal_food_match`` rows at confidence 1.0
carrying the retailer's own answer (``kroger_api_direct``,
``traderjoes_label_direct``, ``wholefoods_direct``, ``sprouts_label_direct``),
and the nutritionist's corrections (GFP-25) do the same by hand. All are stored
with ``match_source='manual'``, which is what stops ``match_deals`` overwriting
them -- and what makes them a ready-made answer key.

WHAT IS COMPARED, AND WHY IT IS NOT ``food_id``
-----------------------------------------------
The retailer-direct scrapers do not point at the curated catalogue -- they
create a food PER ITEM carrying that retailer's own label. "365 Ground Beef
80/20" is food 33; the rules answer with curated food 1, ``beef-ground-80-20``.
Both describe the same beef. Comparing ``food_id`` equality scores **0%
agreement on 683 answered items, every one of them correct** -- measured, not
supposed. The comparison fails, not the matcher.

So the axis is :data:`foods.protein_kind` -- beef vs chicken vs pork. That is
what GFP-280 was fought over and what decides which density an item is priced
with, which is what actually reaches the optimiser.

``unknown`` IS NOT AN ANSWER. Roughly 40% of foods carry ``protein_kind
'unknown'``. Scoring against those would mark the rules wrong for disagreeing
with a non-answer, so they are recorded as :data:`UNLABELLED` and excluded from
precision and recall -- recorded rather than skipped, because a sample that
silently drops 40% of its rows reads as though it covered everything.

WHAT IS MEASURED
----------------
* **Precision** -- of the items it answered, how many were right.
* **Answer rate** -- how often it answered at all. This is the weak number: 71%
  of the time the rules decline on an item whose answer was sitting right there.
* **Calibration** -- the observed accuracy of each stated-confidence bucket.
  ``confidence`` is currently a provenance label wearing numeric clothes: 0.9
  means "a cut_keyword rule fired", not "90% likely correct". Until the true
  rate is measured, GFP-271's 0.9 floor is a threshold on a non-probability.

IT PROPOSES, IT NEVER TUNES
---------------------------
Nothing here writes rules back. A rule silently rewritten by a job to improve a
metric would break the same-inputs-same-plan invariant (GFP-224) and would be
the same unfalsifiable practice in a new costume.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import db, matching

#: Outcomes, stored rather than derived -- see the migration's rationale.
AGREE = "agree"
DISAGREE = "disagree"
DECLINED = "declined"
#: The truth row has no usable kind. Counted and reported, never silently
#: dropped, and never scored.
UNLABELLED = "unlabelled"

#: protein_kind's own value for "we could not tell", which is not an answer.
UNKNOWN_KIND = "unknown"

#: Calibration bucket edges: the confidences the rules actually emit
#: (CONFIDENCE_LOW/MEDIUM/HIGH), so the buckets are the vocabulary rather than
#: an arbitrary histogram.
BUCKET_EDGES: tuple[float, ...] = (0.0, 0.3, 0.6, 0.9, 1.01)


class NoGroundTruthError(RuntimeError):
    """Raised when nothing in the database can serve as an answer key.

    A harvest that finds no usable truth would report precision ``None`` and a
    tidy row of zeroes, which reads exactly like a clean bill of health. This
    project has already been bitten once by a guard test that passed vacuously
    (it read ``foods.protein_kind``, which is NULL until ``classify_all`` runs),
    so "there was nothing to measure" is raised rather than returned.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kinds(conn: sqlite3.Connection) -> dict[int, str | None]:
    """``foods.id -> protein_kind``, for both sides of the comparison."""
    return {
        row["id"]: row["protein_kind"]
        for row in conn.execute("SELECT id, protein_kind FROM foods")
    }


def _usable(kind: str | None) -> bool:
    return bool(kind) and kind != UNKNOWN_KIND


@dataclass(frozen=True)
class MethodScore:
    """Accuracy of one rule method, e.g. ``cut_keyword``."""

    method: str
    agree: int
    disagree: int

    @property
    def answered(self) -> int:
        return self.agree + self.disagree

    @property
    def precision(self) -> float | None:
        return self.agree / self.answered if self.answered else None


@dataclass(frozen=True)
class Bucket:
    """One stated-confidence band and what it turned out to be worth."""

    low: float
    high: float
    agree: int
    disagree: int

    @property
    def answered(self) -> int:
        return self.agree + self.disagree

    @property
    def observed(self) -> float | None:
        """Measured accuracy of this band. Compare against :attr:`low`."""
        return self.agree / self.answered if self.answered else None

    @property
    def overconfident(self) -> bool:
        """Claimed more than it delivered -- what GFP-271's floor assumes away."""
        seen = self.observed
        return seen is not None and seen < self.low


@dataclass(frozen=True)
class Report:
    """One harvest, scored.

    ``agree``/``disagree``/``declined`` are over items with a usable answer.
    ``unlabelled`` is reported separately and scored in nothing.
    """

    evaluated_at: str
    agree: int
    disagree: int
    declined: int
    unlabelled: int
    by_method: list[MethodScore] = field(default_factory=list)
    buckets: list[Bucket] = field(default_factory=list)

    @property
    def scored(self) -> int:
        """Items that could be scored at all."""
        return self.agree + self.disagree + self.declined

    @property
    def answered(self) -> int:
        return self.agree + self.disagree

    @property
    def precision(self) -> float | None:
        """Of the items it answered, the share it got right."""
        return self.agree / self.answered if self.answered else None

    @property
    def answer_rate(self) -> float | None:
        """How often it answered at all -- the weak number."""
        return self.answered / self.scored if self.scored else None

    @property
    def recall(self) -> float | None:
        """Of every item whose answer was known, the share it got right.

        Distinct from :attr:`precision`, which forgives a decline: a matcher
        that answers once and is correct scores precision 1.0 and recall ~0.
        """
        return self.agree / self.scored if self.scored else None


def harvest(
    conn: sqlite3.Connection | None = None, now: str | None = None
) -> dict[str, Any]:
    """Ask the rules about every item whose answer is already known, record the
    comparison, and return summary counts.

    Deliberately re-derives the rule verdict with :func:`matching.match_item`
    rather than reading the stored ``deal_food_match`` row: for these items the
    stored row IS the answer key, so reading it would compare the key with
    itself and report 100% forever.
    """
    own = conn or db.connect()
    stamp = now or _now()
    food_ids = matching._catalog_food_ids(own)
    kinds = _kinds(own)

    truth_rows = own.execute(
        "SELECT store, item_name, food_id, method FROM deal_food_match "
        "WHERE match_source = ? AND food_id IS NOT NULL",
        (matching.MANUAL,),
    ).fetchall()

    counts = {AGREE: 0, DISAGREE: 0, DECLINED: 0, UNLABELLED: 0}
    to_insert: list[tuple[Any, ...]] = []

    for row in truth_rows:
        truth_kind = kinds.get(row["food_id"])
        verdict = matching.match_item(row["item_name"])

        if verdict is None:
            rule_food_id = rule_kind = rule_method = rule_confidence = None
        else:
            rule_food_id = food_ids.get(verdict.source_ref)
            rule_kind = kinds.get(rule_food_id) if rule_food_id else None
            rule_method = verdict.method
            rule_confidence = verdict.confidence

        if not _usable(truth_kind):
            outcome = UNLABELLED
        elif not _usable(rule_kind):
            # Either the rules declined outright, or they answered with a food
            # the catalog cannot place. Both leave the optimiser with nothing,
            # so both are declines; calling the second a disagreement would
            # blame the rules for a catalog gap.
            outcome = DECLINED
        else:
            outcome = AGREE if rule_kind == truth_kind else DISAGREE

        counts[outcome] += 1
        to_insert.append((
            stamp, row["store"], row["item_name"],
            rule_food_id, rule_kind, rule_method, rule_confidence,
            row["food_id"], truth_kind, row["method"], outcome,
        ))

    scored = counts[AGREE] + counts[DISAGREE] + counts[DECLINED]
    if not scored:
        raise NoGroundTruthError(
            f"{len(truth_rows)} labelled rows, none with a usable protein_kind. "
            "Run `gplan nutrition classify` first -- foods.protein_kind is NULL "
            "until it does, and a harvest against it would report a vacuous 0."
        )

    own.executemany(
        "INSERT INTO match_evaluation (evaluated_at, store, item_name, "
        "rule_food_id, rule_kind, rule_method, rule_confidence, truth_food_id, "
        "truth_kind, truth_method, outcome) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        to_insert,
    )
    own.commit()

    answered = counts[AGREE] + counts[DISAGREE]
    return {
        "evaluated_at": stamp,
        "labelled": len(truth_rows),
        **counts,
        "precision": counts[AGREE] / answered if answered else None,
    }


def latest_run(conn: sqlite3.Connection | None = None) -> str | None:
    """The ``evaluated_at`` of the most recent harvest, or None."""
    own = conn or db.connect()
    row = own.execute("SELECT MAX(evaluated_at) AS at FROM match_evaluation").fetchone()
    return row["at"] if row else None


def report(
    conn: sqlite3.Connection | None = None, evaluated_at: str | None = None
) -> Report | None:
    """Score one harvest. Defaults to the most recent. None if never run."""
    own = conn or db.connect()
    stamp = evaluated_at or latest_run(own)
    if stamp is None:
        return None

    counts = {AGREE: 0, DISAGREE: 0, DECLINED: 0, UNLABELLED: 0}
    for row in own.execute(
        "SELECT outcome, COUNT(*) AS n FROM match_evaluation "
        "WHERE evaluated_at = ? GROUP BY outcome",
        (stamp,),
    ):
        counts[row["outcome"]] = row["n"]

    by_method = [
        MethodScore(row["rule_method"], row["agree"] or 0, row["disagree"] or 0)
        for row in own.execute(
            "SELECT rule_method, "
            "SUM(outcome = ?) AS agree, SUM(outcome = ?) AS disagree "
            "FROM match_evaluation WHERE evaluated_at = ? AND rule_method IS NOT NULL "
            "GROUP BY rule_method ORDER BY rule_method",
            (AGREE, DISAGREE, stamp),
        )
    ]

    buckets: list[Bucket] = []
    for low, high in zip(BUCKET_EDGES, BUCKET_EDGES[1:]):
        row = own.execute(
            "SELECT SUM(outcome = ?) AS agree, SUM(outcome = ?) AS disagree "
            "FROM match_evaluation WHERE evaluated_at = ? "
            "AND rule_confidence >= ? AND rule_confidence < ?",
            (AGREE, DISAGREE, stamp, low, high),
        ).fetchone()
        buckets.append(Bucket(low, high, row["agree"] or 0, row["disagree"] or 0))

    return Report(
        evaluated_at=stamp,
        agree=counts[AGREE],
        disagree=counts[DISAGREE],
        declined=counts[DECLINED],
        unlabelled=counts[UNLABELLED],
        by_method=by_method,
        buckets=buckets,
    )


def regressed(
    conn: sqlite3.Connection | None = None, tolerance: float = 0.01
) -> tuple[bool, str]:
    """``(regressed, explanation)`` comparing the last two harvests.

    A gate, not an alarm: precision drifting by a fraction of a point between
    runs is noise, so ``tolerance`` separates a real fall from the ordinary
    movement of a changing deal set. Returns False *with a reason* when there is
    not enough history to judge -- "cannot tell" must not read as "fine".
    """
    own = conn or db.connect()
    stamps = [
        row["evaluated_at"]
        for row in own.execute(
            "SELECT DISTINCT evaluated_at FROM match_evaluation "
            "ORDER BY evaluated_at DESC LIMIT 2"
        )
    ]
    if len(stamps) < 2:
        return False, "not enough history to compare (needs two harvests)"

    current, previous = report(own, stamps[0]), report(own, stamps[1])
    if current is None or previous is None:
        return False, "not enough history to compare"
    if current.precision is None or previous.precision is None:
        return False, "a run answered nothing at all -- precision undefined"

    drop = previous.precision - current.precision
    movement = (
        f"precision {previous.precision:.1%} -> {current.precision:.1%} "
        f"between {stamps[1]} and {stamps[0]}"
    )
    if drop > tolerance:
        return True, f"{movement} -- fell {drop:.1%}, tolerance {tolerance:.1%}"
    return False, movement
