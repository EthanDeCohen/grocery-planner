# Coverage: what the optimiser can actually price

> ## Update — 2026-08-14: Publix changed twice, and the taxonomy widened
>
> Read this before trusting anything below it.
>
> **`publix-catalog` no longer exists.** GFP-304 deleted the Parse.bot Publix
> scraper. Every row in the tables below attributed to it (12 rows, 11 usable)
> is history, and the whole "Parse.bot budget" section at the end is now about
> **Walmart only** — which fits the free tier on its own.
>
> **Publix is reached free through Instacart**, at `delivery.publix.com`
> (GFP-293). It is a third tenant of the client already running Sprouts and
> ALDI. But it has **written zero rows so far**: the pricing pass throttles hard
> — three requests and it backs off to 30s with a ten-minute cooldown (GFP-293
> comment). Treat Publix as unmeasured, not as improved.
>
> **Its prices are also above shelf**, by Publix's own published policy, and the
> same is true of Costco Same-Day. Sprouts is the exception that tracks in-store
> prices. See GFP-297 and GFP-298 — this is a property of the source, and the
> per-store markers in GFP-308 exist to say so in the UI.
>
> **`protein_kind` now names dairy, eggs and plant protein** (GFP-295), so the
> `unknown` counts quoted below are wrong: 928 → 383, with 596 foods gaining a
> real kind. Meat classification did not move. The matcher's answer rate fell
> from 46.3% to 31.5% — not a regression, but the denominator growing as items
> that hid as `unknown` became scoreable.
>
> **Not re-measured:** every per-source and per-market table below. They need a
> fresh scrape to be honest, and the shapes are still sound even where the
> totals have moved.


> ## Update — 2026-08-12, later the same day: the quality floor is FIXED
>
> Everything below this box was measured **before** GFP-271, GFP-274 and
> GFP-279 landed, and the "quality floor" section still describes the beef-stock
> failure in the present tense. It is no longer live. Re-measured after the fix:
>
> ```
> 8,842 priced rows -> 2,795 usable with no floor
>                   -> 2,331 usable at the 0.9 floor   (-464)
> ```
>
> **The floor costs 464 rows, not the 539 predicted below.** The prediction was
> made by counting rows under 0.9; the measured figure is lower because GFP-279
> retracted 49 bad matches outright, so some rows the floor would have dropped
> were already gone for a better reason.
>
> Three defects fixed, all found by screenshotting the app and comparing it with
> `gp cheapest` on the same database:
>
> * **GFP-271** — `bill.py` passed `min_confidence=None` at all three ranking
>   call sites, and `_eligible` skipped its check entirely for a client with no
>   preferences. A 0.3 guess outranked a 1.0 measurement.
> * **GFP-274** — the strip rendered the kind of the food a deal *matched to*,
>   not of the deal. A tin of beans was GIANT's cheapest pork.
> * **GFP-279** — the root cause of both: `matching._EXCLUDE` and
>   `protein_kind.DISQUALIFIERS` were two hand-maintained vocabularies for "not
>   a cut of meat" and had drifted. `_EXCLUDE` had `broth` but not `stock`.
>   There is now one vocabulary, and `match_deals` **retracts** matches the
>   rules no longer allow — it previously only ever inserted, so tightening a
>   rule fixed the future and left the past as wrong as it was. 49 retracted.
>
> The cheapest-protein strip, before and after:
>
> ```
> Lidl    was: beef  $0.0078  beef cooking stock 3G Protein, 32 oz
>         now: chicken $0.0084  boneless skinless chicken breast, family pack
> GIANT   was: pork  $0.0310  Hanover Brown Sugar & Bacon Baked Beans, 16 oz
>         now: beef  $0.0356  Bubba Burger Grass-fed 1/4 lb Beef Patties
> ```
>
> **Not re-measured:** the per-source table and per-market splits below. Those
> need a fresh pass and are still pre-fix figures — treat their totals as
> indicative and their *shapes* as sound.

**Measured 2026-08-12 from the live database.** Refresh this after any scrape or
enrichment change — the numbers move and stale ones are worse than none.

