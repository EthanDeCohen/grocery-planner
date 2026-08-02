# GFP-77 — Is Harris Teeter reachable through Kroger's public developer API?

**Date:** 2026-08-02
**Status:** Documentation phase complete. **Blocked on credentials the user must
create** — the one decisive question cannot be answered without them.
**Related:** [GFP-70 (Whole Foods)](GFP-70-whole-foods.md),
[GFP-76 (Food Lion)](GFP-76-food-lion.md)

## Why this matters

Harris Teeter is the largest single chunk of the database — **938 of 1,819
deals** — and cost-per-gram-of-protein currently resolves for **3** of them.
Every other source is either exhausted or hostile:

| source | status | $/g protein resolved |
|---|---|---|
| Whole Foods | live, best data of any source | 89 / 246 |
| Harris Teeter | Flipp ad copy only | 3 / 938 |
| Food Lion | DataDome-blocked, [not viable](GFP-76-food-lion.md) | 0 / 635 |

Harris Teeter is a **Kroger banner**, and Kroger runs a *documented, public*
developer API. If it covers HT, it would be the first source whose terms can be
**complied with** rather than worked around.

---

## Part A — the size gap is a DATA gap, not a parser gap (settled, no code needed)

GFP-77 asked whether some of HT's missing sizes are `parse_size` coverage
rather than absent data, on the theory that it "would cost nothing to fix."

**Measured against all 938 HT deals. It would cost nothing because there is
nothing there.**

```
Harris Teeter deals      : 938
parse_size() succeeds on : 46 (4.9%)
fails on                 : 892

Of the 892 failures:
   14  name LOOKS sized but parse_size said no
  122  no size in name, but one in the description
  756  no size anywhere at all
```

Both "recoverable" buckets evaporate on inspection:

**The 14 near-misses are almost all `parse_size` being correct.** Verified
case by case against the function's own documented rules:

| name | why it was rejected | correct? |
|---|---|---|
| `Chobani 20G Protein Drinks` | protein claim, not a size (rule 4) | ✅ — and `parse_protein_claim` reads it as 20.0g |
| `Fresh Foods Market 5 inch Patti Cake` | linear dimension, not a package size | ✅ |
| `Cap'N Crunch or 13 oz. Life Cereal` | "A or B" promo — the size belongs to B (rule 2) | ✅ |
| `Galbani Ricotta Cheese or 12 oz. String Cheese` | same | ✅ |
| `Pepsi 20-oz Product` | **hyphenated unit** | ❌ genuine miss |

Only the hyphenated form is a real gap, so it was measured across the whole
database rather than guessed at:

```
Deals that would newly parse if hyphenated units were handled:
  harristeeter   2
  foodlion       1
  TOTAL          3   (of 1,819)
```

Two of those three are `Equal 230-ct., Swerve ... 12-oz, Whole Earth ...` — a
multi-product listing `parse_size` should reject anyway under rule 2. **Net real
gain: one Pepsi**, which has no protein. **Recommendation: do not write this
code.** It adds a regex branch and an ambiguity ("is `20-oz` the size or part of
a product name?") to buy one soda.

**The 122 "size in the description" are a trap, not an opportunity.** They are
all coupon qualifying text — `Save $1.00 on any ONE (1) 6oz or larger Beggin'`.
That is a *minimum qualifying size*, not the product's size. Reading it as the
package size would silently invent wrong data, which is exactly the failure mode
`parse_size`'s rule 1 (unreadable → `None`, never a guess) exists to prevent.
This confirms the per-pound dead end already recorded in
[[data-source-viability]].

**Conclusion: 756 of 938 HT deals have no size anywhere in the payload. No
parser change can fix that. Only a real product data source can.** Part A does
not weaken the case for the API — it removes the cheap alternative to it.

---

## Part B — what Kroger's public API actually offers (from the docs)

Read from <https://developer.kroger.com> on 2026-08-02. Note the docs site
**blocks plain HTTP fetches** and had to be read in a real browser — the same
posture as the Whole Foods storefront, and a hint about how the API itself may
treat unusual clients.

### There is a genuine public tier

Kroger's developer catalog lists 13 API products, **5 of them Public** (the rest
are Partner-only, i.e. require a business relationship):

| API | tier | relevance |
|---|---|---|
| **Products API** | **Public** | the prize — catalog search + product detail |
| **Location API** | **Public** | needed to get a `locationId` for prices |
| Authorization Endpoints | Public | OAuth2 |
| Cart API | Public | irrelevant (needs a customer login) |
| Identity API | Public | irrelevant |
| Catalog API V2 | Partner | richer, but out of reach |

### Rate limits are documented and generous for this use

- **Products API: 10,000 calls/day**, counted per endpoint, distributable
  across operations however you like.
