# GFP-197: every dead end, and why

**Status:** complete for Greensboro and Philadelphia, 2026-08-09 → 2026-08-11.
**Purpose:** stop these being re-litigated. Each entry names what was tested and
what came back, so a future proposal to "just try X" can be checked against
evidence rather than argued from a robots.txt.

---

## The headline finding

**`robots.txt` predicted nothing.** Of the chains probed, the four with the most
permissive robots produced *zero* product data between them, and the one that
looked most restricted on paper (PRISM) turned out to be the second-best source
in the product.

Every outcome was decided by something robots.txt does not mention: a
bot-protection vendor, a client-side render, an absent catalogue, or a payload
missing the only two fields that matter.

**Read robots.txt first — it tells you when to stop. It never tells you when to
proceed.** Only a payload sample does that.

### The only thing that correlated

Every working source publishes structured product data deliberately:

| source | how | yield |
| --- | --- | --- |
| Kroger API (Harris Teeter) | official documented API | 54.7% |
| Wegmans API | own JSON API, `Allow: /`, no disallows | 78.2% |
| Whole Foods | storefront + human-minted session (GFP-70) | 61.5% |
| PRISM (Food Lion, GIANT) | server-rendered `ld+json` | 24.4% |

Nothing else reached double digits, and most reached zero.

---

## Dead ends by failure mode

### 1. Refused by a bot-protection control

The site answers, then a vendor decides you are automated and returns a
challenge. Nothing in robots.txt says so.

| chain | control | evidence |
| --- | --- | --- |
| **Walmart** | PerimeterX | `/ip/{id}` redirects to `/blocked?url=…`; page title "Robot or human?", `px-captcha` present. `/ip/` is *not* disallowed in robots. |
| **Publix** | Akamai | `services.publix.com/search/api/search/storeproductssavings/` → **403 Access Denied** (`errors.edgesuite.net`). Cookie replay from a real browser session also 403 — `_abck` returned `~-1~`, Akamai's "not validated" state. The store locator inside a headless browser returned **"Showing 0 results"**. |
| **Albertsons** (Acme/Vons/Safeway/Shaw's/Star Market) | Imperva | `/shop/aisles/**` → 6KB "Pardon Our Interruption" with `reeseSkipExpirationCheck`. |
| **ShopRite / Wakefern** | unknown | `robots.txt` itself returns **403**. Never reached a product page. |
| **Costco** | unknown | Every product page → **403**, with clean unescaped URLs. robots.txt is largely permissive; 8,082 products in the sitemap, none readable. |
| **Save-A-Lot** | unknown | The **most permissive robots on the entire list** (`Allow: /`, one disallow for `/cgi-bin/`). All sampled pages return a challenge page *behind a 200 status* — the sneakier kind. |

### 2. Publishes no catalogue at all

Not blocked. Not restricted. There is simply nothing there.

| chain | sitemap contents |
| --- | --- |
| **Sprouts** | 442 blog posts, 72 pages, 506 store locations, FAQs. **Zero products.** A WordPress marketing site; shopping runs through Instacart. |
| **Lowes Foods** | No `robots.txt` at all (404). 384 sitemap URLs, **0 product-ish**. |
| **The Fresh Market** | Clean robots, 1,135 URLs, **2 "product" URLs** — a gift card and a product-recalls page. |
| **Trader Joe's** | 403 on everything including `robots.txt`. |

**This is the failure mode that "open on paper" hides.** Sprouts, Lowes Foods
and The Fresh Market are three of the most permissive robots files surveyed and
they publish no products between them.

### 3. Open, readable, and worthless

**Lidl.** Genuinely open — `/p/` is not disallowed, 4,044 products in a gzipped
sitemap, product pages return `ld+json` with a real price.

And that is all they return. Sampled 12 products across the catalogue:

```
$12.99  Ocean Sea frozen wild caught pink salmon   size=None  protein=None  gtin=None
$1.95   real bacon bits                            size=None  protein=None  gtin=None
$5.99   frozen Buffalo style boneless chicken      size=None  protein=None  gtin=None
$4.09   Butcher's Specialty boneless chicken       size=None  protein=None  gtin=None
```

**12 of 12 carried no size, no protein and no UPC.** Zero occurrences of the
word "protein" anywhere on the page. Building it would add ~877 rows to the same
bucket as the ten Flipp banners — which contributed 2,066 priced rows and *3*
usable ones.

Worth stating plainly because it is the cleanest lesson available: **access was
never the constraint. Size and protein were.**

### 4. Closed by policy, and by contract

**Aldi.** `https://www.aldi.us/robots.txt` — 620 lines, 26 user-agent groups.

```
### All other bots ###
User-Agent: *
Disallow: /
```

*Becoming an "allowed" bot would not help.* Every named crawler — Googlebot,
Bingbot, Applebot, DuckDuckGo, Slurp, OAI-SearchBot — carries the same blocks:

```
Disallow: /api/                         the API
Disallow: /store/item*                  product pages
Disallow: /store/*/browse_departments*  category browsing
Disallow: /rest/v8/                     the backend
```

The named groups get store locations, recipes and marketing pages. **Nobody is
permitted to crawl the catalogue.** It is not a tier we are excluded from; the
door is shut to everyone.

And the prize behind it is small regardless: Aldi's weekly ad (already
integrated via Flipp) holds **149 priced rows, 11 protein-relevant** — 4.5%
density, near the bottom of fourteen merchants surveyed. Aldi is a
limited-assortment discounter carrying roughly 1,400 SKUs against a Wegmans'
40,000+.