The only number that matters is **usable rows**: rows that reach a
`cost_per_gram_protein` figure. A priced row without a size or a protein figure
cannot be ranked and does not count, however many of them there are.

```
10,214 priced rows  ->  2,883 usable   (28.2%)
         ...of which  ->  2,344 high-confidence (81.3% of usable)
24 registered feeds -> 22 produce a row -> 20 produce a usable one
```

Previous measurement, 2026-08-11: 5,360 priced → 1,377 usable (25.7%). The gain
is five sources: Trader Joe's (+870), Lidl's catalogue (+269), **Walmart
(+217)**, Sprouts (+112) and ALDI (+15) — plus **Publix (+11)**, which is small
but is the first time Publix has contributed anything at all.

**Walmart and Publix were unreachable until 2026-08-12** (GFP-270). Both are
now read through Parse.bot, a third party that re-exposes a site's own internal
API. That is a real dependency and a metered one — see the note under the
per-source table.

Usable rows have more than doubled since 2026-08-11 (1,377 → 2,883). The
high-confidence share is 81.3%, and **the share below 0.9 has grown — 16.7% →
18.7%** as Walmart, Lidl and ALDI added rows whose protein comes from name
matching rather than a published label. **The totals improved and the top of the
ranking got worse** — see "The quality floor" below, which is the most important
thing on this page today.

---

## Per market

### Greensboro NC (27401)

```
8,468 priced rows  ->  2,315 usable   (27.3%)
11 stores with data  ->    10 contributing
```

| contributing | data but **0 usable** |
| --- | --- |
| Trader Joe's, Harris Teeter, Lidl, **Walmart (217)**, Whole Foods, Sprouts, Food Lion, ALDI (15), **Publix (11)**, Lowes Foods (1) | **Target only** |

Trader Joe's is now the **largest single contributor in the project** at 870
usable rows — more than Harris Teeter, which held that place since 2026-08-02.

### Philadelphia PA (19103)

```
1,146 priced rows  ->    99 usable   ( 8.6%)
5 stores with data  ->    2 contributing
```

| contributing | data but **0 usable** |
| --- | --- |
| GIANT (97, PRISM catalogue), ACME (2) | H Mart, Wegmans (ad), Weis |

**Unchanged since 2026-08-11, and still effectively one store: GIANT.** Every
source added since has been a Greensboro one. This is now the single largest
imbalance in the project.

### Chapel Hill NC (27514) — not a target market

```
600 priced rows  ->  469 usable   (78.2%)
Wegmans only
```

Still the highest-yielding ZIP in the database, and still one nobody asked for.
See "What is left" below — item 1 has not moved.

---

## Per source

`conf ≥ 0.9` counts rows whose protein comes from a retailer's own label or a
confident name match against a density another retailer published, rather than
from an on-pack claim the engine had to
interpret. It is the number to trust.

| store | source | priced | usable | yield | conf ≥ 0.9 |
| --- | --- | --- | --- | --- | --- |
| traderjoes | `traderjoes` GraphQL | 1,696 | **870** | 51.3% | 865 |
| harristeeter | `kroger-api` | 994 | **541** | 54.4% | 490 |
| wegmans | `wegmans-api` | 600 | **469** | 78.2% | 459 |
| lidl | `lidl-catalogue` | 1,974 | **269** | 13.6% | **101** |
| **walmart** | **`parsebot`** | **430** | **217** | **50.5%** | **135** |
| wholefoods | storefront | 244 | **137** | 56.1% | 105 |
| sprouts | `instacart-storefront` | 155 | **112** | 72.3% | 111 |
| giant | `prism` | 398 | **97** | 24.4% | 28 |
| foodlion | `prism` | 397 | **97** | 24.4% | 36 |
| aldi | `instacart-storefront` | 161 | **15** | 9.3% | 6 |
| harristeeter | flipp ad | 342 | 13 | 3.8% | 0 |
| harristeeter | csv-import (historical) | 271 | 12 | 4.4% | 0 |
| **publix** | **`parsebot`** | **12** | **11** | **91.7%** | **8** |
| foodlion | csv-import (historical) | 218 | 8 | 3.7% | 0 |
| lidl | flipp ad | 152 | 4 | 2.6% | 0 |
| publix | flipp ad | 236 | 4 | 1.7% | 0 |
| foodlion | flipp ad | 284 | 3 | 1.1% | 0 |
| acme | flipp ad | 245 | 2 | 0.8% | 0 |
| lowesfoods | flipp ad | 314 | 1 | 0.3% | 0 |
| aldi | flipp ad | 168 | 1 | 0.6% | 0 |
| target, weis, hmart, wegmans, sprouts | flipp ad | 1,091 | **0** | 0.0% | 0 |
| **TOTAL** | | **10,214** | **2,883** | **28.2%** | **2,344** |

