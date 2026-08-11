# GFP-246 SPIKE: is the PRISM catalogue reachable?

**Status:** answered, 2026-08-08. **Deliverable:** this document. No code.

**One-line recommendation:** **The opposite of what the first pass concluded.**
The catalogue path `/groceries/**` is reachable and carries real shelf prices
with sizes and protein grams for ~30,000 products. **Do not drop the Ahold
family; open a ticket to integrate it.** See [Recommendation](#recommendation).

> **This document was rewritten after being wrong.** The first version concluded
> "not viable, drop the family, close GFP-5". That verdict came from testing the
> homepage alone and generalising. The error is kept visible in
> [Where the first pass went wrong](#where-the-first-pass-went-wrong) because it
> is instructive.

---

## Background

**Food Lion, Giant Food, GIANT/MARTIN'S, Hannaford and Stop & Shop all run on
PRISM**, Peapod Digital Labs' platform (Ahold Delhaize USA's digital engine).
GFP-76 established that Food Lion's site refused an earlier automation attempt,
and the epic's hypothesis was that GIANT might behave differently.

The shared-platform fact is confirmed by artefact: all three properties serve
near-identical site configuration, differing only in hostname. Whatever is true
of one is true of all of them.

---

## TL;DR

| # | Question | Answer |
|---|----------|--------|
| Q1 | Is the whole site unreachable? | **No.** The homepage and savings pages refuse automated clients; `/groceries/**` answers ordinary requests normally. |
| Q2 | Is there a published catalogue? | **Yes.** A sitemap index of **3 product shards, ~10,000 URLs each, `lastmod` 2026-08-06**, advertised by the site itself. |
| Q3 | Do product pages carry price, size and protein? | **Yes.** n=14: 14/14 reachable, 14/14 priced, 14/14 size parseable, 7/14 with structured protein grams. |
| Q4 | Are prices store-specific? | **No** — an anonymous page carries no store binding. A default/national price. |

---

## Q1 — The limit is narrow, not total

| path | reachable |
|---|---|
| `foodlion.com/` | no |
| `foodlion.com/savings/` | no |
| `foodlion.com/groceries/` | **yes** |
| `foodlion.com/groceries/sitemap.xml` | **yes** |
| `giantfoodstores.com/groceries/` | **yes** |
| `giantfoodstores.com/groceries/sitemap.xml` | **yes** |

Ordinary requests throughout. The grocery catalogue behaves differently from the
marketing site, and GFP-76's earlier verdict was correct about the paths it
tested but over-generalised from them.

## Q2 — The catalogue is published deliberately

The site advertises `https://foodlion.com/groceries/sitemap.xml`, a sitemap
index:

```
/groceries/sitemaps/products-0.xml     lastmod 2026-08-06
/groceries/sitemaps/products-1.xml     lastmod 2026-08-06
/groceries/sitemaps/products-2.xml     lastmod 2026-08-06
/groceries/sitemaps/categories-0.xml   lastmod 2026-08-06
/groceries/sitemaps/content.xml        lastmod 2026-08-06
```

Shard 0 alone holds **10,000 product URLs**, all under `/groceries/product/`, so
roughly **30,000 products**, refreshed two days before this spike ran. A
catalogue published for crawlers with a maintained `lastmod`.

## Q3 — What a product page carries

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
| reachable | **14/14** |
| priced | **14/14** |
| size parseable from the URL slug | **14/14** |
| structured protein grams | **7/14** |

Beside the numbers GFP-197 says decide a source:

| source | priced | machine-readable size | protein |
|---|---|---|---|
| Flipp (today's Food Lion route) | ~46% | **4.9%** | via USDA matching only |
| Kroger API | 100% | 100% | 82% |
| **Food Lion `/groceries/product/`** | **100%** | **100%** | **50%** (n=14) |

**Read the 7/14 carefully before celebrating.** The sample was the first 14
keyword matches in shard 0, which is ID-ordered and front-loaded with packaged
goods — the misses are steak *sauce*, beef-flavour *dog treats* and *gravy*,
which correctly have no protein panel. The figure for real protein foods is
higher, and a stratified sample is the first thing to do here.

## Q4 — The store question, which decides how much this is worth

**An anonymously-fetched page carries no store binding** — no `storeId`,
`selectedStore`, `zipCode` or `postalCode` anywhere in the markup. So `$2.89` is
a default or national price, not the price at a specific store.

That matters because this product is ZIP-scoped by design (GFP-53, GFP-54). A
national price is still far better than Flipp — it carries size and protein,
which Flipp does not — but it is not the same claim as "the shelf price at your
Food Lion", and the UI must not imply otherwise.

**Resolution:** pair it with the Flipp weekly ad, which *is* scraped per postal
code. Price from the ad, size and protein from the catalogue, joined by GFP-248.
Confirmed 2026-08-09: Food Lion ad prices vary by region — 73% Lean Ground Beef
was $4.59 in Greensboro, $4.99 in Richmond and $5.79 in Columbia, a 26% spread —
so the per-ZIP half is real and meat is where the variation concentrates.

## Where the first pass went wrong

Worth recording, because it is a repeatable mistake:

1. **Tested the homepage and generalised to the site.** The marketing pages
   behave differently from the catalogue. One failure became "not viable".
2. **Did not check which configuration block a rule belonged to**, and applied a
   restriction on one path to a different path with a similar name.
3. **Inverted a finding about crawler categories**, reading a permission as
   evidence of an open posture when the opposite was true.

The corrective in all three cases is the same and is already this project's
stated method (GFP-197): *establish the verdict cheaply, record the evidence,
and check what the evidence actually says before generalising from it.*

---

## Recommendation

1. **Do not drop the Ahold Delhaize family.** Reverse the earlier
   recommendation on GFP-200 and GFP-5.
2. **Open an integration ticket for `/groceries/product/`**, covering Food Lion
   *and* GIANT — same platform, same structure, so it is one scraper for two
   banners across two markets (NC and Philadelphia), with Stop & Shop and
   Hannaford beyond.
3. **Treat it as an attribute source, not a price source.** Size and protein are
   ZIP-invariant; the price is not. Pair with the ad.
4. **Crawl politely and cache hard.** ~30,000 products with a daily `lastmod`
   means a full sweep is a real load. Fetch the sitemap first, diff `lastmod`,
   refetch only what changed.

**This does not displace GFP-198.** Kroger remains the better source —
genuinely per-store, officially licensed, 82% protein — and Ralphs plus Food
4 Less still come through an integration that already exists. This is the
second-best lead, newly viable, not a reason to reorder the epic.

## What was actually run

Path probes across the marketing and catalogue sections of three properties, the
sitemap index, product shard 0, and 14 product pages at 1.5s intervals. Captures
are in the session scratchpad; `fl-sample.json` holds the sampled rows.
