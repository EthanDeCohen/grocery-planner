"""Tests for the matcher's evaluation harness (GFP-281).

The point of this module is to catch the two ways an evaluation harness lies:
by comparing the answer key with itself, and by passing vacuously when there is
nothing to measure. Both have bitten this project already, so both are tested
directly rather than assumed away.
"""
import pytest

from grocery_planner import evaluation, matching, protein_kind


def _retailer_food(conn, slug, name, kind):
    """A food as a retailer-direct scraper creates it: its OWN row, carrying
    that retailer's label, NOT a pointer into the curated catalog.

    ``source`` is the retailer rather than 'curated' because foods is unique on
    (source, source_ref) and the curated catalog is already seeded -- which is
    also precisely why these are separate rows in production.
    """
    cur = conn.execute(
        "INSERT INTO foods (name, category, source, source_ref, slug, protein_kind) "
        "VALUES (?, 'Meat & Seafood', 'wholefoods', ?, ?, ?)",
        (name, slug, slug, kind),
    )
    return cur.lastrowid


def _curated(conn, slug):
    """The id of a seeded curated food, by slug."""
    return conn.execute("SELECT id FROM foods WHERE slug = ?", (slug,)).fetchone()["id"]


def _truth(conn, store, item_name, food_id, method="wholefoods_direct"):
    """A retailer-direct row: the answer key, stored as match_source='manual'."""
    conn.execute(
        "INSERT INTO deal_food_match (store, item_name, food_id, confidence, "
        "method, match_source) VALUES (?, ?, ?, 1.0, ?, ?)",
        (store, item_name, food_id, method, matching.MANUAL),
    )


@pytest.fixture
def labelled(conn):
    """The curated catalog classified, plus retailer-direct answer-key foods.

    ``classify_all`` is called rather than the kinds being hand-set, because
    foods.protein_kind is NULL in a fresh database and a fixture that quietly
    supplied the values would hide that -- which is the exact shape of the
    vacuous guard test this project already shipped once.
    """
    protein_kind.classify_all(conn)
    wf_beef = _retailer_food(conn, "wf-ground-beef-8020", "365 Ground Beef 80/20", "beef")
    wf_chicken = _retailer_food(conn, "wf-chicken-breast", "365 Chicken Breast", "chicken")
    conn.commit()
    return {
        "beef": _curated(conn, "beef-ground-80-20"),
        "wf_beef": wf_beef,
        "wf_chicken": wf_chicken,
    }


def test_agreement_is_on_kind_not_on_food_identity(conn, labelled):
    """The bug that made the first implementation report 0% on 683 correct rows.

    Retailer-direct scrapers create a food PER ITEM, so the truth food is never
    the curated food the rules answer with. Comparing ids scores nothing right.
    """
    _truth(conn, "wholefoods", "Ground Beef 80/20", labelled["wf_beef"])
    conn.commit()

    summary = evaluation.harvest(conn)
    assert summary[evaluation.AGREE] == 1
    assert summary[evaluation.DISAGREE] == 0

    row = conn.execute("SELECT * FROM match_evaluation").fetchone()
    assert row["truth_food_id"] != row["rule_food_id"], (
        "fixture no longer exercises the bug: truth and rule point at the same "
        "food, so an id comparison would pass and the regression could return"
    )
    assert row["truth_kind"] == row["rule_kind"] == "beef"


def test_a_real_disagreement_is_recorded(conn, labelled):
    # The answer key says chicken; the name says beef. The rules should fire
    # beef and be marked wrong.
    _truth(conn, "wholefoods", "Ground Beef 80/20", labelled["wf_chicken"])
    conn.commit()

    summary = evaluation.harvest(conn)
    assert summary[evaluation.DISAGREE] == 1
    assert summary[evaluation.AGREE] == 0
    assert summary["precision"] == 0.0


def test_unknown_truth_is_excluded_from_scoring_but_still_reported(conn, labelled):
    """'unknown' is not an answer, and dropping it silently would overstate.

    Marking the rules wrong for disagreeing with a non-answer is what turned 11
    phantom errors into a 98.3% precision figure before this rule existed.
    """
    junk = _retailer_food(conn, "mystery-item", "Mystery Item", evaluation.UNKNOWN_KIND)
    _truth(conn, "wholefoods", "Ground Beef 80/20", junk)
    _truth(conn, "wholefoods", "Chicken Breast", labelled["wf_chicken"])
    conn.commit()

    summary = evaluation.harvest(conn)
    assert summary[evaluation.UNLABELLED] == 1
    assert summary[evaluation.DISAGREE] == 0, "scored against a non-answer"

    report = evaluation.report(conn)
    assert report.unlabelled == 1
    assert report.scored == 1, "unlabelled must not inflate the denominator"
    assert report.precision == 1.0


def test_a_decline_is_recorded_rather_than_omitted(conn, labelled):
    """Recall is the whole point; a decline that leaves no row cannot be counted."""
    _truth(conn, "wholefoods", "Zzzz Nonspecific Grocery Item", labelled["wf_beef"])
    conn.commit()

    summary = evaluation.harvest(conn)
    assert summary[evaluation.DECLINED] == 1

    report = evaluation.report(conn)
    assert report.answer_rate == 0.0
    assert report.recall == 0.0
    assert report.precision is None, "answered nothing -- precision is undefined, not 0"


