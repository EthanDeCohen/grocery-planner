# Roadmap: from one nutrient to all sections of the food pyramid

**Written 2026-08-12.** The stated goal is that the optimiser eventually plans
against every section of the 2025–2030 inverted pyramid, not protein alone. This
page is the route, and — more importantly — the **gates between stages**, so
that a later reader can tell whether moving on was earned or merely impatient.

Every number below was measured on this date against the live database. None of
it is estimated.

---

## What changed outside the project

The **2025–2030 Dietary Guidelines for Americans** (HHS + USDA, published
2026-01-07) replaced MyPlate's plate with an **inverted pyramid**:

| tier | contents | direction |
| --- | --- | --- |
| wide, top | protein, dairy (full-fat included), healthy fats, vegetables, fruits | eat most |
| narrow, tip | grains, whole preferred | **2–4 servings/day** |

Protein moved to the widest tier, and the recommendation rose to **0.54–0.73 g
per pound** of body weight, from 0.36.

**This validates the product bet rather than undermining it.** Cost per gram of
protein is now closer to the federal model than MyPlate was. Widening to other
groups is an expansion of a correct foundation, not a correction of a wrong one.

---

## Stage 0 — where the project actually is

```
10,555 distinct (store, item_name)
 2,046 answered by a retailer directly     fine, better than name matching
 1,292 matched by the rules                fine
 7,217 unmatched                           the real coverage gap

 8,842 priced rows -> 2,331 usable at the 0.9 confidence floor
 2,413 foods, and food_nutrients holds exactly ONE nutrient: protein
```

Accuracy of the rules, measured against the 2,378 rows where a retailer had
already supplied the answer:

```
agree     650      precision where they guess:  95.4%
disagree   31      and the errors CLUSTER (GFP-280)
declined 1697      recall is the problem, not precision
```

**Read that carefully:** the rules are accurate when they speak and silent most
of the time. The instinct to replace them with a model is wrong — a 95.4%-precise
rule set with written rationale for every decision is not the thing to throw
away. The work is in the 1,697 declines.

---

## The stages, and the gate on each

### Stage 1 — measure the one nutrient we already have · GFP-281

Harvest the labels the app already discards: every retailer-direct source
answers authoritatively, the rules answer independently, and **every
disagreement is a free labelled error**. Report precision, recall, per-method
accuracy, and calibration.

> **GATE:** `confidence` means something. Today 0.9 means "a `cut_keyword` rule
> fired", not "90% likely correct" — and the GFP-274 beans row was 0.9 and
> wrong. Until each confidence band has a measured true rate, **GFP-271's floor
> is a threshold on a non-probability** and no downstream decision resting on it
> is trustworthy.

### Stage 2 — capture everything, consume nothing · GFP-284 *(runs in parallel)*

Store every nutrient a source publishes and every raw category string, verbatim.
Nothing reads any of it. `food_nutrients` is already
`(food_id, nutrient, amount_per_100g, unit)`, so this is **rows, not schema**.

This stage is time, not effort: it must start early because the data cannot be
back-filled. Prices change weekly and pages disappear, so a nutrient not
captured in August 2026 is not recoverable in v3 by re-reading anything.

> **GATE:** one to two months of accumulation before v2 releases, and a
> **size measurement** — this file ships to customers and is deliberately
> disposable, so unread rows are not free.

### Stage 3 — decide, before building · GFP-283 spike

Two questions, both answerable only with Stage 2's data in hand:

1. **Feasibility.** What fraction of foods could be assigned a pyramid tier from
   data already held? If it is low, the answer is more sources, not more code.
2. **Meaning.** What does "optimise" mean across groups? Cheapest-per-gram-protein
   is a *total order*. Several groups at once is multi-objective — a plan can be
   cheaper on protein and worse on vegetables — and "cheapest" stops being
   well-defined. Weighted objective, hard per-group constraints with cost
   minimised subject to them, or a report rather than an optimiser?

> **GATE:** the objective is written down and agreed before any code. This is a
> product decision wearing an algorithm's clothes, and discovering it during
> implementation is how it gets decided by accident.

### Stage 4 — build the group vocabulary, then the engine

Only now: map categories onto tiers, populate the target engine per group.

> **GATE, and the hardest-won lesson here:** do **not** hand-write a second
> vocabulary. `foods.category` holds retailer strings — 'Baby Food Purees',
> 'Block', 'Bone Broth', 'Bread Flour' — and mapping them by hand is exactly
> what `protein_kind` did for meat. On 2026-08-12 that approach produced four
> defects in a single day: two vocabularies drifting apart (GFP-279), a veto
> that ate real chicken and shrimp, a regex that misses "Beanee Weenee", and a
> species precedence contradicting its own docstring (GFP-280). Whatever is
> built here must assert **relationships**, not spellings (GFP-179).

---

## Why the order is what it is

The stages are sequenced by **what each one makes measurable**, not by what is
most interesting to build.

* Measuring one nutrient is cheap and tells you whether the method works at all.
* Adding a second nutrient before the first is measured **multiplies unknowns**:
  every source has different gaps, and the confidence problem compounds per
  nutrient.
* Capture is the only stage that is urgent, because it is the only one whose
  input disappears if you wait.

The through-line: **record early, decide late, build last.**

---

## What would change this plan

Written down so that changing course is a decision rather than a drift.

* **If Stage 1 shows precision is much worse than 95%** — the problem is the
  rules, not the coverage, and Stage 4's vocabulary work is on sand. Fix the
  matcher first.
* **If Stage 3 shows tier assignment is under ~50% feasible** — the constraint
  is data, not modelling. Pursue sources that publish structured categories
  (Target's redsky is the best-shaped found so far) rather than writing rules.
* **If the multi-objective question has no clean answer** — ship a *report*
  showing group coverage alongside the protein plan, and do not pretend to
  optimise something that has no single ordering. A report that is honest beats
  an optimiser that is arbitrary.

---

## Ticket map

| stage | ticket | release |
| --- | --- | --- |
| 0 | GFP-282 protein band admits the federal range | v2.0 — **done** |
| 0 | GFP-280 matcher species precedence | v2.0 |
| 1 | GFP-281 continuous evaluation and calibration | v2.0 |
| 2 | GFP-284 capture every nutrient, consume none | v2.0 |
| 3–4 | GFP-283 model the pyramid food groups | v3.0 |

Related: `coverage.md` for what the optimiser can price today, and
`v2-status.md` for where the release as a whole stands.