**Eleven catalogues carry 98.3% of the value. Twelve weekly ads carry 1.0%.**

**Walmart and Publix arrive through a third party (Parse.bot), not from the
retailer.** Three things follow that no other source here carries: an outage
takes out *both* stores at once, every call is metered so neither walks a
catalogue (both work from a bounded 10-keyword list, reported in
`stats['queries']` — "Walmart has 430 rows" means "we asked 10 questions"), and
the generated API ids are pinned and will rot. Publix's 12 rows are not a bug:
most of its catalogue comes back priceless, and the fresh-meat rows that do
carry a figure quote it **per pound** (see the ‡ marker).

Lidl's *weekly ad* went from 0 usable to 4 the moment Lidl's *catalogue* landed,
because `sourcelink` could finally borrow a size for it. That is the survey's
"count catalogues, not chains" claim demonstrated in a single scrape.

---

## The quality floor — READ THIS BEFORE ADDING ANOTHER SOURCE

Measured 2026-08-12, immediately after the Lidl catalogue landed. The optimiser
was asked for one client's 180 g/day at lowest cost and returned:

```
lidl   $1.41   180.0 g   beef cooking stock 3G Protein, 32 oz
```

**One line. Stock. That is the whole recommended day.**

### Why it happens

`savings.cost_per_gram_protein` correctly marks that row **confidence 0.3** — it
read "3G Protein" out of the item name, has no servings-per-container, and says
so. But `bill.py` calls `rank_by_cost_per_gram_protein(..., min_confidence=None)`
in all three places, so a 0.3 guess competes head-to-head with a 1.0 measured
density, and cheapest wins.

**And the project already knows stock is not protein.**
`protein_kind.DISQUALIFIERS` has held `\b(broth|stock|bouillon|consomm\w*)\b`
and `gravy` all along; `protein_kind.classify("beef cooking stock…")` returns
`other`, which is exactly why the cheapest-meat strip does *not* show it. The
bill never asks. `bill._eligible` reads:

```python
if not applied_categories:
    return ranked          # unconstrained
```

So the disqualifier list is consulted only through `nutrition.food_ids_in`, and
only when a client **has** preferences. **A client with no preferences set is
the least protected client in the product** — and that is the default state of
every newly added client. Fiona, who has preferences, gets bacon. Ethan, who has
none, gets stock.

### This is not new, and it is not Lidl's fault

Measured against the 2026-08-11 snapshot, before any of today's sources:

| | 2026-08-11 | 2026-08-12 |
| --- | --- | --- |
| usable rows | 1,399 | 2,635 |
| at conf ≥ 0.9 | 1,122 (**80.2%**) | 2,195 (**83.3%**) |
| of the top 20, below 0.9 | **11** | **12** |

The distribution got *better*. The top of the ranking was already half
low-confidence a day ago. **What changed is what those rows are.** Yesterday's
sub-0.9 leaders were chicken leg quarters and pork butt — a wrong density on a
food that is genuinely protein, so the plan was mispriced but still edible.
Today's are cooking stock, gravy and pork-and-beans, where the claim is real and
the density is meaningless.

So the defect is not "Lidl is a bad source". It is that **nothing has ever
filtered on confidence**, and the project got away with it until a source
arrived whose cheap end is not food anyone eats for protein.

### Why it concentrates at the top

