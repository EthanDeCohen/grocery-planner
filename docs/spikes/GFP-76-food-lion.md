# GFP-76 SPIKE: Food Lion scraper feasibility

**Status:** answered. **Deliverable:** this document (no shippable code — see
"What was actually run" for where the throwaway probes live).

**One-line recommendation:** GFP-5 is **not viable** as an automated
foodlion.com scraper. The site's own catalog data is excellent (real price,
real per-unit price, and full nutrition including protein grams — as good as
GFP-70 found for Whole Foods), but every automated path tested — plain
httpx, headless Playwright, and headed (non-headless) Playwright — was
rejected outright by DataDome bot-mitigation with a flat `403`, on every
route including the plain homepage. This is a materially harder wall than
GFP-70's Whole Foods finding: WFM's problem was a missing-cookie session
bootstrap that *any* Chromium instance (including a freshly-launched,
automated one) could solve once and then hand off to httpx. Food Lion's
DataDome specifically rejects the automation framework itself, headless or
not, so there is no browser step a shipped CLI could run that would get past
it in the first place. See [Recommendation](#recommendation).

---

## TL;DR

| # | Question | Answer |
|---|----------|--------|
| Q1 | Does the payload carry size, price, and nutrition? | **Yes — real shelf price, real per-unit price, package size for fixed-size items, and full nutrition including protein grams, all rendered directly on the product/search pages.** As good a source as Whole Foods, quality-wise. |
| Q2 | httpx or a real browser? | **Neither works as an automatable step.** Plain httpx: `403` from DataDome on every path, including `/`. Headless Playwright: `403`, same wall. Headed (non-headless) Playwright: `403`, same wall. Only a pre-existing, already-authenticated real Chrome session (not spawned by an automation framework) got through — and that same session was later hit with an adaptive CAPTCHA challenge ("Verification Required... Automated (bot) activity") after continued rapid navigation. We did not attempt to solve it — bypassing bot-detection/CAPTCHAs is out of scope and against policy, not just this ticket's "be a polite client" instruction. |
| Q3 | Can it be pinned to a store / ZIP? | **Yes** — confirmed with ZIP `27401`: the store list returned real Greensboro, NC stores headed by "2316 E Market St, Greensboro, NC 27420" at 0.6 miles, and selecting it changed the *actual displayed price* of an identical SKU (Food Lion 73% Lean Ground Beef) from $5.39/lb at the session's default store to $4.79/lb at the Greensboro store — real per-store pricing, not a cosmetic label. |
| Q4 | Can results be joined to the 635 Food Lion deals we hold? | **Yes, well** — real ad-copy headlines from the database ("Pork Chops", "73% Lean Fresh Ground Beef") work directly as search queries against Food Lion's own site search and return closely-matching real products as top hits (e.g. "Pork Chops" → "Food Lion Boneless Pork Chops Fresh" $4.29/lb; "73% Lean Fresh Ground Beef" → "Food Lion 73% Lean 27% Fat Ground Beef Fresh" $5.39/lb, an exact lean-percentage match). The join mechanism would be "search by headline text," not exact string matching — but it works. This finding is moot given the Q2 answer: there is no automatable way to run the search in the first place. |

---

## Q1 — Does the payload carry SIZE, PRICE, and NUTRITION?

**Yes, and it's excellent — on par with GFP-70's Whole Foods finding.**

foodlion.com is built on Ahold Delhaize's Peapod Digital Labs (PDL) platform
(confirmed via `service:Peapod%20Digital%20Labs` tags on the site's Datadog
RUM beacon calls, and product images served from `i5.peapod.com`) — the same
corporate family/stack as Giant, Stop & Shop, and Hannaford.

A real category page (`/browse-aisles/categories/1/categories/1563-meat`,
"Meat") renders product cards directly in the page with real prices, e.g.:

```
Food Lion 85% Lean 15% Fat Ground Beef Round Fresh
Sale Price $6.59   Original Price $7.29
$6.59 /lb
```

```
Jimmy Dean Premium Regular Pork Breakfast Sausage Roll
Sale Price $5.29   Original Price $6.49
16 oz pkg | $5.29 /lb
```

Every card carries a real current price, and a real per-unit price
(`$X.XX /lb` or `$X.XX /oz`). Fixed-size packaged items additionally carry a
package size string ("16 oz pkg", "3 lb pkg", "11.5 oz pkg", "2.1 oz pkg").
Butcher-counter items sold by weight ("Family Pack", "apx 1 lb") have no
fixed size at all — priced directly per pound instead, same shape as GFP-70's
finding for Whole Foods' butcher-counter items.

The product detail page (`/product/{slug}/{numeric-id}`, e.g.
`/product/food-lion-85-lean-15-fat-ground-beef-round-fresh-apx-1-lb/368036`)
goes further and renders a full **Nutrition** tab:

```
Food Lion 85% Lean 15% Fat Ground Beef Round Fresh
$6.59 (was $7.29)
$6.59 / lb | final cost by weight

240 CALORIES   7g SAT FAT   75mg SODIUM   -- SUGARS

Nutrition Facts
varied servings per container
Serving Size            4 ounce
Amount per serving
Calories                240
Total Fat               17g   26%
Saturated Fat            7g   32%
Cholesterol             75mg  25%
Sodium                   75mg  3%
Protein                 21g
Iron                          10%

Ingredients: Beef.
```

**`Protein 21g` per 4-ounce serving is exactly the number GFP-26's
`cost_per_gram_protein` chain needs** — and for a variable-weight item like
this one ("varied servings per container", priced `$/lb`), it is *more*
directly usable than a fixed package size: `$/lb` converts straight to
`$/4oz-serving`, divided by 21g protein, with **no USDA catalog match and no
`matching.py` lookup required at all** — the same "butcher-counter items are
actually the best-fit case, not the hardest one" finding GFP-70 made for
Whole Foods.

This is a real, structurally rich source. The problem is entirely Q2.

## Q2 — httpx, or does it need a browser?

**Neither reliably works, because the wall here is bot-mitigation
(DataDome), not a missing-cookie session bootstrap.**

### Plain httpx: blocked everywhere, unconditionally

A polite httpx client (real Chrome UA, cookie jar enabled, a handful of
requests) got a flat `403` on **every** path tried, not just deep API
routes:

| URL | Result |
|---|---|
| `https://www.foodlion.com/` | `403` |
| `https://foodlion.com/` | `403` |
| `https://foodlion.com/product-search/pork%20chops?...` | `403` |
| `https://foodlion.com/product/food-lion-85-lean-15-fat-ground-beef-round-fresh-apx-1-lb/368036` | `403` |
| `https://foodlion.com/browse-aisles/categories/1/categories/1563-meat` | `403` |

Every response body is the same DataDome interstitial:

```html
<html lang="en"><head><title>foodlion.com</title>...</head>
<body style="margin:0"><p id="cmsg">Please enable JS and disable any ad
blocker</p><script data-cfasync="false">var dd={'rt':'c','cid':'...',
...,'host':'geo.captcha-delivery.com',...}</script>
<script data-cfasync="false" src="https://ct.captcha-delivery.com/c.js">
</script>...
```

A `datadome` cookie is set on the 403 itself, but it's the *challenge*
cookie, not a passing session — reusing it changes nothing.

### Headless Playwright: also blocked

Installed Playwright + Chromium in the throwaway venv (outside the repo,
per the ticket's constraint) and launched headless Chromium with a normal
Chrome UA against the plain homepage:

```
status: 403
title: foodlion.com
body len: 2601
<html lang="en">...<script data-cfasync="false">var dd={'rt':'c',...
```

Identical DataDome interstitial. Headless automation is rejected exactly
like plain httpx.

### Headed (non-headless) Playwright: also blocked

To rule out "headless" specifically being the tell, the same script was
re-run with `headless=False` (a real, visible browser window, still
Playwright-launched):

```
status: 403
title: foodlion.com
body len: 2619
<html lang="en">...<script data-cfasync="false">var dd={'rt':'i',...
```

Still `403`. This rules out "just don't run headless" as a fix — DataDome
here appears to be fingerprinting the automation framework itself (a
Playwright/CDP-driven browser carries detectable signals regardless of the
visible/headless flag), not merely refusing headless Chrome.

### The one thing that *did* get through — and its limits

A real, already-authenticated Chrome browser session (this session's
`claude-in-chrome` browser-automation tool, which drives an existing, real
Chrome instance via a browser extension rather than spawning a fresh
automation-framework context) loaded the homepage, category pages, search
pages, and product pages successfully, with real rendered data as shown in
Q1/Q3/Q4 below.

**But this is not a usable architecture for a shipped feature, for two
reasons:**

1. It depends on a live, already-existing, organically-trusted browser
   profile at scrape time — not something a Python CLI (bundled or
   PyInstaller-built) can spin up itself. GFP-70's "mint a session once with
   a freshly-launched Playwright browser, then reuse the cookies" pattern
   does not apply, because the freshly-launched browser is exactly what got
   blocked above.
2. **The same session was later challenged mid-task.** After continued rapid
   programmatic navigation (the normal shape of a scraping loop — several
   page loads in quick succession, no human pauses), that same real browser
   session hit an adaptive DataDome CAPTCHA:

   ```
   Verification Required

   Slide right to secure your access

   Why is this step needed?
   We detected unusual activity from your device or network. Reasons may include:
     - Rapid taps or clicks
     - JavaScript disabled or not working
     - Automated (bot) activity on your network (IP ...)
     - Use of developer or inspection tools
   ```

   We did not attempt to solve this. Bypassing or completing CAPTCHAs is out
   of scope for this project's own instructions and out of scope on general
   principle — it is not a cost to price in, it's a line not to cross. That
   this triggered on the *legitimate* browser path, from ordinary automated
   navigation, is itself the finding: DataDome here scores behavior
   adaptively, not just the initial request, so even the one path that
   worked is not a stable base to build a scraper on.

### Why this is a harder problem than GFP-70's Whole Foods finding

GFP-70's WFM blocker was "a fresh httpx client cannot bootstrap a session
because of a JS-computed anti-CSRF header" — solvable by literally any
Chromium instance running the page's own JS once, including an automated
one, with the resulting cookies then reusable by plain httpx indefinitely.
Food Lion's blocker is upstream of all of that: **the automation framework
itself is rejected before any page JS, session, or cookie logic even runs**,
and the one path that isn't rejected (a real, human-associated browser) is
not a repeatable pattern a shipped CLI can rely on and further degrades
under sustained automated use.

## Q3 — Can the store be pinned to a ZIP?

**Yes — confirmed, and store-specific pricing is real, not cosmetic.**

With no ZIP entered, the session defaulted to store "3346 Halifax Road,
24592" (South Boston, VA) — wherever this session's default geo-resolution
landed, unrelated to the ticket's 27401 anchor. Opening "Select a Store" and
entering ZIP `27401` returned a store list headed by:

```
1. Food Lion
   2316 E Market St
   Greensboro, NC 27420
   0.6 miles
```

— a real Greensboro, NC address, correct for ZIP 27401. Selecting it updated
the header from "In-Store at 3346 Halifax Road, 24592" to "Browsing at 2316
E Market St, 27420", **and the displayed price of the same SKU actually
changed**: "Food Lion 73% Lean 27% Fat Ground Beef Fresh" was $5.39/lb at
the default South Boston, VA store and $4.79/lb after pinning to the
Greensboro, NC store. That's real per-store price data, confirmed by an
observed price change on an identical product, not just a changed label.

(Note: selecting a store without signing in shows "Browsing at ..." rather
than "In-Store at ...", suggesting the pin is session-scoped rather than
account-scoped unless signed in — not tested further, and irrelevant given
the Q2 finding.)

## Q4 — Can results be joined to the 635 Food Lion deals we hold?

**Yes, well — via search, not exact-string matching.** (Moot given Q2, but
answered as asked, since it's easy to get wrong in the other direction: real
data that turns out unusable because names don't line up.)

Sampled real Food Lion `deals.item_name` values straight from the live
database (read-only connection, per the ticket) that the existing
`matching.py` module already recognizes as protein items — e.g. `"Pork
Chops"`, `"73% Lean Fresh Ground Beef"`, `"BONELESS RIBEYE STEAK"`,
`"Chicken Wings"`, `"Food Lion Boneless Chicken Breast"`, `"Fresh Atlantic
Salmon Side"`, `"Food Lion Raw Shrimp"` — and used two of them as literal
search queries against `foodlion.com/product-search/{query}`:

| Ad-copy headline (from the real DB) | Top real search result | Match quality |
|---|---|---|
| `"Pork Chops"` | "Food Lion Boneless Pork Chops Fresh" — $4.29/lb | Close — ad copy is generic, site name is a specific cut, but clearly the same product family |
| `"73% Lean Fresh Ground Beef"` | "Food Lion 73% Lean 27% Fat Ground Beef Fresh" — $5.39/lb | Exact — same lean percentage, same store brand |

Food Lion's own site search tolerates the free-text, less-specific ad-copy
phrasing well (it's built for shopper queries, not exact catalog lookups),
so the join strategy that would work is "use the deal's headline text
(`matching.headline()`, already built) as a search query and take the top
relevant result(s)," not an exact-string join on product name. That's a
workable design — the problem this spike surfaces is entirely upstream of
it (Q2), not this.

## Also worth reporting: per-store size coverage, from the real database

Queried the live database read-only (`sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)`,
never written to) and ran the existing `savings.parse_size()` against every
row, split by store:

| Store | Deals | Readable size (`parse_size`) | Coverage |
|---|---|---|---|
| Harris Teeter | 938 | 46 | **4.9%** |
| Food Lion | 635 | 2 | **0.3%** |
| **Total** | **1,573** | **48** | **3.1%** |

This confirms the ticket's framing directly: Harris Teeter bakes size into
its ad copy far more often ("16.4 - 17.9 oz. Betty Crocker Cookie Mix", "24
oz. Harris Teeter Maple Syrup") while Food Lion's ad copy is almost always
bare brand/product names with no quantity at all ("Mini Bagels", "Food Lion
Brewed Tea", "Pork Chops"). The leverage is **not** evenly split: a
size-parsing improvement effort would pay off disproportionately for Harris
Teeter's existing ad copy, while Food Lion's gap can only be closed by an
external source (this spike) or by leaning harder on the existing
USDA-match pipeline (GFP-23/25/26) for the many deals — at both stores —
that are priced per pound with no fixed size at all, where a size string was
never the right lever to begin with.

## What was actually run

All probe code is throwaway, lives outside the repo (per the ticket's
constraint — a scratch venv, nothing touched `grocery_planner/` or
`pyproject.toml`), and the real database was opened strictly read-only
(`?mode=ro`) for the coverage/sample queries.

- `probe1_home.py` — plain httpx GET against `www.foodlion.com` and
  `foodlion.com`; established the outright DataDome `403` on the homepage.
- `probe2_paths.py` — plain httpx GET against search, product, and category
  paths; confirmed the block is universal, not route-specific.
- `probe3_playwright_headless.py` — installed Playwright + Chromium
  (`playwright install chromium`) in the scratch venv and loaded the
  homepage headless; confirmed the same `403` DataDome wall for automated
  Chromium.
- `probe4_playwright_headed.py` — identical script with `headless=False`;
  confirmed the block is not merely a "headless" tell.
- Browser session via this session's `claude-in-chrome` tool (a real,
  already-authenticated Chrome instance, not a freshly-spawned automation
  context) — used to characterize the site's actual payload: category page
  (`/browse-aisles/categories/1/categories/1563-meat`), a product detail
  page (`/product/food-lion-85-lean-15-fat-ground-beef-round-fresh-apx-1-lb/368036`),
  and `/product-search/{query}` for `"pork chops"` and `"73% Lean Fresh
  Ground Beef"`. Also used to open the "Select a Store" modal, enter ZIP
  `27401`, and confirm the resulting real store address and price change
  (Q3). This same session was later presented an adaptive DataDome CAPTCHA
  ("Verification Required") after continued rapid navigation, which was
  **not** attempted or solved.
- `coverage_and_names.py`-equivalent ad hoc queries against the real,
  read-only database — per-store deal counts (635 Food Lion / 938 Harris
  Teeter / 1,573 total), per-store `savings.parse_size()` coverage, and
  sampled real Food Lion item names (including every one `matching.py`
  currently recognizes as a protein item) for the Q4 search comparison.

Traffic was modest: a handful of category/product/search page loads plus
the httpx/Playwright probes above (each of which got an immediate `403`
with no retry loop), well within "characterizing a source" rather than
harvesting it.

## Recommendation

**GFP-5 is not viable as an automated foodlion.com scraper.**

This is not a data-quality problem — Q1 shows foodlion.com's own catalog is
as good a source as GFP-70 found Whole Foods to be: real shelf price, real
per-unit price, and full nutrition including protein grams, often requiring
no USDA catalog match at all for the same reason GFP-70 found (butcher-
counter items priced per pound are the easy case, not the hard one). Q3 and
Q4 both come back clean too: ZIP pinning is real and verifiable, and the
existing ad-copy headlines join cleanly against the site's own search.

The blocker is entirely Q2, and it is a harder, different kind of blocker
than GFP-70's: Whole Foods needed a browser to bootstrap a session once,
after which plain httpx worked indefinitely — a real architecture decision,
but not a stop sign. Food Lion's DataDome protection rejects the automation
step itself: plain httpx, headless Playwright, and headed Playwright were
**all** turned away with the same `403` before any session or cookie logic
had a chance to run. The only path that worked — an already-authenticated,
non-automation-framework real browser session — is not something a shipped
Python CLI (bundled or PyInstaller-built, on any platform) can reproduce on
demand, and even that path degraded to an adaptive CAPTCHA challenge under
ordinary scraping-shaped usage. Solving that CAPTCHA would be required to
go further, and that is out of scope on principle, not just cost — this
project should not build a feature whose only path to working requires
defeating a bot-mitigation vendor whose specific job is to prevent exactly
that.

**What else might work**, if the 635 Food Lion deals' missing size/nutrition
data is still worth pursuing:

1. **Lean harder on the existing GFP-23/25/26 pipeline instead of a new
   scrape.** The coverage table above shows most Food Lion (and Harris
   Teeter) meat deals are priced per pound with no fixed package size at
   all — for those, a size string was never the right lever; the
   already-built USDA-catalog match (`matching.py`) plus curated
   protein-per-100g data (`foods`/`food_nutrients`) is the correct source,
   and doesn't depend on scraping anything new. This is the same
   observation GFP-70 made for Whole Foods' butcher-counter items, just
   applied to the store where scraping the retailer itself turned out to be
   the wrong path rather than the right one.
2. **A third-party grocery marketplace** (e.g. Instacart, which lists Food
   Lion's catalog in some markets) was not tested in this spike — out of
   scope for the time available — but is the natural next probe if this
   line of investigation continues, since it may not carry the same
   bot-mitigation posture as the retailer's own site.
3. **Shelving the Food-Lion-specific half of GFP-5 entirely** and treating
   this spike's answer as final: the 635 Food Lion deals' cost-per-gram-
   protein coverage improves only through the matching/nutrition pipeline,
   not through a new scrape target.
