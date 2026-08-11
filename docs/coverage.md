# Coverage: what the optimiser can actually price

**Measured 2026-08-11 from the live database.** Refresh this after any scrape or
enrichment change — the numbers move and stale ones are worse than none.

The only number that matters is **usable rows**: rows that reach a
`cost_per_gram_protein` figure. A priced row without a size or a protein figure
cannot be ranked and does not count, however many of them there are.

---

## Per market

### Greensboro NC (27401)

```
3,614 priced rows  ->  809 usable   (22.4%)
9 stores with data  ->    4 contributing
```

| contributing | data but **0 usable** |
| --- | --- |
| Harris Teeter, Whole Foods, Food Lion, Lowes Foods (1 row) | Aldi, Lidl, Publix, Sprouts, Target |

Against the 17-chain target list: **4 of 17 — 24%.** Three stores supply 808 of
the 809 rows; Lowes Foods' single row is an accident (a size that happened to
appear in an item name).

### Philadelphia PA (19103)

```
1,146 priced rows  ->   99 usable   (8.6%)
5 stores with data  ->    2 contributing
```

| contributing | data but **0 usable** |
| --- | --- |
| GIANT (97, PRISM catalogue), ACME (2) | H Mart, Wegmans (ad), Weis |

Against the 20-chain target list: **2 of 20 — 10%**, and ACME's 2 are accidents.
**Philadelphia is effectively one store: GIANT.**

### Chapel Hill NC (27514) — not a target market

```
600 priced rows  ->  469 usable   (78.2%)
Wegmans only
```

The highest-yielding ZIP in the database is one nobody asked for. It exists only
because Wegmans store 140 is there and it was the nearest store to the default
postal code. See "Immediate" below — this is the most valuable fact on the page.

---

## Per source

| store | source | priced | usable | yield |
| --- | --- | --- | --- | --- |
| harristeeter | `kroger-api` | 987 | **540** | 54.7% |
| wegmans | `wegmans-api` | 600 | **469** | 78.2% |
| wholefoods | storefront | 244 | **150** | 61.5% |
| foodlion | `prism` | 397 | **97** | 24.4% |
| giant | `prism` | 398 | **97** | 24.4% |
| harristeeter | flipp ad | 366 | 13 | 3.6% |
| foodlion | flipp ad | 302 | 8 | 2.6% |
| acme | flipp ad | 245 | 2 | 0.8% |
| lowesfoods | flipp ad | 323 | 1 | 0.3% |
| wegmans, weis, hmart, publix, aldi, lidl, sprouts, target | flipp ad | 1,498 | **0** | 0.0% |
| **TOTAL** | | **5,360** | **1,377** | **25.7%** |

**18 scrapers registered. 7 stores produce anything. 5 sources carry 99.8% of
the value.** Every one of those five publishes structured product data on
purpose; see `spikes/GFP-197-source-survey.md` for the 30-odd chains that do not.

---

## What is left, ranked by return

None of these requires a new store or a new source. All four operate on
data already held or already reachable.

| # | work | est. usable rows | market |
| --- | --- | --- | --- |
| 1 | **Scrape Wegmans at a Philadelphia ZIP** | **+470** | Philadelphia |
| 2 | Raise the Wegmans SKU cap, 600 -> 1,535 | +700 | both, per ZIP |
| 3 | PRISM protein fix (Food Lion + GIANT) | +400 | both |
| 4 | Cross-store size borrowing | +113 | all |

### 1. Wegmans at a Philadelphia ZIP — minutes of work

Wegmans operates **16 stores in Pennsylvania**, including King of Prussia
(store 48), Malvern, Collegeville, Montgomeryville and Downingtown. The scraper
already resolves a store from a ZIP through Wegmans' own store API. Nothing
needs building:

```
gplan scrape wegmans-api --zip 19406      # King of Prussia
```

At Chapel Hill's measured yield that is roughly **+470 usable rows**, taking
Philadelphia from 99 to about 570, and from two contributing stores to three.
Philadelphia would go from **8.6% to roughly 35%**.

This is the single largest available gain in the project and it is a parameter.

### 2. The Wegmans SKU cap

`DEFAULT_MAX_PRODUCTS = 600`, and discovery found **1,535** protein SKUs. The
remaining 935 are already identified and shipped as package data
(`grocery_planner/data/wegmans_skus.json`); they are simply not fetched. At the
measured 78.2% yield that is roughly **+700 rows per ZIP scraped**.

Cost: about 6 minutes of wall clock per store at the current 0.4s delay.

### 3. PRISM protein

Food Lion and GIANT sit at **24.4%** while their catalogue pages carry
`Protein 18 g` in the page state. `prism.py` writes that figure into the row's
`notes` and nothing ever reads it.

The fix is the one already proven on Wegmans: write a `foods` row, its protein
density, and a `deal_food_match` at confidence 1.0 with `match_source=MANUAL`,
exactly as `kroger.py` and `wegmans_api.py` do. No new code path, no new column,
no change to `savings.py` — the existing chain finds it.

That change took Wegmans from **27.7% to 78.2%**. Applied to 795 PRISM rows it
is worth roughly **+400**.

### 4. Cross-store size borrowing

`sourcelink` (GFP-248) joins a promo row to a catalogue row **within one store**.
Food size is a property of the product, not the shop.

Measured: **113 of 288 (39%)** price-only protein rows could borrow a size from
a catalogue already in the database — for example Food Lion's
`BONELESS RIBEYE STEAK` from Harris Teeter's `Prime Beef Boneless Ribeye Steak,
1 lb` at overlap 1.00.

Carries real risk and needs guarding: cross-store borrows must rank below
same-store ones, and the existing `MAX_PRICE_RATIO` veto and contradiction guard
both still apply. Do it after 1-3, which enlarge the catalogue pool it borrows
from.

---

## If all four land

```
now      1,377 usable
+1,683   (470 + 700 + 400 + 113)
------
~3,060   usable rows, roughly 2.2x
```

Philadelphia moves from one contributing store to three. Greensboro's Food Lion
roughly triples. **No new chain is added, because the survey established there
are none left to add** — see `spikes/GFP-197-source-survey.md`.

---

## The lesson this page exists to hold

Between 2026-08-09 and 2026-08-11 the registry went from 7 scrapers to 18.
Usable rows went from 908 to 1,377 — and **469 of that 469-row gain came from a
single source**, Wegmans. The ten new ad-only banners added 2,066 priced rows
and **3** usable ones.

**Count catalogues, not chains.** A weekly ad is the per-ZIP price half of a
pair; it can never carry size or protein, and on its own it yields nothing.