---

## The Instacart layer

This deserves its own section because it explains several of the entries above
and because the shape of it is not obvious until you look.

### Aldi's storefront is not Aldi's

`aldi.us` runs on **Instacart Storefront Pro**. Three independent confirmations:

1. **URL patterns in Aldi's own robots.txt** are Instacart's, not a grocer's CMS:
   ```
   /store/*/browse_departments*   /store/*/buy_it_again*
   /store/*/l/*                   /store/item*
   /rest/v8/                      ?xrs_id=   ?sisid=
   ```
2. **The sitemap path names the platform**:
   `https://www.aldi.us/sitemaps/storefront_pro/www_aldi_us/sitemap.xml`
3. **Instacart's own documentation** refers to "Storefront versus Storefront Pro
   plans".

### The terms at aldi.us/terms are Instacart's

The document Aldi's robots.txt points bots to is an Instacart agreement:

> "you may only access the Services through the interfaces that Instacart
> provides for that purpose (for example, you may not **'scrape' or 'data mine'
> the Services through automated means**…)"

> "you may not reverse engineer (**including tracking the inputs and outputs
> flowing through our system or application in order to mimic or recreate** the
> system…)"

> may not use the Services "to directly or indirectly create, train, test, or
> improve any machine learning, large language, or artificial intelligence
> models, or similar or competing product"

So the prohibition is contractual as well as conventional, and **the counterparty
is Instacart, not Aldi.** "Asking Aldi" was never the right ask; Aldi does not
control this.

### And the partnership route returns no data

The obvious next thought is: fine, get written permission. That was this
project's assumption for two days and it is **wrong**.

The Instacart Developer Platform has two endpoints:

```
POST /idp/v1/products/products_link   -> create a shopping list page
POST /idp/v1/products/recipe          -> create a recipe page
```

**Both are push-only.** You send line items *in*; Instacart returns a shareable
link where a shopper picks a store and checks out. There is no catalogue query,
no product lookup, no price read, no nutrition, no UPC.

The clinching detail from their docs: *"when product UPCs are present in line
items, Instacart searches exclusively using the provided identifiers."* **The
caller supplies the UPC. Instacart returns none.**

Access is a partner agreement, 30–40 days to production, no published per-call
pricing — the revenue model is take-rate on completed orders, storefront
subscriptions charged to retailers, and ads.

### What that adds up to

A retailer outsources its storefront. The platform then holds the catalogue,
the store locator, the pricing and the terms. A third party can *send* that
platform demand — traffic, carts, orders — and receives nothing back. The data
originates with the retailer and becomes unreachable through the retailer.

The practical consequence for this product is exact:

- **Aldi has no route.** Not scraping, and not partnership, because the
  partnership yields no data.
- **Sprouts likewise** — its own site publishes no products because its
  catalogue lives inside Instacart.
- **The GFP-255 ethnic-grocery hypothesis loses its payoff.** Even if H Mart,
  99 Ranch, Northgate and Vallarta all run on Storefront, one partnership
  reaching four chains reaches four chains' *checkout*, not their catalogues.

**Where it is still exactly right: v3.** GFP-232 needs a way to turn the
optimiser's basket into a purchase, and `products_link` does precisely that —
send the shopping list, get a link, the client checks out. That needs no
catalogue access, because we already know what we want to buy. The ticket
belongs under GFP-232, not under market expansion.

---

## Greensboro, complete

All 17 chains on the 2026-08-09 target list, probed to the payload:

