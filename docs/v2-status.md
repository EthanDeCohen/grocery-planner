# v2 status: what the board says, and what is actually true

**Measured 2026-08-12 from the live Jira board, after the reconciliation logged
at the bottom of this page.** Refresh this after any batch of ticket work —
a stale roadmap is worse than none, for the same reason a stale coverage
measurement is (see `coverage.md`).

This page exists because the board was actively misleading. Two distortions had
built up, in opposite directions, and between them it was not possible to answer
"where are we" by looking at Jira.

```
v2.0 — Multi-ZIP, multi-store, distributable
121 tickets across 14 epics
    6 Done       8 In Progress      107 Backlog
```

---

## The two distortions, and why they cancelled out badly

### 1. The newest work was invisible

Ten tickets — **GFP-262 through GFP-271** — carried **no fixVersion**, and eight
of them carried **no epic**. Six were In Progress with code committed. Since
fixVersion is the release machinery (see the v1/v2 scope decisions), the most
recent and most active work in the project did not appear in the release at all.

That is not a filing slip with cosmetic consequences. It means every "what is
left for v2" question was answered from a set that excluded the work in flight.

### 2. The oldest tickets claimed work that was finished

The reverse problem, and the larger one. Work landed under **new** tickets while
the **original** tickets stayed in Backlog:

| stayed Backlog | actually delivered by |
| --- | --- |
| GFP-201 Aldi: find a route to price data | GFP-265 — shipped |
| GFP-202 Lidl: find a route to price data | GFP-267 — shipped |
| GFP-206 Sprouts, Trader Joe's, Whole Foods | GFP-262 + GFP-264 — shipped |
| GFP-208 Spike: Walmart as a fourth source | GFP-269 — duplicate |
| GFP-204 Publix, Lowes Foods, Ingles | GFP-270 — **Publix only** |

So the backlog **overstated** remaining work while the release **understated**
delivered work. Both are now corrected, except GFP-204, which is genuinely
partial and stays open for Lowes Foods and Ingles.

**The lesson worth keeping:** a spike ticket that turns into a build should be
closed by the build ticket that replaces it, in the same sitting. Four tickets
sat finished-but-open for over a week because that step has no home in the
workflow.

---

## The five storylines

### 1. Get more data — GFP-165, 32 tickets

The most active epic in the project, and the only one with real momentum. Every
source added on 2026-08-11/12 lives here.

Delivered: Sprouts, ALDI, Trader Joe's, Lidl catalogue, Walmart, Publix, plus
the source survey (GFP-197) and scraper pacing (GFP-263).

Still open: the Albertsons family (six banners, ~200k products, **a UPC on every
one** — GFP-260), ShopRite/Wakefern, Wegmans, FreshDirect, ethnic grocery, the
LA premium tier, and Target via redsky (GFP-268, validated but unbuilt — it has
the cleanest nutrition of any source found so far).

Gated by **GFP-249**: does the market list grow beyond LA, Philadelphia and NC?

### 2. Stop shipping credentials — GFP-164, 14 tickets

The architectural bet: domain and TLS, AWS baseline, the Kroger credential
living only in AWS, server-side scheduled ingest, a sync API, QuotaPool, invite
keys, and the containerised per-ZIP worker fleet (GFP-266).

**Nothing started.** This is what makes v2 distributable at all, and it is also
what stops ten nutritionists in one ZIP from scraping the same catalogue ten
times and taking the source down between them.

### 3. Make the base sound — GFP-163 (15) + GFP-22 (9), 24 tickets

The engineering floor the v1 shakedown audit called for: connection lifecycle,
ranking cost, credential and PII file permissions, log redaction, lint and types
and a lockfile, CI security scanning, golden-payload tests.

Also holds the three known correctness bugs:

* **GFP-271** — ranking has no confidence floor. A 0.3 guess outranks a 1.0
  measured density, and the optimiser returned beef stock as an entire day's
  protein. Getting worse as sources without published nutrition are added.
* **GFP-169** — the grocery list is built from one day's bill multiplied out,
  not from the week plan. Measured divergence 20–127%, with different items.
  The grocery list is the product's one hand-to-a-client artifact.