**The error is one-directional.** A mis-derived density can only make an item
look *cheaper* per gram, never dearer — inventing protein deflates the price per
gram, and there is no matching error that inflates it. So low-confidence rows do
not scatter through the ranking; they **colonise the top of it**, which is the
only part the optimiser ever reads.

The spread across all 2,883 usable rows:

| confidence | rows | what it means |
| --- | --- | --- |
| 1.0 | 1,847 | retailer's own panel, or a stated density |
| 0.9 | 497 | confident name match to a retailer-published density |
| 0.6 | 295 | label claim, single serving assumed |
| 0.3 | 244 | label claim, multi-serve, servings unknown |

539 rows sit below 0.9 — 18.7% of the total, up from 16.7% before Walmart. Their share of the ranking rises
monotonically towards the cheap end, which is the one-directional error made
visible:

| slice of the ranking | below 0.9 |
| --- | --- |
| top 20 | **70%** |
| top 50 | **50%** |
| top 100 | 37% |
| all 2,883 | 18.7% |

The optimiser reads the top. That is where the bad rows are.

### What the same query returns with a 0.9 floor

```
$0.0169/g  harristeeter  Chicken Drumsticks Value Pack, 1 lb
$0.0202/g  wholefoods    365 Boneless Skinless Chicken Thighs
$0.0206/g  traderjoes    Whole Green Lentils, 16 oz
$0.0214/g  harristeeter  Bone-In Assorted Pork Loin Chops
```

Every one of those is a real food a person could eat for a day. **The data to
fix this is already in the rows; nothing is filtering on it.**

GFP-197 already wrote the rule this needs — *"any 'grams unknown' tier must be
unsortable against real $/g rows and shown separately"*. It was written about
Flipp's missing denominations and applies unchanged here.

---

## What each store can provide

Capability, not row count — what the source is *able* to publish. This is the
table to read before proposing a new scraper, because the failure mode is not
"we cannot reach them", it is "we reach them and they publish no size".

### Catalogues: price + size + protein

| store | route / credential | price | size | protein | usable | the catch |
| --- | --- | --- | --- | --- | --- | --- |
| Trader Joe's | Magento GraphQL, open, introspectable | store-scoped ✓ | ✓ | ✓ label | **870** | ZIP → store is unresolvable (the locator publishes no postal codes), so store 750 is pinned; 924 of 2,454 products are not carried there |
| Harris Teeter | Kroger API, OAuth2 client credentials | per-store ✓ | ✓ | ✓ label (`PRO-`) | **541** | 10,000 calls/day *per credential* is the monetization ceiling; terms unresolved (GFP-119) |
| Wegmans | own public JSON API, no auth | per-store ✓ | ✓ | ✓ label + UPC | **469** | highest yield in the project, and it is scraped at **27514** — a ZIP nobody asked for. Capped at 600 of 1,535 known protein SKUs |
| Whole Foods | storefront, hand-minted `wfm_store_d8` | per-ZIP ✓ | ✓ | ✓ label | **137** | one cookie pins one store; the customer mints it per ZIP |
| Sprouts | Instacart Storefront Pro, guest session | ✓ *but* HTML-throttled | ✓ | ✓ (pinned hash, not auto-discoverable) | **112** | 11,097 density-computable products in the catalogue. Price needs the product page, which hard-403s after ~2,300 fetches. robots.txt/Instacart ToS is the real blocker |

### Catalogues missing the nutrition half