| outcome | chains |
| --- | --- |
| **working catalogue** | Harris Teeter, Whole Foods, Food Lion |
| **ad only** (price, no size) | Aldi, Lidl, Target, Publix, Sprouts, Lowes Foods |
| **bot-blocked** | Publix (data path), Costco, Save-A-Lot, Walmart |
| **no catalogue published** | Sprouts, Lowes Foods, The Fresh Market, Trader Joe's |
| **not on Flipp, no route** | Super G Mart, Bestway Grocery, Deep Roots Market |

**4 of 17 contribute usable rows — 24%. That is the acquisition ceiling.** There
is no eighteenth thing to try.

## What remains, and it is not acquisition

Both of these are in data already held, need no permission and no vendor:

1. **PRISM protein** — Food Lion and GIANT sit at 24.4% while their pages carry
   `Protein 18 g` that is written to `notes` and never read. The identical fix
   took Wegmans from 27.7% to 78.2%. Estimated **+400 usable rows**.
2. **Wegmans SKU cap** — 600 of 1,535 discovered SKUs are scraped. Raising the
   cap is **~+700 rows** for work already done.
3. **Cross-store size borrowing** — measured at **39%** of the price-only
   protein rows (113 of 288) able to borrow a size from a catalogue already in
   the database. GFP-248's join is currently same-store only; food size is a
   property of the product, not the shop.

Together roughly **+1,200 usable rows**, against a current total of 1,377.

---

## Philadelphia, complete

All 20 chains on the 2026-08-09 target list:

| chain | outcome | evidence |
| --- | --- | --- |
| **ACME Markets** | ad only | Flipp weekly ad integrated; its catalogue is Albertsons → Imperva |
| **GIANT** (The GIANT Company) | **working catalogue** | PRISM `/groceries/product/`, plus a Flipp ad found 2026-08-09 |
| **Wegmans** | **working catalogue** | own JSON API; 16 PA stores exist, none scraped yet |
| Weis Markets | ad only | Flipp; catalogue not probed |
| H Mart | ad only | Flipp — notable, since GFP-255 assumed white-label |
| ALDI | closed | see Aldi section |
| Lidl | open, worthless | price only |
| Target | ad only | 1.6% protein density, lowest measured |
| Sprouts | ad only | publishes no catalogue |
| Whole Foods | **working** | own storefront, human-minted session |
| **ShopRite** | bot-blocked | 403 on robots.txt itself |
| Walmart | bot-blocked | PerimeterX |
| Costco | bot-blocked | 403 on all product pages |
| BJ's Wholesale | **not probed** | not on Flipp; no route identified |
| Save-A-Lot | bot-blocked | challenge page behind a 200 |
| Redner's Markets | **not probed** | not on Flipp |
| The Fresh Grocer | **not probed** | Wakefern banner; ShopRite is 403, likely the same |
| Trader Joe's | closed | 403 on everything |
| Amazon Fresh | **not probed** | no route identified |
| First Oriental Market | **not probed** | not on Flipp; likely no e-commerce |

**Philadelphia is effectively uncovered in the database** despite ten registered
banners: only ACME has data (245 rows, 2 usable), and Wegmans' Pennsylvania
stores have never been scraped — only Chapel Hill, NC.

**Five chains were never probed** and are recorded as such rather than as dead:
BJ's, Redner's, The Fresh Grocer, Amazon Fresh, First Oriental Market.

---

## Which chains publish a Flipp weekly ad

Surveyed 2026-08-09 by reading Flipp's merchant list per ZIP. The cheap route,
and the only one that scaled — but see the density table for what it is worth.

**Philadelphia 19103** — 80 flyers, 27 with a weekly ad:
ALDI, Acme Markets, Associated Supermarkets, Boscov's, CVS, Dollar General,
Dunham's, Food Lion, GameStop, Giant Food, **Giant Food Stores**, Grocery
Outlet, **H Mart**, Hobby Lobby, Home Depot, Lidl, Lowe's, Michaels, Ocean
State Job Lot, Sprouts, Supremo Foods, Target, ULTA, Walgreens, Wegman's, Weis
Markets, Wild Fork.

**Greensboro 27401** — 63 flyers, 20 with a weekly ad:
ALDI, CVS, Dollar General, Dunham's, **Earth Fare**, Food Lion, GameStop,
Harris Teeter, Hobby Lobby, Home Depot, Lidl, Lowe's, **Lowes Foods**,
Michaels, **Publix**, Sprouts, Target, ULTA, Walgreens, Wegman's.

