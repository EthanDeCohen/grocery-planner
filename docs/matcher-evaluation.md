# Is the matcher any good?

**First honest answer: 2026-08-14.** Before this the project could say how many
items it matched and not whether any of them were right. `gplan evaluate run`
regenerates everything below; re-run it after any rule, catalogue, or scraper
change, because stale accuracy numbers are worse than none.

```
scored 1,453   agree 671   disagree 1   declined 781
excluded 925 with no usable answer (protein_kind 'unknown')

precision    99.9%   of what it answered
answer rate  46.2%   of what it could have
recall       46.2%   right, out of everything known
```

## Where the answer key comes from

Nothing was labelled by hand for this. Retailer-direct scrapers
(`kroger_api_direct`, `traderjoes_label_direct`, `wholefoods_direct`,
`sprouts_label_direct`) already store the retailer's own answer per item at
confidence 1.0, and the nutritionist's corrections (GFP-25) do the same. All
land in `deal_food_match` with `match_source='manual'`, which is what stops
`match_deals` overwriting them — and what makes them a ready-made answer key.

The keyword rules independently have an opinion about the same item names. Every
disagreement is a labelled error, free, with no human in the loop.

## The comparison is on *kind*, not on `food_id`

This is the trap, and the first implementation fell into it: it reported **0%
agreement on 683 items that were all correct.**

Retailer-direct scrapers do not point at the curated catalogue. They create a
food **per item** carrying that retailer's own label. "365 Ground Beef 80/20" is
food 33; the rules answer with curated food 1, `beef-ground-80-20`. Both describe
the same beef, and comparing ids can never agree by construction.

So the axis is `foods.protein_kind` — beef vs chicken vs pork. That is what
GFP-280 was fought over and what decides which density an item is priced with,
which is what actually reaches the optimiser.

**`unknown` is not an answer.** ~40% of foods carry `protein_kind 'unknown'`.
Scoring against those would mark the rules wrong for disagreeing with a
non-answer, so they are excluded — and *reported*, because a sample that quietly
drops 40% of its rows reads as though it covered everything.

## Precision is not the problem

One disagreement in 672 answers, and it is arguable:

| item | rules | retailer |
| --- | --- | --- |
| Gluten Free Breaded Shrimp, 12 oz | `shellfish` | `other` |

The rules are defensibly right and the answer key is coarse.

## Recall is the problem — and it is concentrated

781 declines, broken down by what the item actually was:

| truth kind | declined | |
| --- | --- | --- |
| other | 542 | **correct to decline** — not a protein |
| turkey | 92 | no curated food exists |
| pork | 60 | |
| chicken | 28 | |
| shellfish | 23 | |
| beef | 18 | |
| fish | 15 | |
| lamb | 3 | no curated food exists |

**542 of the 781 declines are right.** The real gap is 239 items, and **turkey
and lamb are 95 of them — 40% of the whole problem.** That is not a matcher
weakness: `matching._KINDS_WITHOUT_A_CURATED_FOOD` is exactly
`frozenset({"turkey", "lamb"})`. The rules recognise these items and have
nowhere to put them. Filed as **GFP-290**; it is catalogue work, not rule work.

## Calibration inverts the assumption behind the quality floor

| stated confidence | answered | observed accuracy |
| --- | --- | --- |
| [0.3, 0.60) | 135 | **99.3%** |
| [0.6, 0.90) | 154 | 100.0% |
| [0.9, 1.01) | 383 | 100.0% |

**Nothing is overconfident. Everything is badly under-confident.** A rule
announcing 0.3 is right 99.3% of the time.

GFP-271's `MIN_MATCH_CONFIDENCE = 0.9` costs **464 usable rows** (see
`coverage.md`). On this evidence it is a threshold on a number that means "a
keyword rule fired", not "31% likely correct".

**Do not just delete it.** Two caveats, filed with the rest as **GFP-291**:

1. This is measured only where a retailer label exists. Unlabelled items may be
   the harder ones, so this is a favourable sample, not a random one.
2. GFP-271 fixed a real defect. The beans row (GFP-274) was **0.9 and wrong** —
   inside the band this table calls 100%. Its error was kind-correct but
   food-wrong, so this harness cannot see that class at all. A density axis is
   the missing measurement.

## What it will not do

It proposes; it never tunes. Nothing writes rules back from this data. A rule
silently rewritten by a job to improve a metric would break the
same-inputs-same-plan invariant (GFP-224) and would be the same unfalsifiable
practice this harness exists to end.