def test_harvest_refuses_to_report_a_vacuous_zero(conn):
    """The failure mode this project has already hit once.

    foods.protein_kind is NULL until classify_all runs. A harvest against an
    unclassified catalog would report a tidy row of zeroes that reads exactly
    like a clean bill of health.
    """
    # No classify_all() here on purpose: this is a fresh database exactly as a
    # new install has it.
    unclassified = _retailer_food(conn, "wf-ground-beef-8020", "365 Ground Beef 80/20", None)
    _truth(conn, "wholefoods", "Ground Beef 80/20", unclassified)
    conn.commit()

    with pytest.raises(evaluation.NoGroundTruthError, match="classify"):
        evaluation.harvest(conn)


def test_the_answer_key_is_not_compared_with_itself(conn, labelled):
    """match_item must be re-derived, not read back from deal_food_match.

    Reading the stored row would compare the key with itself and report 100%
    forever -- an evaluation harness that can never fail.
    """
    # Truth deliberately contradicts the name. If the harness read the stored
    # row instead of asking the rules, this would come back as agreement.
    _truth(conn, "wholefoods", "Chicken Breast", labelled["wf_beef"])
    conn.commit()

    summary = evaluation.harvest(conn)
    assert summary[evaluation.AGREE] == 0, "harness is reading its own answer key"
    assert summary[evaluation.DISAGREE] == 1


def test_calibration_buckets_measure_the_stated_confidence(conn, labelled):
    _truth(conn, "wholefoods", "Ground Beef 80/20", labelled["wf_beef"])
    conn.commit()
    evaluation.harvest(conn)

    report = evaluation.report(conn)
    populated = [b for b in report.buckets if b.answered]
    assert len(populated) == 1
    bucket = populated[0]
    stated = conn.execute(
        "SELECT rule_confidence FROM match_evaluation"
    ).fetchone()["rule_confidence"]
    assert bucket.low <= stated < bucket.high
    assert bucket.observed == 1.0
    assert bucket.overconfident is False


def test_overconfidence_is_flagged_when_it_happens():
    """The property GFP-271's floor depends on, tested directly.

    Measured on live data the rules are UNDER-confident (the 0.3 bucket scores
    99.3%), so no real bucket exercises this today -- which is exactly why it is
    unit-tested rather than left to a fixture that happens not to trigger it.
    """
    honest = evaluation.Bucket(low=0.9, high=1.01, agree=95, disagree=5)
    assert honest.observed == 0.95
    assert honest.overconfident is False

    lying = evaluation.Bucket(low=0.9, high=1.01, agree=50, disagree=50)
    assert lying.observed == 0.5
    assert lying.overconfident is True


def test_regression_gate_needs_two_runs_and_says_so(conn, labelled):
    """'Cannot tell' must not read as 'fine'."""
    _truth(conn, "wholefoods", "Ground Beef 80/20", labelled["wf_beef"])
    conn.commit()

    regressed, why = evaluation.regressed(conn)
    assert regressed is False
    assert "not enough history" in why

    evaluation.harvest(conn, now="2026-08-14T00:00:00+00:00")
    regressed, why = evaluation.regressed(conn)
    assert regressed is False
    assert "not enough history" in why, "one run is not a comparison"


def test_regression_gate_catches_a_real_fall(conn, labelled):
    _truth(conn, "wholefoods", "Ground Beef 80/20", labelled["wf_beef"])
    conn.commit()
    evaluation.harvest(conn, now="2026-08-13T00:00:00+00:00")   # 100%

    # Second run: same item, answer key flipped to chicken, so the rules are now
    # wrong about it.
    conn.execute(
        "UPDATE deal_food_match SET food_id = ? WHERE item_name = ?",
        (labelled["wf_chicken"], "Ground Beef 80/20"),
    )
    conn.commit()
    evaluation.harvest(conn, now="2026-08-14T00:00:00+00:00")   # 0%

    regressed, why = evaluation.regressed(conn)
    assert regressed is True
    assert "100.0%" in why and "0.0%" in why


def test_runs_accumulate_rather_than_replace(conn, labelled):
    """History is the point -- a harvest that overwrote the last one could not
    show a regression at all."""
    _truth(conn, "wholefoods", "Ground Beef 80/20", labelled["wf_beef"])
    conn.commit()

    evaluation.harvest(conn, now="2026-08-13T00:00:00+00:00")
    evaluation.harvest(conn, now="2026-08-14T00:00:00+00:00")

    stamps = [
        r["evaluated_at"]
        for r in conn.execute(
            "SELECT DISTINCT evaluated_at FROM match_evaluation ORDER BY evaluated_at"
        )
    ]
    assert stamps == ["2026-08-13T00:00:00+00:00", "2026-08-14T00:00:00+00:00"]
    assert evaluation.latest_run(conn) == "2026-08-14T00:00:00+00:00"
