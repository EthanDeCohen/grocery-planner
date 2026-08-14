-- GFP-281: measure the matcher instead of counting it.
--
-- `match_deals` reports "matched: 1292, unmatched: 7217". That is throughput.
-- It says nothing about whether any of the 1292 are RIGHT, and every rule change
-- to date has been validated by reading a dozen rows of `gp cheapest` and
-- judging that they looked plausible. GFP-274 is what that costs: a tin of baked
-- beans was GIANT's cheapest pork, at confidence 0.9, and it took a screenshot
-- to notice.
--
-- THE LABELS ALREADY EXIST AND ARE BEING THROWN AWAY.
-- Retailer-direct sources (kroger_api_direct, traderjoes_label_direct,
-- wholefoods_direct, sprouts_label_direct) write deal_food_match rows at
-- confidence 1.0 carrying the retailer's own answer, and the nutritionist's
-- corrections (GFP-25) do the same by hand. All are stored with
-- match_source='manual', which is what stops match_deals overwriting them --
-- and what makes them a ready-made answer key. The keyword rules independently
-- have an opinion about the same item name, so every disagreement is a labelled
-- error, free, with no human in the loop.
--
-- First measurement, 2026-08-12, over the 2,378 rows carrying a retailer answer:
--
--     rules agree                650
--     rules DISAGREE              31
--     rules declined to guess  1,697
--     precision where it guesses  95.4%
--
-- Precision is high; RECALL IS POOR. 71% of the time the rules decline on an
-- item whose answer was sitting right there. Coverage is there, not in adding
-- more sources. The 31 errors also clustered into one product family the
-- codebase believed it had fixed (GFP-280) -- the argument for making this
-- permanent rather than a script someone ran once.
--
-- WHAT IS COMPARED, AND WHY IT IS NOT food_id.
-- This is the trap in the whole ticket. The retailer-direct scrapers do not
-- point at the curated catalogue -- they create a food PER ITEM carrying that
-- retailer's own label. So "365 Ground Beef 80/20" is food 33 and the rules
-- answer with curated food 1, `beef-ground-80-20`. Both describe the same beef.
-- Comparing food_id equality scores 0% agreement on 683 answered items, all of
-- them correct: the comparison, not the matcher, is what fails.
--
-- The comparison is therefore on foods.protein_kind -- beef vs chicken vs pork.
-- That is the axis GFP-280 was fought on and the one that decides which density
-- an item is priced with, which is what reaches the optimiser.
--
-- 'unknown' IS NOT AN ANSWER. 928 of 2,413 foods carry protein_kind 'unknown'.
-- Scoring against those would mark the rules wrong for disagreeing with a
-- non-answer. They are recorded with outcome 'unlabelled' and excluded from
-- precision and recall -- recorded rather than skipped, because a sample that
-- silently drops 40% of its rows reads as if it covered everything.
--
-- WHY A TABLE AND NOT A REPORT.
-- Three of the four things this ticket needs cannot be computed from the
-- current state of deal_food_match:
--
--   * RECALL needs the declines counted, and a decline leaves no row behind.
--     'declined' is a first-class outcome here, not an absent record.
--   * CALIBRATION needs the confidence the rules STATED at the time, including
--     when they were wrong. Today `confidence` is a provenance label wearing
--     numeric clothes: 0.9 means "a cut_keyword rule fired", not "90% likely
--     correct". Until the true rate of each bucket is measured, GFP-271's 0.9
--     floor is a threshold on a non-probability.
--   * REGRESSION needs history. Precision falling between runs has to be
--     visible, which means keeping the runs.
--
-- APPEND-ONLY, AND THE VERDICT IS STORED RATHER THAN DERIVED.
-- A row records what the rules said on a date against what the retailer said on
-- that date. Recomputing `outcome` later from the current catalogue would let a
-- food edit silently rewrite history and quietly repair a past regression --
-- exactly the unscientific thing this ticket exists to end. Every row in one
-- harvest shares an `evaluated_at`, and that IS the run identifier; a run has no
-- attributes beyond its instant, so it needs no table of its own.
--
-- NOTHING HERE IS READ ON THE SOLVE PATH. Written after ingest, read by
-- reporting. The optimiser never consults it, so the same-inputs-same-plan
-- invariant (GFP-224) is untouched.
--
-- IT PROPOSES, IT NEVER TUNES. Nothing writes rules back from this data. A rule
-- silently rewritten by a job to improve a metric would break that same
-- invariant and would be the same unfalsifiable practice in a new costume.

CREATE TABLE IF NOT EXISTS match_evaluation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identical for every row of one harvest: this IS the run id.
    evaluated_at    TEXT NOT NULL,

    store           TEXT NOT NULL,
    item_name       TEXT NOT NULL,

    -- What the keyword rules said, asked independently of any stored match.
    -- All NULL when the rules declined -- the case that matters most, since it
    -- is 71% of them.
    rule_food_id    INTEGER REFERENCES foods(id) ON DELETE SET NULL,
    rule_kind       TEXT,
    rule_method     TEXT,
    rule_confidence REAL,

    -- The authoritative answer. food_id is kept for provenance; rule_kind vs
    -- truth_kind is what `outcome` is actually decided on -- see above.
    truth_food_id   INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    truth_kind      TEXT,
    -- 'kroger_api_direct' | 'traderjoes_label_direct' | 'wholefoods_direct' |
    -- 'sprouts_label_direct' | 'manual' (a nutritionist correction, GFP-25).
    truth_method    TEXT NOT NULL,

    -- 'agree' | 'disagree' | 'declined' | 'unlabelled'. Stored, not derived.
    outcome         TEXT NOT NULL
);

-- Reporting reads one run at a time, then slices it by outcome.
CREATE INDEX IF NOT EXISTS idx_match_evaluation_run
    ON match_evaluation(evaluated_at, outcome);

-- Calibration buckets by stated confidence across runs; per-method accuracy
-- groups by the method that made the claim.
CREATE INDEX IF NOT EXISTS idx_match_evaluation_method
    ON match_evaluation(rule_method, rule_confidence);