| store | route | price | size | protein | usable | the catch |
| --- | --- | --- | --- | --- | --- | --- |
| Food Lion | PRISM `ld+json` | ✓ (not per-ZIP) | ✓ from slug | published, **written to `notes` and never read** | **97** | the 24% comes from name-matching (`cut_keyword`, `category_fallback`) against densities other retailers published — **not** from Food Lion's own figure, which is sitting unread in `notes` |
| GIANT | PRISM, same code | ✓ (not per-ZIP) | ✓ from slug | same | **97** | Philadelphia's only real contributor |
| ALDI | Instacart platform (`aldi-storefront`) | ✓ | ✓ | **none — 0 panels across all 15,256 products** | **15 of 161** (9.3%), 6 at conf ≥ 0.9 | not our bug: the pinned hash works and returns a well-formed empty envelope. Price + size only, so every usable row is a name match. Measured on a bounded 200-product slice; 39 of 200 pages carried no JSON-LD at all |
| **Walmart** | Parse.bot (`walmart`) | **per-ZIP ✓ — verified**, plus a retailer-stated `$/lb` | ✓ on nearly every row | **prose only** — "each serving offers 25 grams of lean protein", no serving size; the real panel is an image URL | **217 of 430** (50.5%), 135 at conf ≥ 0.9 | The best price+size source in the project: `specifications` states `Sales unit: Weight` and `Net content statement: Random Weight`, which is the GFP-98 denomination in the retailer's own words rather than inferred. No UPC. Third-party dependency, metered |
| **Publix** | Parse.bot (`publix-catalog`) | **per-ZIP ✓ — resolved 27401 → store 1658 itself** | on the details endpoint (`sizeDescription`) | none on search; `ingredients` on details | **11 of 12** (91.7%), 8 at conf ≥ 0.9 | Tiny, because most of the catalogue returns **priceless**. Fresh meat quotes a **rate** (`"$5.39/lb"`) with no package total, so those rows carry ‡ — see GFP-270 |
| Lidl | sitemap + `ld+json` (`lidl-catalogue`) | ✓ | ~ from description prose | claim in the nutrition image's **alt text**, no serving size anywhere → **no density is computable from the source** | **269**, but only **101** at conf ≥ 0.9 | 1,974 products — double the ~950 the module estimated. The module correctly writes no food facts; the 269 come from name-matching against other retailers' densities (148 `cut_keyword`, 108 `category_fallback`) and from claims folded into `item_name`. **Lowest quality per row of any catalogue — see "The quality floor"** |

### Weekly ads (11 Flipp banners): per-ZIP price, never a size or a protein figure

Harris Teeter, Food Lion, Lowes Foods, Publix, Target, ALDI, Lidl, Sprouts
(27401); ACME, Weis, H Mart, Wegmans, GIANT (19103).

The three ads that yield anything at all do so only because the same banner has
a catalogue for `sourcelink` to borrow a size from. The eight without one yield
exactly zero, and always will.

### Ruled out — do not re-probe

| chain | verdict |
| --- | --- |
| Walmart, Publix (catalogue), ShopRite, Save-A-Lot | challenge page or 403 to every automated client |
| **Costco** | **NOT a bot problem — re-checked live 2026-08-12.** `costcobusinessdelivery.com` returns 200 to plain httpx with the most permissive robots.txt of anything surveyed (only account/checkout/error paths disallowed), publishes a **1,407-product sitemap**, and **5 of 8 sampled product pages carry structured nutrition** — `nutritionInformation` → `numberOfServings`, `servingSize`, `nutrientDetails[{nutrient, quantityContained}]` — plus ingredients and allergens. `www.costco.com` also answers 200; only `search.costco.com` is 403. What rules it out is **price and assortment**: 0 of 8 pages carried a price ("does not deliver to your area" for 27401), the catalogue is break-room stock (candy, chips, cocoa, canned lattes) rather than groceries, `metricServingSize` is null on sampled items so no density is computable, and **no UPC/GTIN appears anywhere** — which also kills the one salvage idea, using it as a nutrition reference table, since there is nothing to join on |
| Food Lion's own site | DataDome — beats httpx, headless Playwright *and* headed Playwright |
| Lowes Foods, The Fresh Market, Sprouts' own site | publish no product catalogue at all |

Full evidence in `spikes/GFP-197-source-survey.md`.

---

## What is left, ranked by return

| # | work | est. usable rows | market |
| --- | --- | --- | --- |
| 0 | **A confidence floor in `bill.py`** | **−539, and the product starts working** | all |
| 1 | **Scrape Wegmans at a Philadelphia ZIP** | **+470** | Philadelphia |
| 2 | Raise the Wegmans SKU cap, 600 → 1,535 | +700 | both, per ZIP |
| 3 | PRISM protein fix (Food Lion + GIANT) | +400 | both |
| 4 | Raise the Sprouts price bound | +1,000 (throttle-bound) | Greensboro |
| 5 | Cross-store size borrowing | +113 | all |