- **Locations API: 1,600 calls/day per endpoint** (three endpoints).

For context, a weekly refresh of ~940 HT items is trivially inside that budget —
this is not a limit we would have to engineer around.

### The response shape carries exactly what this product needs

The documented `/v1/products/{id}` response includes:

```
description, brand, categories, upc, productId,
items[]              <- carries per-item size and soldBy
itemInformation{}    <- package dimensions/size
nutritionInformation{}   <- THE field this whole project needs
price / nationalPrice    <- regular AND promo price
```

`price` (regular + promo), `aisleLocations`, `fulfillment` and `stockLevel` are
returned **only when `filter.locationId` is supplied**. So the flow is
necessarily two-step: resolve a store via the Locations API, then query products
against that `locationId`.

If `nutritionInformation` and `items[].size` populate for real HT items, this
single source supplies **price + size + protein together** — the same trifecta
that made Whole Foods jump from 0 to 89 resolved deals, but over 938 deals
instead of 246.

### Auth is OAuth2 client credentials — no cookie, no browser, no login

`ClientContext` authorization: a registered app's `client_id`/`client_secret`
exchanged for a bearer token. **This is categorically better than every other
source we have.** It is a supported, documented, revocable credential rather
than a scraped browser artifact — no minting step, no cookie rot, no
`SessionExpiredError` path, and nothing that breaks when a site redeploys
(contrast the Whole Foods `buildId` rotation and the base64 cookie shape change
in GFP-93).

---

## The one question that is NOT answered — and cannot be, yet

**Does Harris Teeter actually appear in this API?**

Kroger owns HT, but ownership is not the same as being in the catalog. Two
concrete reasons for doubt, both found during this spike:

1. Harris Teeter historically ran its own e-commerce stack, and every example in
   Kroger's own docs is a Kroger-banner store (`chain: "KROGER"`, `Kroger Landen`).
2. Kroger's syndication documentation states that **Kroger Our Brands and Harris
   Teeter private-label items are syndicated exclusively through the Syndigo
   platform** — a *separate* channel from this API. That may affect only
   private-label items, or it may indicate HT data lives elsewhere entirely.

The Locations API can settle it in one call, since `/v1/chains` returns every
chain Kroger owns and each chain carries a `domain`:

```bash
# 1. token
curl -s -X POST https://api.kroger.com/v1/connect/oauth2/token \
  -u "$KROGER_CLIENT_ID:$KROGER_CLIENT_SECRET" \
  -d 'grant_type=client_credentials&scope=product.compact'

# 2. THE decisive call -- is Harris Teeter a chain?
curl -s https://api.kroger.com/v1/chains \
  -H "Authorization: Bearer $TOKEN" | jq '.data[].name'

# 3. if yes -- is there an HT store near 27401?
curl -s 'https://api.kroger.com/v1/locations?filter.zipCode.near=27401&filter.chain=HARRIS%20TEETER&filter.radiusInMiles=25' \
  -H "Authorization: Bearer $TOKEN"

# 4. the payload test -- does a real HT item carry size AND protein?
curl -s 'https://api.kroger.com/v1/products?filter.term=chicken%20breast&filter.locationId=<ID>&filter.limit=50' \
  -H "Authorization: Bearer $TOKEN"
```

**Steps 1–3 answer go/no-go. Step 4 answers whether it is worth building.**

---

## What is needed to unblock this

**Registration is a human step and must be done by the account owner.** It
involves creating an account and handling a client secret, which is not
something this project's tooling should do on the user's behalf.

1. Register at <https://developer.kroger.com> and create an application.
2. Note the **`client_id`** and **`client_secret`**.
3. Store them the way the Whole Foods session is stored — in the user-data dir
   (`gplan db-path`), **never in the repo**:

   ```json
   // <user-data-dir>/kroger_credentials.json
   { "client_id": "...", "client_secret": "..." }
   ```

4. Confirm the **Terms of Service** accepted at registration permit this use —
   a personal, low-volume, non-commercial nutrition planner. This is the whole
   reason Kroger is attractive over Flipp/DataDome, so it is worth actually
   reading rather than assuming.

Until then this ticket cannot progress past documentation.

## Recommendation

**Pursue it, but verify before building.** The prize is 938 deals and the only
compliant source we have found; the risk is that HT simply is not in the
catalog, and that risk is resolvable in a single API call. Do not write a
`scrapers/harristeeter_api.py` until step 3 returns an HT store.

If HT is **not** in the catalog, this is a dead end of the same kind as Food
Lion, and the honest next move is to accept that HT contributes ad copy only and
concentrate on widening Whole Foods coverage instead — it is the only source
that has ever moved the number.
