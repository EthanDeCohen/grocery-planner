# GFP-197: source survey, Greensboro and Philadelphia

**Status:** complete, 2026-08-09 → 2026-08-11. 37 chains across two markets.
**Purpose:** record which sources carry usable data and which do not, so the
survey is not repeated.

A source is **usable** only if it supplies price *and* size *and* protein (or a
UPC that resolves to protein). Price alone cannot be ranked, which is the single
finding that shaped this whole survey.

---

## What works

| source | mechanism | yield |
| --- | --- | --- |
| Kroger API (Harris Teeter) | official documented API, credentialled | 54.7% |
| Wegmans API | public JSON API, per-store pricing, UPC, nutrition | 78.2% |
| Whole Foods | storefront, human-minted session (GFP-70) | 61.5% |
| PRISM (Food Lion, GIANT) | server-rendered `ld+json` | 24.4% |

All four publish structured product data deliberately. Nothing else surveyed
reached double digits.

---

## What does not, and why

### Technically unreachable

The site returns a challenge or refuses automated clients. Not worth further
effort without a change on their side.

| chain | observed |
| --- | --- |
| Walmart | product URLs redirect to a challenge page |
| Publix | price endpoint returns 403; store locator returns no results to an automated client |
| Albertsons (ACME, Vons, Safeway, Shaw's, Star Market) | browse pages challenged; ~83% of product pages render client-side, deterministic across retries |
| ShopRite / Wakefern | 403 at the root |
| Costco | 403 on all product pages |
| Save-A-Lot | challenge page returned behind a 200 status |

### Publishes no catalogue

Not restricted — there is simply nothing to read.

| chain | sitemap contents |
| --- | --- |
| Sprouts | 442 blog posts, 72 pages, 506 store locations. Zero products. |
| Lowes Foods | 384 URLs, zero products |
| The Fresh Market | 1,135 URLs, two "product" URLs (a gift card and a recalls page) |
| Trader Joe's | 403 at the root |

**This is the failure mode that surprises.** Three of the most permissive sites
surveyed publish no product data at all.

### Reachable but empty of what matters

**Lidl.** 4,044 products in the sitemap, product pages return `ld+json` with a
real price. Sampled 12 across the catalogue:

```
$12.99  Ocean Sea frozen wild caught pink salmon   size=None  protein=None  gtin=None
$1.95   real bacon bits                            size=None  protein=None  gtin=None
$5.99   frozen Buffalo style boneless chicken      size=None  protein=None  gtin=None
$4.09   Butcher's Specialty boneless chicken       size=None  protein=None  gtin=None
```

12 of 12 carried no size, no protein and no UPC. Zero occurrences of "protein"
anywhere on the page. Building it would add ~877 rows that cannot be ranked.

### Access restricted by site policy

| chain | note |
| --- | --- |
| Aldi | Automated access restricted; storefront and terms are operated by Instacart, not Aldi. Ad data is available through Flipp and is already integrated. |

Aldi's advertised assortment is 149 priced rows of which **11 are
protein-relevant** (4.5% density, near the bottom of fourteen merchants
measured). Aldi is a limited-assortment discounter carrying roughly 1,400 SKUs
against a Wegmans' 40,000+, so the ceiling is low independent of access.

### Not probed

Recorded honestly rather than counted as dead: BJ's Wholesale, Redner's Markets,
The Fresh Grocer, Amazon Fresh, First Oriental Market, Super G Mart, Bestway
Grocery, Deep Roots Market. None publishes a Flipp ad and no route was
identified.

Out of scope: H-E-B (Texas only), FreshDirect (NYC only, separate stack from
PRISM despite shared ownership — GFP-251), Stop & Shop and Hannaford (PRISM
banners outside the target markets — GFP-250), Giant Food of Landover (PRISM,
outside the markets).

---

## Which chains publish a Flipp weekly ad

The cheap route, and the only one that scaled. Merchant strings are exactly as
Flipp writes them — `Wegman's` carries an apostrophe, `Lowes Foods` does not,
and a near-miss silently matches nothing.

**Philadelphia 19103** — 80 flyers, 27 weekly:
ALDI, Acme Markets, Associated Supermarkets, Boscov's, CVS, Dollar General,
Dunham's, Food Lion, GameStop, Giant Food, Giant Food Stores, Grocery Outlet,
H Mart, Hobby Lobby, Home Depot, Lidl, Lowe's, Michaels, Ocean State Job Lot,
Sprouts, Supremo Foods, Target, ULTA, Walgreens, Wegman's, Weis Markets,
Wild Fork.

**Greensboro 27401** — 63 flyers, 20 weekly:
ALDI, CVS, Dollar General, Dunham's, Earth Fare, Food Lion, GameStop, Harris
Teeter, Hobby Lobby, Home Depot, Lidl, Lowe's, Lowes Foods, Michaels, Publix,
Sprouts, Target, ULTA, Walgreens, Wegman's.

Not on Flipp in either market: ShopRite, Walmart, Costco, BJ's, Save-A-Lot,
Redner's, The Fresh Grocer, Trader Joe's, Amazon Fresh, First Oriental Market,
The Fresh Market, Super G Mart, Bestway, Deep Roots.

---

## The measurement that reframed the epic

Every candidate's live weekly ad, against GFP-197's three quality columns.

| merchant | rows | priced | protein-matchable | machine-readable SIZE |
| --- | --- | --- | --- | --- |
| Publix | 446 | 54.7% | 8.1% | **0.0%** |
| Lowes Foods | 358 | 89.9% | 7.8% | 5.3% |
| Food Lion | 320 | 93.4% | 7.2% | **0.0%** |
| Harris Teeter | 433 | 81.8% | 6.9% | 5.1% |
| Weis Markets | 269 | 75.1% | 4.8% | 2.6% |
| H Mart | 215 | 73.0% | 4.7% | 0.5% |
| ACME Markets | 307 | 79.5% | 4.6% | 4.2% |
| ALDI | 154 | 96.1% | 4.5% | **0.0%** |
| Lidl | 164 | 98.8% | 3.7% | **0.0%** |
| Earth Fare | 177 | 82.5% | 3.4% | 2.3% |
| Giant Food Stores | 419 | 74.2% | 3.3% | 0.2% |
| Sprouts | 154 | 70.8% | 3.2% | 1.3% |
| Wegman's (ad) | 207 | 68.1% | 1.9% | 0.5% |
| Target | 371 | 88.1% | 1.6% | **0.0%** |

**Every Flipp ad is thin, and thin in the same way.** GFP-197's original note —
sizes on only 4.9% of items — is confirmed across fourteen merchants. It is a
Flipp property, not a Food Lion one.

### What adding ten of them bought

```
TEN NEW BANNERS:     2,066 priced rows ->     3 usable   (0.1%)
ONE NEW CATALOGUE:     600 priced rows ->   469 usable  (78.2%)
```

The registry went 7 → 18 scrapers. Usable rows went 908 → 1,377, and **469 of
that gain came from one source**.

ACME's 2 and Lowes Foods' 1 are accidents — a size that happened to sit in an
item name. Food Lion's ad yields 8 and Harris Teeter's 13 *only because those
stores have a catalogue* for `sourcelink` to borrow a size from. The eight
banners with no catalogue yield exactly zero.

**Count catalogues, not chains.** Keep the ad feeds — they cost nothing, they
are correct per-ZIP price sources, and they convert the moment a catalogue
exists for the same chain. But adding more of them is not progress.

---

## Enrichment: unproven, not dead

Distinct from acquisition. These supply the missing size and protein for rows
already held.

| route | status |
| --- | --- |
| Cross-store catalogue join | **Measured: 113 of 288 (39%)** price-only protein rows could borrow a size from a catalogue already in the database. `sourcelink` is same-store only; food size is a property of the product, not the shop. Untried. |
| USDA FoodData Central Branded | Untried. ~400k branded products, public domain, keyed by UPC and brand. FoodData Central is already ingested (GFP-24); the Branded dataset is separate and far larger. |
| Open Food Facts | Brand-batched queries work — 1,432 Aldi private-label products returned with protein per 100g. Per-item free-text search hits the rate limit. A real design batches by brand and gates on match confidence. |

**Cautions carried from GFP-248.** A cross-store borrow is a weaker claim than a
same-store one and must rank below it. The `MAX_PRICE_RATIO` veto (a 4x price
gap means the two numbers are denominated differently) and the contradiction
guard (boneless vs bone-in) both still apply.

**The denomination problem sits on top of all of it.** Flipp never states
`sold_by` — it is NULL on every row. `$2.00 BONELESS RIBEYE STEAK` is a
per-pound price with the denomination stripped. Ranking such rows by raw price
would put per-pound meat above per-package items and make the cheapest-looking
rows the most misleading. Any "grams unknown" tier must be unsortable against
real $/g rows and shown separately.

Of 288 price-only human-protein rows: 5% carry a size in the name, 0% carry a
protein claim, and **66% already match a food**. What is missing is almost never
*what it is* — it is *what the price buys*.

---

## Method notes

Each of these cost real time during the survey and each is repeatable.

1. **Do not generalise from one path.** A 403 on a homepage says nothing about
   `/groceries/**`, which turned out to be open and to hold the second-best
   source in the product.
2. **A payload sample is the only viability test.** One product page is not
   enough: a strided sample of 12 Albertsons pages returned 2 usable, and three
   retries each of the failures returned identical byte counts.
3. **Do not hand-write geographic footprints.** A prefix list claimed Food Lion
   served all of Kentucky; it is in one metro (Bowling Green). Ask the source —
   Flipp answers exactly and for free.
4. **Read the endpoint list, not the marketing page.** Feature copy describes
   what an end user sees, not what an integrator receives.
5. **Verify the test before trusting the result.** An early enrichment test used
   a tag-filter endpoint with a free-text parameter it ignores; every store
   returned the same product.

The corrective is the same each time: establish the verdict cheaply, record the
evidence, and check what the evidence says before generalising. A strided,
retried payload sample is the minimum bar for calling a chain viable *or* dead.