### 0. The confidence floor — the only item here that *removes* rows

Every other line adds coverage. This one takes 539 rows out of the ranking and
is still first, because coverage that recommends cooking stock is negative
value: it is worse than having no row at all, since a nutritionist cannot tell
by looking that the number was guessed.

It is also the cheapest item here — everything needed already exists.
`rank_by_cost_per_gram_protein` takes a `min_confidence` argument today and
`bill.py` passes `None` in three places; `protein_kind` already disqualifies
stock, broth and gravy and `_eligible` already skips that check for
preference-less clients. Two edits, no new data, no new source.

A second, smaller defect found the same way: `protein_kind` classifies
**"Hanover Brown Sugar & Bacon Baked Beans, 16 oz" as `pork`** — visible today
as the cheapest "pork" in the app's own cheapest-meat strip — and "pork and
beans" likewise. `DISQUALIFIERS` catches stock and gravy but nothing marks a
bean product as not-meat when the name carries "bacon" or "pork".

Note which sources this does *not* punish: Sprouts contributes 111 of its 112
rows at ≥ 0.9, Trader Joe's 865 of 870. A floor costs almost nothing on sources
that publish real panels. It costs Lidl 168 of its 269 — which is the point.

### 1. Wegmans at a Philadelphia ZIP — still minutes of work, still not done

Wegmans operates 16 stores in Pennsylvania. The scraper already resolves a store
from a ZIP through Wegmans' own store API:

```
gplan scrape wegmans-api --zip 19406      # King of Prussia
```

At the measured yield that is roughly **+470 usable rows**, taking Philadelphia
from 99 to about 570 and from two contributing stores to three — 8.6% to
roughly 35%. **This is still the single largest available gain and it is still a
parameter.**

### 2. The Wegmans SKU cap

`DEFAULT_MAX_PRODUCTS = 600`, and discovery found **1,535** protein SKUs already
shipped as package data (`grocery_planner/data/wegmans_skus.json`). At 78.2%
that is ~+700 rows per ZIP scraped, for about 6 minutes of wall clock per store.

### 3. PRISM protein

Food Lion and GIANT sit at 24.4% while their catalogue pages carry
`Protein 18 g` in the page state. `prism.py` writes that figure into the row's
`notes` and **nothing ever reads it**. The fix is the one already proven on
Wegmans and Trader Joe's: write a `foods` row, its density, and a
`deal_food_match` at confidence 1.0. Worth roughly +400.

### 4. The Sprouts price bound

The catalogue holds 11,097 products with a computable protein density; 155 are
priced. Price requires the product page, and that path returns a hard 403 after
~2,300 fetches — so this is bounded by a rate policy, not by access. Raising it
means more, slower passes under `retry.Paced`, not a code change. Gate on
GFP-119 first: robots.txt disallows `*` and defers to **Instacart's** terms.

### 5. Cross-store size borrowing

`sourcelink` (GFP-248) joins a promo row to a catalogue row **within one store**.
Food size is a property of the product, not the shop. Measured: **113 of 288
(39%)** price-only protein rows could borrow a size from a catalogue already in
the database. Cross-store borrows must rank below same-store ones, and the
`MAX_PRICE_RATIO` veto and contradiction guard both still apply.

---

## The Parse.bot budget, because it is money

**Measured from the vendor's own usage export, 2026-08-12** — not inferred from
the pricing page, which led to a wrong conclusion once already.

Free tier: **200 credits/month**, 5 req/min, plus a separate **100 requests/day**
cap that expires independently. The export shows 207 credits consumed, **all of
it API calls** — no build or dispatch charge appears at all, so standing up the
two private APIs was not what emptied the tier.

**The per-call cost varies 10x by endpoint, and that is the whole story:**

