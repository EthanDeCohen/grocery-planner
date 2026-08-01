# GFP-70 SPIKE: Whole Foods scraper feasibility

**Status:** answered. **Deliverable:** this document (no shippable code — see
"What was actually run" for where the throwaway probes live).

**One-line recommendation:** GFP-4 needs a browser, but only once per store
session, not per request — that's a real architecture decision, not a stop
sign. See [Recommendation](#recommendation).

---

## TL;DR

| # | Question | Answer |
|---|----------|--------|
| Q1 | Does the payload carry size and price? | **Yes, and it's dramatically better than the current pipeline.** Real shelf price, per-unit price, and (for ~40% of items in one query) structured size + full nutrition facts including protein grams — enough to compute cost-per-gram-protein *directly*, no USDA matching needed. |
| Q2 | httpx or real browser? | **A real browser is required, but only once, to bootstrap a session.** A fresh plain-httpx client cannot get past a "loading" shell no matter how it's warmed up. But cookies minted by a real browser can then be handed to plain httpx for unlimited further searches — confirmed working. |
| Q3 | Can the store be pinned to a ZIP? | **Yes, deterministically and correctly**, confirmed against two different ZIPs (27401 → Greensboro, NC store; 90210 → Beverly Hills, CA store), each verified against a real street address. One caveat: a display-only cookie field is stale/wrong and must not be trusted — see below. |

---

## Q1 — Does the payload carry SIZE and PRICE?

**Yes.** Every one of 30 `chicken breast` search results carried a real
shelf price. A representative entry (`Bell & Evans Boneless Skinless Chicken
Breast`, trimmed):

```json
{
  "name": "Bell & Evans Boneless Skinless Chicken Breast",
  "asin": "B0787WTY4C",
  "variationsList": [
    {
      "displayString": "Size",
      "symbol": "size_name",
      "variationNodeList": [
        {"dimensionValue": "1.5 Pound (Pack of 1)", "asin": "B0787WTY4C"},
        {"dimensionValue": "3.8 Pound (Value Pack)", "asin": "B0787Y4X59"}
      ]
    }
  ],
  "offerDetails": {
    "price": {"currencyCode": "USD", "priceAmount": 6.99},
    "unitPrice": {"baseUnit": "each", "currencyCode": "USD", "priceAmount": 10.21}
  },
  "nutritionFacts": {
    "servingSize": "4.0 oz",
    "servingsPerContainer": "4.0 servings per container",
    "macronutrients": [
      {"name": "Protein", "amount": "27g", "percent": "", "level": "TOP"}
    ]
  }
}
```

That single object gives us, per product, in one request:

- **`variationsList[].variationNodeList[].dimensionValue`** — a
  machine-readable size string ("1.5 Pound (Pack of 1)") for multi-size
  products.
- **`offerDetails.price.priceAmount`** — real shelf price in dollars.
- **`offerDetails.unitPrice`** — a pre-computed per-unit price (varies:
  `each`, `pound`, `ounce` depending on the product).
- **`nutritionFacts.servingSize` / `servingsPerContainer` /
  `macronutrients[].amount`** — full nutrition, including protein grams per
  serving. Butcher-counter items (bone-in chicken, ground chicken, etc.) sold
  by the pound don't have a fixed "size" at all, but they have `unitPrice`
  (`baseUnit: "pound"`) directly, plus serving-based nutrition — which is
  actually a *better* fit for cost-per-gram-protein than a fixed pack size.

**Coverage, measured against the same 30-result `chicken breast` query that
motivates this spike** (script: `coverage_check.py` below):

| Signal | Count / 30 |
|---|---|
| Has a real price | 30 / 30 (100%) |
| Has a structured size (`variationsList`) or a size token in the name | 11 / 30 (37%) |
| Has full nutrition with a protein gram amount | 15 / 30 (50%) |
| **Has *everything* needed to compute cost-per-gram-protein directly** (price + size-or-per-unit-price + protein + serving size) | **12 / 30 (40%)** |

Compare this to the current pipeline: cost-per-gram-protein resolves for
**2 of 1,573** scraped deals (0.13%) because size is only readable on 51 of
them and then has to be matched against a vendored USDA snapshot. Whole
Foods' own payload gets to **40% directly, from the store itself, with no
USDA matching step at all** — roughly a 300x improvement in the fraction of
deals this metric can be computed for, from a single query. This is the
headline finding of the spike: it is not just "a third store," it's evidence
the model can work well when the source actually publishes size and
nutrition, which Whole Foods does and Flipp-sourced flyers don't.

## Q2 — httpx or a real browser?

**A real browser is required, but the requirement is narrower than "every
request needs one."**

### What plain httpx does, step by step

1. `GET /alm/storefront?almBrandId=...` with a normal Chrome UA, keeping
   cookies: **200 OK**, 921 KB of real HTML (nav, header, footer, the
   `buildId`). But the store-selector button in that HTML renders only as a
   loading skeleton (`<div class="... animate-pulse ...">`), and the
   `Set-Cookie` headers on this response are all scoped
   `Domain=.amazon.com` — which `www.wholefoodsmarket.com` cannot legally
   set (RFC 6265 cross-domain rejection; any compliant client, browser or
   httpx, drops them). **Zero cookies survive this request.**
2. `GET /_next/data/{buildId}/grocery/search.json?k=...` reusing that
   (empty) cookie jar: **200 OK**, but the JSON is a shell:
   ```json
   {"pageProps": {"nonce": "...", "pageType": "loading"}, "__N_SSP": true}
   ```
   `searchResults` and `productsInfo` are both `null`. This matches exactly
   what the prior session found.

### What a real browser does differently

Watching the network tab during a real page load shows the site bootstraps
session state through several *client-JS-triggered* XHR calls that a bare
GET never fires:

- `GET /api/wwos/location/zip` — geo-IP zip lookup.
- `POST /api/wwos/location/store/closest` — pins a store to that zip.
  **This POST requires a custom header, `x-amzn-csrf`, whose value is not
  derivable from the page HTML or from any response header we could find**
  (it isn't the `<meta name="csrf-token">` value, and isn't exposed on any
  cookie) — it's computed by the page's own JS. Calling this endpoint
  ourselves without that header returns `403 {"error":"Forbidden"}`.
- Various analytics/beacon calls (`unagi.wholefoodsmarket.com`,
  `fls-na.amazon.com`) that also appear to be part of session
  establishment.

Only after these fire do the real `.wholefoodsmarket.com`-scoped cookies
(`session-id`, `wfm_store_d8`, `ubid-main`, `cwr_s`, `rxc`, …) get set.
**This is the actual reason plain httpx fails** — it's not one missing
cookie, it's a multi-request client-side bootstrap sequence gated by a
JS-computed anti-CSRF token.

### The important nuance: the browser cost is paid once, not per request

Once a Playwright session has bootstrapped (store pinned, cookies minted),
we exported those cookies and handed them to a **fresh plain-httpx client
with no Playwright involved at all**. That client successfully:

- Called `_next/data/{buildId}/grocery/search.json?k=greek+yogurt` (a
  **different** query than the one the browser searched) and got full real
  results — 30 `productsInfo` entries, real prices, real names
  (`"Chobani® Non-Fat Plain Greek Yogurt 32oz"`, `$6.99`, unit price
  `$0.22/oz`).
- Also worked by hitting the plain HTML page
  (`GET /grocery/search?k=greek+yogurt`) and reading `__NEXT_DATA__` out of
  the response body — same result, no `_next/data` endpoint needed at all.

So the shape of the problem is: **mint a session once with a real browser,
then scrape with httpx for as long as that session stays valid.** We did not
measure session lifetime in this spike (that's a fair follow-up item), but
the long-lived cookies observed (`session-id`, `ubid-main`) carry
multi-year expiries, suggesting the *session* itself is not the limiting
factor — whatever server-side state backs it might still expire sooner,
untested here.

## Q3 — Can the store be pinned to a ZIP?

**Yes — confirmed with two different ZIPs, both verified against a real
street address, not just an internal ID.**

1. With no ZIP input at all, the browser's own geo-IP detection resolved to
   ZIP `27401` (this is apparently where this session/dev machine actually
   geolocates) and store id `10426`. Calling
   `GET /api/stores/10426/summary` confirmed: **"Greensboro Friendly", 3202
   W Friendly Ave, Greensboro, NC 27408** — correct for a 27401 lookup.
2. To rule out that being a coincidence of this machine's location, we
   intercepted the `GET /api/wwos/location/zip` response and forced it to
   return `90210` instead (Beverly Hills, CA) before the app's own
   `store/closest` POST fired. Result: store id changed to `10022`, and
   `GET /api/stores/10022/summary` confirmed: **"Beverly Hills", 239 North
   Crescent Dr, Beverly Hills, CA 90210** — an exact match for the forced
   ZIP, with correct timezone (`America/Los_Angeles`) and coordinates.

**Caveat that matters for implementation:** the `wfm_store_d8` cookie
carries a JSON blob with `id`, `name`, `state`, and `geometry` fields. The
`id` field updates correctly (`10426` → `10022` above) and is authoritative,
but **`name`/`state`/`geometry` do not update — they stay hardcoded to
`"Lamar"` / `"TX"` / Austin coordinates regardless of the real pinned
store.** Any implementation must resolve the store's real identity from
`GET /api/stores/{id}/summary` (or the search payload itself), never from
this cookie's display fields — they're stale placeholders, not the live
store.

## What was actually run

All probe code is throwaway, lives outside the repo (per the ticket's
constraint), and was run from a scratch venv — nothing was added to
`pyproject.toml`, nothing touched `grocery_planner/`. Scripts (for
reference/reproducibility, not shipped):

- `probe1_storefront.py` / `probe2_redirects.py` — plain httpx warm-up +
  `_next/data` call; established the Q2 "shell" behavior and the
  cross-domain cookie rejection.
- `probe3` / `probe4_playwright.py` — first real-browser passes; found the
  store-selector overlay, the geo-IP zip default, and the
  `location/zip` + `store/closest` bootstrap calls.
- `probe5_zip_and_search.py` — decoded the `wfm_store_d8` cookie, exported
  browser-minted cookies + `buildId`, did a full-navigation search capture.
- `probe6_headers.py` / `probe7_find_csrf_source.py` — isolated the
  `x-amzn-csrf` header requirement on the `store/closest` POST and confirmed
  it isn't derivable from any exposed response header, cookie, or `window`
  global we could find in a quick search.
- `probe8_httpx_with_pw_cookies.py` — the key test: fed browser-minted
  cookies into a **fresh httpx client**, ran a brand-new query neither the
  browser nor httpx had searched before, confirmed real results both via
  `_next/data/*.json` and the plain HTML route.
- `probe9_force_zip.py` — forced ZIP `90210` via response interception (not
  a hand-crafted request) to prove store-pinning is genuine ZIP-based
  resolution, not geo-IP coincidence.
- `coverage_check.py` — measured size/price/nutrition field coverage across
  the 30-result `chicken breast` search for the Q1 table above.

Traffic was modest and paced: a Chrome UA throughout, waits for
`networkidle` between steps rather than back-to-back hammering, and a
handful of searches total (`chicken breast`, `greek yogurt`) across roughly
ten page loads spread over the session. No rate-limiting, CAPTCHA, or block
was encountered.

## Cost of the browser path, honestly

Per the ticket's framing, if a browser is needed at all:

- Playwright ships ~150 MB of Chromium (confirmed during this spike: the
  headless-shell download alone was 114.5 MB) against a 15 MB CLI binary —
  a ~10x size increase if bundled unconditionally.
- macOS defaults to Safari, so "use the user's installed Chrome" isn't a
  reliable fallback — Playwright's own bundled Chromium is what actually
  gets used.
- PyInstaller cannot cross-compile, so today's Mac build is CLI-only; it has
  no path to bundling a Windows/Linux-built Playwright browser, and
  vice versa.
- This repo already deleted `feat/playwright-scrapers` once when it
  committed to local-first Python — this isn't a new tradeoff, it's the
  same one recurring.

**But the finding that changes the calculus:** the browser is not needed
per-scrape. It's needed to mint a session (store pinned, cookies set), and
that session can then serve an unknown-but-apparently-long number of
subsequent httpx-only searches. That reframes the question from "does every
`gplan scrape wholefoods` invocation need 150 MB of Chromium" to "does this
machine need to run a browser *occasionally*, to refresh a session file."

### Is the optional-extra pattern (`gui`, `build`) viable here?

**Partially — and it's worth being honest about where it breaks.**

Technically, yes: an extra like `scrape-browser = ["playwright"]` could gate
a small "refresh the Whole Foods session" command that a user runs rarely
(assuming session lifetime turns out to be days/weeks, not minutes — *not
verified in this spike*), producing a cookie-jar file the core httpx scraper
then reads on every actual `gplan scrape` run. Core `grocery_planner/` stays
httpx-only; Playwright never ships to a user who doesn't opt in.

Where it breaks: the Mac PyInstaller build is CLI-only and cannot bundle
Playwright at all (no cross-compile), so a Mac user who wants Whole Foods
data has no in-app way to ever mint or refresh that session — they'd need a
separate Python environment, which contradicts the "no containers, local-
first, single disposable binary" posture this project has already
committed to once (the deleted branch). The `gui`/`build` extras don't have
this problem because they're either optional *at build time* (pyinstaller)
or opt-in on platforms where PySide6 actually ships; Playwright's
requirement — "run a real Chromium at least occasionally on the user's
machine" — is a materially bigger ask than either.

## Recommendation

**GFP-4 needs a browser → architecture decision required, with the
optional-extra option assessed above.**

This is not "don't build it" — Q1's data quality is the best of any store
this project scrapes, by a wide margin (real price, real per-unit price,
and direct protein-grams-per-serving with no USDA matching), and Q3
confirms ZIP pinning works cleanly. But it is also not "proceed as a normal
httpx ticket" — Q2 is unambiguous that a fresh httpx client cannot get past
session bootstrap on its own, and the reason (a JS-computed anti-CSRF token)
is not something to reverse-engineer with confidence it won't silently
break on WFM's next deploy.

The concrete decision GFP-4 is blocked on: **who runs the browser, how
often, and how does a CLI-only Mac build cope with not being able to run
one at all.** Before writing GFP-4 as a real ticket, that needs an answer —
candidates worth weighing (not fully evaluated here, out of scope for a
spike): (a) an opt-in `scrape-browser` extra that refreshes a session file
occasionally, degrading to "Whole Foods unavailable" on the Mac CLI-only
build; (b) a maintainer-side, out-of-band session-minting step whose output
(a cookie-jar file) ships as ordinary config data the CLI reads, keeping
Playwright out of the shipped product entirely, at the cost of a session
that goes silently stale with no user-facing refresh path; (c) deciding the
Mac-build gap alone is disqualifying and shelving GFP-4. A fast, cheap
follow-up worth doing before that conversation: find out how long a minted
session actually lasts (hours vs. weeks) — that number alone probably picks
between (a) and (b).