**Not on Flipp in either market:** ShopRite, Walmart, Costco, BJ's, Save-A-Lot,
Redner's, The Fresh Grocer, Trader Joe's, Amazon Fresh, First Oriental Market,
The Fresh Market, Super G Mart, Bestway, Deep Roots.

Extras Flipp carries that were not on the target lists: Grocery Outlet, Wild
Fork, Supremo Foods (Philadelphia); Earth Fare (Greensboro).

Merchant strings must be exact — Flipp writes `Wegman's` with an apostrophe and
`Lowes Foods` without one, and a near-miss silently matches nothing.

---

## The density measurement that reframed the epic

Every candidate merchant's live weekly ad, measured against GFP-197's three
quality columns on 2026-08-09.

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
Flipp gives sizes for only 4.9% of items — is confirmed across fourteen
merchants. It is a *Flipp* property, not a Food Lion one.

### What adding ten of them actually bought

All ten registered banners were scraped into the live database:

```
TEN NEW BANNERS:     2,066 priced rows ->     3 reach a $/g figure   (0.1%)
ONE NEW CATALOGUE:     600 priced rows ->   469 reach a $/g figure  (78.2%)
```

Chain count went 7 → 18. Usable rows went 908 → 1,377, and **469 of that gain
came from one source**.

ACME's 2 and Lowes Foods' 1 are accidents — a size that happened to sit in an
item name. Food Lion's ad yields 8 and Harris Teeter's 13 *only because those
stores have a catalogue* for GFP-248's join to borrow a size from. The eight
banners with no catalogue yield exactly zero.

**Coverage is not capability.** Keep the ad feeds — they cost nothing, they are
correct per-ZIP price sources, and they convert the moment a catalogue exists
for the same chain. But adding more of them is not progress.

---

## Paid services evaluated

None purchased. Recorded so the evaluation is not repeated.

| vendor | offering | verdict |
| --- | --- | --- |
| **Bright Data** | proxies, Web Unlocker, Scraping Browser, **datasets marketplace** | Pricing gated behind per-product pages. Pay-per-result against ~30k products per banner across ~20 chains is a recurring bill that scales with coverage. The *datasets marketplace* is the only part worth pricing — buying data carries none of the maintenance or terms exposure of collecting it. Criterion: does it carry SIZE and PROTEIN, or a UPC? |
| **Apify** | Actor marketplace plus infrastructure | Reasonable candidate to replace `server/renderer`. Adds nothing for Kroger, Wegmans or PRISM, none of which needs a browser in production. A marketplace Actor is not permission. |
| **Scrapfly / Oxylabs / ScrapingBee** | proxying, rendering, per-site endpoints | Same shape; their Imperva/Akamai support is the selling point, which is the part below. |
| **Capsolver / Hyper Solutions** | direct Incapsula and `reese84` solvers | Explicitly challenge-defeating. Not built against, bought or written. |

**The distinction:** proxying and rendering for a site that permits access is
someone else operating the container already in `server/`. Solving a challenge
that returned "Pardon Our Interruption" is defeating a control that said no.
These vendors span both.

**The practical point:** the four sources that carry the data need none of it.
The vendors were being considered for Albertsons and Lidl. Lidl is price-only
however it is reached, which leaves Albertsons as the only case where a vendor
unlocks anything real — and it is the case where the tooling is explicitly
about defeating the control.

### The free alternative to the one thing a vendor was wanted for

Geo-targeting, to test whether PRISM's default-store price varies by requesting
region, needs no vendor. GFP-164 is already buying AWS, which gives multiple
regions on the free tier: a function in `us-east-1` (N. Virginia) and one in
`us-west-2` (Oregon) fetching the same product URL answers it at zero marginal
cost. Untested; tracked on GFP-259.

---

## Enrichment routes: unproven, not dead

Distinct from acquisition. These supply the missing size and protein for rows
already held.