| api | endpoint | calls | credits | per call |
| --- | --- | --- | --- | --- |
| publix.com | `search_products_by_zip` | 15 | **150** | **10.0** |
| walmart.com | `search_grocery_products` | 15 | 45 | 3.0 |
| walmart.com | `get_product_details` | 2 | 4 | 2.0 |
| publix.com | `search_products` | 2 | 2 | 1.0 |

An average of "about 2 credits a call" hides that completely. Publix's ZIP
search costs **more than three times** Walmart's, and it is the one endpoint
that emptied the month.

### Cost per usable row — the number that should drive the decision

| store | credits per scrape | usable rows | **credits per usable row** |
| --- | --- | --- | --- |
| Walmart | 30 (10 × 3) | **217** | **0.14** |
| Publix | 100 (10 × 10) | **11** | **9.1** |

**Publix costs 65x more per row than Walmart.** On a weekly cadence it is
~433 credits/month against Walmart's ~130 — 77% of the bill for 5% of the rows,
and its ceiling is low anyway because most of its catalogue returns priceless.

| plan | weekly Walmart+Publix | weekly Walmart only |
| --- | --- | --- |
| Free (200) | ~563 — **far over** | ~130 — **fits, 70 spare** |
| Hobby (1,000, $30) | ~563 — fits | ~130 — fits |

Dropping Publix from the schedule keeps the project on the **free tier** and
still leaves room for the Target verification. Keeping it means $30/month for
11 rows.

Every scrape reports `parsebot_calls` plus the vendor's remaining credit and
daily counters in its stats, so this is visible before it becomes a 402.

### DECIDED 2026-08-14 (GFP-287): Publix is unscheduled, Walmart stays

The user's position was "I'm not paying for Parse.bot". The table above says
that does not require dropping Parse.bot — it requires **dropping Publix**:

```
weekly cadence          before            after
  walmart               ~130/month        ~130/month
  publix-catalog        ~433/month        0          <- unscheduled
  ------------------------------------------------
  total                 ~563  OVER 200    ~130  fits the FREE tier
```

The $30/month Hobby tier was only ever being spent on **eleven rows**.

**What changed.** The live `publix-catalog interval 7d` schedule is deleted, and
`scrapers.publix.SCHEDULABLE = False` now stops it being re-added.
`service.schedulable_scrapers()` is a third question alongside *registered* and
*ready* — it asks whether a source should run **unattended and repeatedly**,
which is a question about money rather than capability.

The scraper is **kept**. `gplan scrape publix-catalog` still runs it: spending
100 credits once, knowingly, is a decision someone is making with their eyes
open. What is refused is the cadence that spends money while nobody is looking.

**Could Publix's own site replace the metered feed? No — GFP-288, asked and
answered 2026-08-14.** Worth recording because two of the four pieces work, and
the next person to notice that will retrace this.

| route | result |
| --- | --- |
| `services.publix.com/…/productitems?Id=<uuid>&StoreNbr=` | **200, no auth, real prices** |
| `sitemap_products1..7.xml` | **200, ~70k products, published for crawlers** |
| `/?setstorenumber=1658` | **200**, sets a `Store` cookie; prices then appear in page HTML |
| `/search/api/search/storeproductssavings` | 403, blanket path deny |
| `/pd/<slug>/<baseProductId>` | Akamai `bm-verify` interstitial |
| `/search?query=…` | 200, but the query is ignored without a store |

The working half is genuinely attractive: `productitems` returns **package
prices**, not the per-pound rates that force the ‡ marker, and the sitemaps would
let protein candidates be filtered offline with no search endpoint at all — 595
chicken URLs in sitemap 1 alone.

**They cannot be joined, and not for the reason it first appears.** `Id` is not a
product identifier — it identifies a curated **carousel**. One call returns five
products sharing that same `id`, each with its own `itemCode` and
`baseProductId`. There is no product uuid to go hunting for.

That is what settles it. Even with every carousel enumerated, the reachable set
is whatever Publix is *promoting* — the seed carousel returns a 50-piece wing
platter, a charcuterie board and a shrimp platter, $24–70 catering items. A
cost-per-gram-protein ranking built on promoted stock is worse than one built on
nothing: as the quality-floor section below puts it, a bad input does not scatter
through a ranking, it colonises the top of it. The sitemap has the catalogue and
no prices; this endpoint has prices and no catalogue.

