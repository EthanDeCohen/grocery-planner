# GFP-246 SPIKE: is PRISM uniformly walled?

**Status:** answered, 2026-08-08. **Deliverable:** this document. No code.

**One-line recommendation:** **Drop the Ahold Delhaize family from GFP-165.**
GIANT is walled exactly as Food Lion is, on both policy and technical grounds,
and the two are provably the same configuration. See
[Recommendation](#recommendation).

---

## Why this was worth running

Established 2026-08-08: **Food Lion, Giant Food, GIANT/MARTIN'S, Hannaford and
Stop & Shop all run on PRISM**, the proprietary platform built by Peapod Digital
Labs, Ahold Delhaize USA's digital and e-commerce engine.

GFP-165's original hypothesis was the optimistic one: *if Giant isn't walled,
GFP-76's DataDome block is per-banner and Food Lion may reopen.* The shared
platform reversed that prior — one platform much more likely means one
protection configuration — but the test was cheap and both outcomes were
valuable:

- **Open** → one integration reaches four banners: Food Lion (NC), GIANT
  (Philadelphia), Stop & Shop and Hannaford. The largest single-integration
  prize anywhere in the epic.
- **Walled** → four chains retire at once, GFP-5 closes for good, and GIANT
  comes off the Philadelphia plan.

It is the second one.

---

## TL;DR

| # | Question | Answer |
|---|----------|--------|
| Q1 | Does robots.txt permit what an integration needs? | **No.** `User-agent: *` disallows `/product-search/` and `/browse-aisles/` — precisely the paths a price integration would read. |
| Q2 | Is GIANT the same configuration as Food Lion? | **Yes, provably.** All three robots.txt files are 167 lines and differ by *exactly one line*: the sitemap hostname. |
| Q3 | Is GIANT technically walled like Food Lion? | **Yes.** All three return `403 Forbidden` with `X-DataDome: protected` to an ordinary homepage request. |
| Q4 | Does the Philadelphia banner differ from the Maryland one? | **No.** The GIANT Company and Giant Food behave identically. |

---

## Q1 — What robots.txt permits

The governing group, identical on all three properties:

```
User-agent: *
Disallow: /product-search/
Disallow: /browse-aisles/
Disallow: /*returnurl=
Disallow: /*searchRef=
```

**This is the finding that decides the ticket, and it is a policy answer, not a
technical one.** The two disallowed paths are exactly what a price integration
would need to read. No technical result could override it, and the ticket said
as much before the probe ran.

Worth noting what the rest of the file does: it grants explicit, generous access
to ad crawlers and to AI/answer-engine crawlers (GPTBot, ClaudeBot,
PerplexityBot, Google-Extended). So this is not a blanket anti-bot posture —
it is a deliberate policy that welcomes crawlers whose traffic they benefit
from and excludes product-catalogue reading specifically. That is a considered
"no", not an oversight.

## Q2 — GIANT and Food Lion are the same configuration

Three properties fetched, `User-agent: *` groups compared, then a full diff:

```
$ diff robots-giantfoodstores.com.txt robots-foodlion.com.txt
167c167
< Sitemap: https://giantfoodstores.com/groceries/sitemap.xml
---
> Sitemap: https://foodlion.com/groceries/sitemap.xml
```

167 lines each. **One line of difference, and it is the hostname.** Same rules,
same order, same comments, same section banners.

This is the strongest available evidence that PRISM's configuration is applied
uniformly across Ahold Delhaize USA's banners, and it kills the per-banner
hypothesis outright. There was no need to reason about it from the platform
announcement; the artefacts say it directly.

## Q3 — DataDome, on every banner

One ordinary `GET /` per property, browser user-agent, no evasion of any kind.
The homepage is *allowed* by robots; the disallowed product paths were not
touched.

| property | status | evidence |
|---|---|---|
| `giantfoodstores.com` (The GIANT Company, Philadelphia) | **403** | `X-DataDome: protected`, `X-DataDome-CID: AHrlqAAAAAMAG-nAZvG81MwArV4cdQ==` |
| `giantfood.com` (Giant Food, MD/DC) | **403** | `X-DataDome: protected`, `X-DataDome-CID: AHrlqAAAAAMAEiA0JQjLy7gArV4cdQ==` |
| `foodlion.com` (the known-blocked control) | **403** | `X-DataDome: protected`, `X-DataDome-CID: AHrlqAAAAAMAkGr_qHLGWfoArV4cdQ==` |

All three sit behind Cloudflare and all three set a year-long `datadome`
cookie on refusal. Food Lion was included deliberately as a control, to confirm
the probe reproduces GFP-76's known result — it does.

## Where the probe stopped, and why

**At the 403.** This ticket was scoped to establish a verdict, not to build an
evasion, and that line was written into it before any request was made. A
control that says no is an answer. GFP-76 reached the same conclusion for Food
Lion and it was the right call then.

The robots.txt finding independently settles it regardless: even a technically
reachable endpoint under `/product-search/` is one the site has asked automated
clients not to read.

---

## Recommendation

1. **Drop the Ahold Delhaize family from GFP-165's plan**, in writing, so it is
   not re-proposed: Food Lion, GIANT (Philadelphia), Giant Food, and by strong
   inference Stop & Shop and Hannaford.
2. **Close GFP-5 (Food Lion shelf prices) for good.** It was already parked;
   this removes the last reason to revisit it. GFP-27, which was gated on it,
   should be re-planned without a Food Lion source.
3. **Remove GIANT from the Philadelphia chain list.** GFP-203's Philadelphia
   regionals (ShopRite, Wegmans, Weis) and GFP-199's ACME carry that market now.
4. **Do not buy this data either.** Measured 2026-08-07 while fixing GFP-121:
   Food Lion's flyer yields 37 matchable protein items out of 543 distinct names
   (6.8%) against Harris Teeter's 485 of 1,743 (27.8%). Their ad skews to
   beverages, baby products and dairy. Even free and unblocked, this family is
   a thin protein source — which makes a negative result cheap to accept.

**The corollary worth stating positively:** the effort that would have gone here
belongs in the Kroger family instead — Ralphs and Food 4 Less (GFP-198) reached
through an integration that already exists, against the best-measured source in
the product (100% priced, 100% machine-readable size, 82% carrying protein
grams).

## What was actually run

Three `curl` fetches of `robots.txt` and three `HEAD` requests to `/`, with a
browser user-agent, in August 2026. No product paths were requested, no
protection was circumvented, and nothing was written to any store's systems.
Raw robots.txt captures are in the session scratchpad; the diff above is the
whole of the evidence that matters.