* **GFP-121** — Food Lion deals are never matched to foods, so 297 priced deals
  are invisible to the optimiser.

**Nothing started.**

### 4. Make it per-location — GFP-53 (5) + GFP-166 (5), 10 tickets

Per-client ZIP codes, a de-duplicated ZIP pool, per-(store, ZIP) scheduling, one
Whole Foods session per ZIP, and deterministic distance in miles.

GFP-257 (store availability by ZIP) is the only ticket here in flight. Multi-ZIP
is the literal definition of this release, and it is otherwise at zero.

### 5. New capability bets — GFP-215 (16) + GFP-167 (2) + GFP-21 (1), 19 tickets

USDA agricultural market data is the largest unstarted epic in the project. It
is gated by its own kill-switch, **GFP-218**: does USDA wholesale data actually
change a client's plan, or is it decoration? Answer that before building the
other fifteen.

The invite-only web app is two tickets, one of which is the decision itself.

### Plus 9 tickets under no epic at all

Family groups, monthly and longer-range cost, Mac distribution through Apple,
selection constraints and objectives, the formulas panel's future. Real work,
but unparented and therefore easy to lose.

---

## The four decisions that gate whole epics

These are cheap to answer and each one either unblocks or **deletes** a chunk of
scope. Answering them shrinks the board faster than writing any code.

| ticket | question | gates |
| --- | --- | --- |
| GFP-249 | Does the market list grow past LA / Philadelphia / NC? | most of GFP-165 |
| GFP-218 | Does USDA data change a plan, or is it decoration? | 15 of GFP-215 |
| GFP-213 | Does the product move to an invite-only website? | all of GFP-167 |
| GFP-258 | Drive time or straight-line miles? | shape of GFP-166 |

---

## The honest read

Recent effort has gone almost entirely into **storyline 1**, and it worked:
usable rows went 1,377 → 2,883 in a single day, and the number of contributing
sources roughly doubled.

But storylines 2, 3 and 4 are what the release name actually promises —
*multi-ZIP, multi-store, distributable* — and all three are at or near zero.
More sources do not make the product distributable, do not make it multi-ZIP,
and (per GFP-271) have measurably degraded the top of the ranking while
improving the totals.

The data-source work was the right thing to do first: there is no point
distributing a product that cannot price a client's week. That case is now made.
The next unit of work that changes what a customer experiences is in storylines
3 and 4, not storyline 1.

---

## Reconciliation log — 2026-08-12

Applied to the board on this date. Recorded so the numbers above can be audited
against the ticket history.

**fixVersion `v2.0` set on 10 tickets** that had none:
GFP-262, 263, 264, 265, 266, 267, 268, 269, 270, 271.

**Parent set to GFP-165 on 8 tickets** that had no epic:
GFP-262, 263, 264, 265, 267, 268, 269, 270.

**Closed as superseded**, each with a comment naming the ticket and commit that
replaced it: GFP-201, GFP-202, GFP-206, GFP-208.

**Left open deliberately:** GFP-204. Publix is delivered by GFP-270, but Lowes
Foods and Ingles are untouched — Lowes Foods publishes a sitemap of 384 URLs and
zero products, and Ingles has never been probed. Closing it would have lost
both. It should be renamed or split.

**Transitioned:** GFP-270 Backlog → In Progress (it had three finished scrapers
in the tree while claiming nobody had started). GFP-197 Backlog → Done (it had
three commits already merged). GFP-269 Backlog → In Progress, with the full
Walmart Affiliate API spike result recorded against its own five questions.

**Created:** GFP-271 (the confidence floor, which had no ticket despite
`coverage.md` calling it the most important open item) and GFP-272 (this
reconciliation and this page). GFP-272 is itself the 121st ticket in the count
above.

**The process gap this exposed, worth fixing rather than repeating:** four
tickets sat finished-but-open for over a week because closing a superseded spike
has no home in the workflow. When a spike turns into a build under a new key,
the old ticket is nobody's job. The convention that would prevent it: the build
ticket closes the spike it replaces, in the same sitting.
