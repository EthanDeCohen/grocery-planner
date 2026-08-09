# GFP-246 SPIKE: is PRISM uniformly walled?

**Status:** answered, 2026-08-08. **Deliverable:** this document. No code.

**One-line recommendation:** **The opposite of what the first pass concluded.**
PRISM's wall is real but *narrow*: `/groceries/**` is open, unprotected, and
carries real shelf prices with sizes and protein grams for ~30,000 products.
**Do not drop the Ahold family; open a ticket to integrate it.** See
[Recommendation](#recommendation).

> **This document was rewritten after being wrong.** The first version concluded
> "walled, uniformly — drop the family, close GFP-5". That verdict was reached
> by testing `/` and reading the `User-agent: *` Disallow list, and it was
> wrong on both counts. What follows is the corrected finding, with the error
> kept visible in [Where the first pass went wrong](#where-the-first-pass-went-wrong)
> because the mistake is instructive.

---

## Background

**Food Lion, Giant Food, GIANT/MARTIN'S, Hannaford and Stop & Shop all run on
PRISM**, Peapod Digital Labs' platform (Ahold Delhaize USA's digital engine).
GFP-76 established that Food Lion's site refused automation, and the epic's
hypothesis was that GIANT might be unwalled, reopening Food Lion.

The shared-platform fact is confirmed by artefact: all three properties serve a
**167-line robots.txt differing by exactly one line**, the sitemap hostname.
Whatever is true of one is true of all of them.

---

## TL;DR

| # | Question | Answer |
|---|----------|--------|
| Q1 | Is the whole site DataDome-protected? | **No.** `/` and `/savings/` return 403 with `X-DataDome: protected`. `/groceries/**` returns **200 with no DataDome header at all**. |
| Q2 | Does robots.txt permit reading products? | **Yes, for us.** The `User-agent: *` group disallows `/product-search/` and `/browse-aisles/` — but **not** `/product/`. |
| Q3 | Is there a published catalogue? | **Yes.** `robots.txt` advertises a sitemap index of **3 product shards, ~10,000 URLs each, `lastmod` 2026-08-06**. |
| Q4 | Do product pages carry price, size and protein? | **Yes.** n=14: 14/14 reachable, 14/14 priced, 14/14 size parseable, 7/14 with structured protein grams. |
| Q5 | Are prices store-specific? | **Unknown, and this is the biggest open question.** An anonymous page shows no store binding at all. Probably a default/national price. |

---

## Q1 — The wall is narrow, not blanket

| URL | status | DataDome |
|---|---|---|
| `https://www.foodlion.com/` | **403** | `X-DataDome: protected` |
| `https://foodlion.com/` | **403** | `X-DataDome: protected` |
| `https://foodlion.com/savings/` | **403** | `X-DataDome: protected` |
| `https://foodlion.com/groceries/` | **200** | *none* |
| `https://foodlion.com/groceries/sitemap.xml` | **200** | *none* |
| `https://giantfoodstores.com/groceries/` | **200** | *none* |
| `https://giantfoodstores.com/groceries/sitemap.xml` | **200** | *none* |

Ordinary requests, browser user-agent, no evasion of any kind. The grocery
catalogue is simply not behind the bot protection. GFP-76's verdict was correct
about the paths it tested and over-generalised from them.

## Q2 — Which robots.txt group applies, and what it permits

Two groups matter, and the difference between them is the crux:

```
User-agent: *                          User-agent: GPTBot
Disallow: /product-search/             User-agent: ChatGPT-User
Disallow: /browse-aisles/              User-agent: ClaudeBot
Disallow: /*returnurl=                 User-agent: PerplexityBot
Disallow: /*searchRef=                 User-agent: Google-Extended
                                       Allow: /groceries/
                                       Allow: /savings/
                                       Allow: /pharmacy/
                                       Allow: /pages/
                                       Disallow: /product/        <-- EXTRA
                                       Disallow: /product-search/
                                       Disallow: /browse-aisles/
```

**The AI group is MORE restricted, not less.** It carries every `*` restriction
*plus* `Disallow: /product/`. Ahold singled out the named AI crawlers to add a
product-page restriction that generic clients do not have.

So a first-party integration, running under its own user-agent, falls under `*`
and **`/groceries/product/…` is permitted**. This route works precisely *because*
we are not an AI crawler. Sending `User-agent: ClaudeBot` would have forfeited
the access — as well as being a false claim to their servers about who is
asking.

## Q3 — They publish the catalogue deliberately

`robots.txt` advertises `https://foodlion.com/groceries/sitemap.xml`, which is a
sitemap index:

```
/groceries/sitemaps/products-0.xml     lastmod 2026-08-06
/groceries/sitemaps/products-1.xml     lastmod 2026-08-06
/groceries/sitemaps/products-2.xml     lastmod 2026-08-06
/groceries/sitemaps/categories-0.xml   lastmod 2026-08-06
/groceries/sitemaps/content.xml        lastmod 2026-08-06
```

Shard 0 alone holds **10,000 product URLs**, all under `/groceries/product/`, so
roughly **30,000 products**, refreshed two days before this spike ran. This is
not a door left ajar; it is a catalogue published for crawlers with a
maintained `lastmod`.

## Q4 — What a product page carries

`schema.org` structured data in a single `application/ld+json` block:

```json
{"@type": "Offer", "availability": "https://schema.org/InStock",
 "price": 2.89, "priceCurrency": "USD",
 "url": ".../swanson-premium-chunk-chicken-breast-in-water-4-5-oz-can/7134"}
```

And structured nutrition in the page state:

```json
{"amount": 18, "id": "protein", "name": "Protein", "unit": "g"}
```

**Sample of 14 protein-keyword products from shard 0** (1.5s between requests):

| column | result |
|---|---|
| reachable (HTTP 200) | **14/14** |
| priced | **14/14** |
| size parseable from the URL slug | **14/14** |
| structured protein grams | **7/14** |

Put beside the numbers GFP-197 says decide a source:

| source | priced | machine-readable size | protein |
|---|---|---|---|
| Flipp (today's Food Lion route) | ~46% | **4.9%** | via USDA matching only |
| Kroger API | 100% | 100% | 82% |
| **Food Lion `/groceries/product/`** | **100%** | **100%** | **50%** (n=14) |

**Read the 7/14 carefully before celebrating.** The sample was the first 14
keyword matches in shard 0, which is ID-ordered and front-loaded with packaged
goods — the misses are steak *sauce*, beef-flavour *dog treats*, and *gravy*.
Those correctly have no protein panel. The figure for real protein foods is
almost certainly higher, and a proper stratified sample is the first thing
GFP-197 should do here.

## Q5 — The open question that decides how much this is worth

**An anonymously-fetched page shows no store binding whatsoever** — no
`storeId`, `selectedStore`, `zipCode` or `postalCode` anywhere in the markup.
So `$2.89` is most likely a default or national price, not the price at a
specific store.

That matters because this product is ZIP-scoped by design (GFP-53, GFP-54). A
national price is still far better than Flipp — it carries size and protein,
which Flipp does not — but it is not the same claim as "the shelf price at your
Food Lion". **Establish this before building**, and be honest in the UI about
which it is.

## Where the first pass went wrong

Worth recording, because it is a repeatable mistake:

1. **Tested `/` and generalised to the site.** The homepage is protected; the
   catalogue is not. One 403 became "walled".
2. **Read the Disallow list without reading which group it was in.** I quoted
   the `*` group's `/product-search/` and treated it as covering `/product/`.
   Those are different paths, and `/product/` is where the catalogue lives.
3. **Inverted the AI-group finding.** I noted that AI crawlers were welcomed and
   read it as evidence of an open posture, when in fact that group is the *most*
   restricted one in the file.

The corrective in all three cases is the same and is already this project's
stated method (GFP-197): *establish the verdict cheaply, record the evidence,
and check what the evidence actually says before generalising from it.*

---

## Recommendation

1. **Do not drop the Ahold Delhaize family.** Reverse the earlier recommendation
   on GFP-200 and GFP-5.
2. **Open an integration ticket for `/groceries/product/`**, covering Food Lion
   *and* GIANT — same platform, same structure, so it is one scraper for two
   banners across two markets (NC and Philadelphia), with Stop & Shop and
   Hannaford beyond.
3. **Answer Q5 first.** If prices turn out to be national rather than per-store,
   that changes what the UI may claim, not whether to build.
4. **Check the terms of service separately from robots.txt.** They are different
   instruments, and robots.txt permission is not ToS permission. This is the
   same unresolved question as GFP-119 for Kroger, and it should be answered for
   both together rather than twice.
5. **Crawl politely and cache hard.** ~30,000 products with a daily `lastmod`
   means a full sweep is a real load. Fetch the sitemap first, diff `lastmod`,
   and refetch only what changed.
6. **Keep the DataDome line where it is.** `/`, `/savings/`, `/product-search/`
   and `/browse-aisles/` are refused or disallowed. Nothing here needs them, and
   nothing should try.

**This does not displace GFP-198.** Kroger remains the better source — genuinely
per-store, officially licensed, 82% protein — and Ralphs plus Food 4 Less still
come through an integration that already exists. This is the second-best lead,
newly viable, not a reason to reorder the epic.

## What was actually run

`robots.txt` and `HEAD /` on three properties; path probes on `/`, `/groceries/`,
`/savings/` and the sitemaps; the sitemap index and product shard 0; and 14
product pages at 1.5s intervals. Browser user-agent throughout, no
protection circumvented, no disallowed path requested, nothing written to any
store's systems. Captures are in the session scratchpad; `fl-sample.json` holds
the sampled rows.