Two things follow. The decision on this page is **unchanged and better
supported**: unschedule the metered feed, because the free replacement does not
exist yet. And **GFP-197's original verdict stands** — "the store locator returns
no results to an automated client" was right, and the cause is the bot-detection
layer rather than the locator itself.

**And walmart.io is not a replacement for Parse.bot Walmart.** They are
complements — Parse.bot gives a *store-scoped price* (verified: same query at
27401 and 94110 priced 3 of 43 items differently), the official API gives *UPC*,
which is the route to real nutrition. The API is also blocked (GFP-269) and
national-priced only, so swapping today would trade per-ZIP pricing — the
defining feature of v2 — for nothing.

## Leads filed, validated, not built

Both would change a market rather than pad a total.

* **GFP-268 — Target.** Still the only 27401 store with data contributing
  **zero**. Two routes, and the one that matters is now cheap:
  * `redsky.target.com` — has the price, but is `User-agent: * / Disallow: /`.
  * `www.target.com` PDP — **permitted and free**, published product sitemap,
    and verified live: typed numeric protein, `serving_size` + unit as separate
    numeric fields, `ingredients`, and a **UPC**. But **no price at all**.
  * **Parse.bot marketplace has a `target.com API`** (6 endpoints, no auth,
    verified 2026-08-12) whose `search_products` takes a `zip` "for local
    pricing". Adopting a marketplace API is **free** — only calls cost credits.
    *Unverified*: we ran out of credits before testing whether it returns a
    price for 27401. That single check is ~5 calls, and it is the highest-value
    thing to spend credits on when they reset.

    ```
    GET /scraper/9935e57e-18c2-4c7c-aebe-bc311e983dc8/search_products
        ?keyword=chicken+breast&zip=27401&count=5
    ```

* **GFP-260 — Albertsons catalogue**, six banners and a UPC on every product.
  ACME is a Philadelphia store contributing 2 accidental rows today.

---

## The lesson this page exists to hold

Between 2026-08-09 and 2026-08-12 the registry went from 7 scrapers to 24.
Usable rows went 908 → 1,377 → 2,883. Of that 1,975-row gain, **1,966 came from
catalogues** (Wegmans, Sprouts, Trader Joe's, Lidl, ALDI, Walmart, Publix and the
PRISM pair) and **9 came from twelve weekly ads** — five of those nine only
because a catalogue arrived for the same chain.

**Count catalogues, not chains.** A weekly ad is the per-ZIP price half of a
pair; it can never carry size or protein, and on its own it yields nothing.

Two corollaries earned since:

* **A catalogue is not automatically a source.** ALDI publishes 15,256 products
  and zero nutrition panels; Lidl publishes a protein claim with no serving size,
  so no density exists to compute. Both are reachable, neither is rankable.
* **The bound is often a rate policy, not an access one.** Sprouts is not
  blocked — it is paced. That is a scheduling problem, and it belongs to
  `retry.Paced` rather than to a new scraper.
* **And sometimes the bound is our own pacer, not theirs.** Measured 2026-08-12:
  an ALDI run took three 403s in its first two seconds, hit the 30 s ceiling,
  and then crawled for 100 minutes — projecting to ~10 hours for 1,200 pages.
  A fresh run 30 minutes later did 200 pages in **137 seconds with zero 403s**.
  ALDI's wall is a *burst* limit at startup; `PRODUCT_PAGE_BUDGET` reads it as a
  sustained verdict, and recovery from the ceiling needs **475 clean requests
  and 62 minutes of pacing** — so within any bounded run the ceiling is a
  one-way door. Before blaming a source for being slow, check whether the pacer
  put it there.
* **A usable row is not automatically a trustworthy one, and an improving
  average can hide a worsening product.** The high-confidence share rose to
  83.3% on the same day the optimiser started recommending beef stock. Averages
  describe the whole ranking; the optimiser only ever reads the top of it.
  Count rows at confidence ≥ 0.9, and check what the cheapest ten actually are.