| route | status |
| --- | --- |
| **Cross-store catalogue join** | **Measured: 113 of 288 (39%)** price-only protein rows could borrow a size from a catalogue already in the database. GFP-248's join is same-store only; food size is a property of the product, not the shop. Untried. |
| **USDA FoodData Central Branded** | Untried. ~400k branded products, public domain, keyed by UPC and brand. FoodData Central is already ingested for GFP-24 — the *Branded* dataset is separate and far larger. |
| **Open Food Facts** | Partly tested. Brand-batched queries **work**: 1,432 Aldi private-label products with protein per 100g (Simply Nature 482, Specially Selected 271, Friendly Farms 196, Kirkwood 189, L'oven Fresh 103, Never Any! 102, Appleton Farms 73, Fit & Active 16). Per-item free-text search **hits the rate limit** — 1 of 8 succeeded, and that match was questionable (ground turkey → deli turkey breast). A real design batches by brand and gates on match confidence. |

**Cautions carried from GFP-248.** Borrowing across stores is a weaker claim
than within one. The `MAX_PRICE_RATIO` veto (a 4x price gap means the two
numbers are denominated differently) and the contradiction guard (boneless vs
bone-in) both apply, and a cross-store borrow should carry lower confidence than
a same-store one, with same-store always winning.

**The denomination problem sits on top of all of it.** Flipp never states
`sold_by` — it is NULL on every row. `$2.00 BONELESS RIBEYE STEAK` is a
per-pound price with the denomination stripped. Ranking such rows by raw price
would put per-pound meat above per-package items and make the cheapest-looking
rows the most misleading. Any "grams unknown" tier must be unsortable against
real $/g rows and shown in its own section.

Of 288 price-only human-protein rows: **5% carry a size in the name**, 0% carry
a protein claim, and **66% already match a food**. What is missing is almost
never *what it is* — it is *what the price buys*.

---

## Method errors made during this survey

Recorded because each cost real time and each is repeatable.

1. **Concluded from `/` and generalised to the site.** Food Lion's homepage
   returns 403 (DataDome); `/groceries/**` returns 200 with no protection at
   all. One 403 became "walled, drop the family" — and that family held the
   second-best source in the product.

2. **Read a `Disallow` without checking which group it was in.** Quoted PRISM's
   `Disallow: /product-search/` as though it covered `/product/`. Different
   paths. The catalogue was never disallowed.

3. **Inverted the AI-crawler finding.** Read "AI crawlers are welcomed" as an
   open posture. That group is the *most* restricted in the file — it carries
   every `*` restriction plus `Disallow: /product/`. The route worked precisely
   *because* we are not an AI crawler.

4. **Called Albertsons decisive from one product page.** That page was in the
   ~17% that render server-side. A strided sample of 12 got 2 hits, and three
   retries each of the failures returned identical byte counts. **robots.txt
   plus one product page is not a viability test.**

5. **Asserted a hand-written ZIP-prefix footprint.** Claimed Food Lion served
   all of Kentucky (`"40","41","42"`) and all of Georgia. Measured: Food Lion is
   in *one* Kentucky metro — Bowling Green (42101) — while Louisville,
   Lexington, Covington, Owensboro, Elizabethtown, Paducah and Pikeville all
   return no; and it is not in Atlanta at all. **A state is not a unit of
   grocery footprint.** Replaced by asking Flipp, which answers exactly and free.

6. **Read marketing copy as an API contract.** "Real-time inventory and pricing"
   on Instacart's Developer Platform page describes what a *shopper* sees on the
   page Instacart renders, not data returned to a partner. GFP-261 was filed on
   that premise before the endpoint list was checked.

7. **Wrote a broken test and nearly reported its output.** The first Open Food
   Facts enrichment test used a v2 tag-filter endpoint with a free-text
   parameter it ignores; every store returned the same product ("Fromage Blanc
   Nature").

**The corrective is the same every time, and is already this ticket's stated
method:** establish the verdict cheaply, record the evidence, and check what the
evidence actually says before generalising. A strided, retried payload sample is
the minimum bar for calling a chain viable *or* dead.

---

## Out of scope, noted for completeness

- **H-E-B** — blocks search, cart, account, GraphQL and ajax endpoints. Texas
  only, outside both target markets. Not probed further.
- **FreshDirect** — Ahold-owned but *not* on PRISM: a separate stack from
  acquisition, NYC only. Not probed. Tracked on GFP-251.
- **Instacart Connect** (`docs.instacart.com/connect`) — a different product
  from the Developer Platform: retailer-facing, for a grocer integrating its own
  catalogue *into* Instacart. Not available to a third party.
- **Albertsons sibling banners** — Vons, Safeway, Shaw's, Star Market share
  ACME's platform (22-line robots.txt differing only by a date stamp, a banner
  code and the hostname). They fail together; none was probed separately.
- **Giant Food (Landover MD)** — a different company from The GIANT Company,
  same PRISM platform. Outside the target markets; would work if wanted.
- **Stop & Shop, Hannaford** — PRISM banners in New England and upstate NY.
  Outside the target markets. Tracked on GFP-250.
