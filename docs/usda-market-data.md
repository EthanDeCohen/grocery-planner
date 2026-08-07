# USDA agricultural market data — epic GFP-215

Filed 2026-08-07. Index of the 15 tickets under
[GFP-215](https://decohen-partners.atlassian.net/browse/GFP-215).

## What this is, and what it is not

Today the optimiser only knows what a *retailer* charges this week. It has no
idea whether $4.99/lb chicken breast is a genuine deal or the new normal. USDA
Agricultural Marketing Service publishes free, official, **mandatory-reported**
wholesale prices for exactly the commodities this product cares about.

That is a **benchmark** series, not a shelf price. Everything below exists to
answer, in order: what is really in these APIs → does the signal change a
client's plan → if so, how do we use it without breaking our invariants.

**Not** USDA FoodData Central. That is nutrition, already ingested under GFP-24
and shipped as `grocery_planner/usda.py`. This epic is the *market price* side.

## Sources

| source | auth | notes |
| --- | --- | --- |
| [MyMarketNews / MARS](https://mymarketnews.ams.usda.gov/) | free key, basic auth with key as username | `https://marsapi.ams.usda.gov/services/v1.2/reports` — ~5k rows unregistered, ~100k registered, some endpoints cap a request at ~180 days. [Getting started](https://mymarketnews.ams.usda.gov/mars-api/getting-started) |
| [LMPRS / MPR Datamart](https://mpr.datamart.ams.usda.gov/) | none | Livestock + Dairy mandatory reporting. History to ~April 2001. `.../ws/report/v1/cattle/LM_CT100?filter={...}` |
| [FAS Open Data](https://apps.fas.usda.gov/opendataweb/home) | key | trade/production. Evaluate then probably park — GFP-226 |
| NASS Quick Stats | key | survey-based farm-gate prices. Same — GFP-226 |

## The tickets

### The gate

| key | |
| --- | --- |
| GFP-216 | SPIKE: register a MARS key, inventory every report covering beef, chicken, eggs, milk, cheese, pork |
| GFP-217 | SPIKE: LMPRS vs MARS — what does mandatory reporting add, and is an LMPRS client throwaway work? |
| **GFP-218** | **DECISION: does USDA wholesale data actually change a client's plan?** Backtest against our own retail history, must beat a naive baseline. **Blocks everything below.** |

GFP-216 and GFP-217 block GFP-218. A negative result on GFP-218 closes this epic
as Won't Do, and that counts as done — an evidence-backed no is the point of
running the spikes first.

### The plumbing

| key | |
| --- | --- |
| GFP-219 | Commodity series schema + migration (`db_script/migration`, per convention) |
| GFP-220 | MARS client behind the ingest seam, routed through QuotaPool (GFP-192) |
| GFP-221 | Map USDA commodity series → our food taxonomy, joining on GFP-106's protein-kind axis |
| GFP-224 | Determinism guard: snapshot to DB, no network on the solve path |
| GFP-225 | ToS, attribution, provenance — including whether one shared key may serve many nutritionists (GFP-164) |

### The payoff

| key | |
| --- | --- |
| GFP-222 | Deal-quality benchmark — is this "deal" actually good? |
| GFP-223 | Trend/seasonality signal for cost-per-gram-protein (feeds epic GFP-21) |
| GFP-226 | SPIKE: FAS + NASS, timeboxed, expected outcome is "park it" |

### The main-window redesign (decided with the user 2026-08-07)

The right-hand trends pane becomes three stacked panels:

```
TOP  50%   existing $/g-protein-by-store chart, unchanged      X = dates
MID  25%   NEW: raw USDA market price series                   X = dates (aligned to top)
BOT  25%   NEW: above/below-market dot plot                    X = commodity

  +60% |  * WF
  +40% |            * WF
  +20% |  o HT      o HT
    0% | ================================  market baseline
       +--------------------------------
          beef        chicken      eggs
```

Dot labels carry the raw dollar figure in parentheses — `beef ($2.10/lb)` on the
axis, `HT ($3.99)` on each dot — while position stays percent-only, so the
carcass-weight problem never enters the geometry and the dollars are read one at
a time rather than subtracted from each other.

| key | |
| --- | --- |
| GFP-227 | Stack the pane into three panels — **owns the height problem** |
| GFP-228 | `service`: USDA series + percent-above-market queries, with CLI parity |
| GFP-229 | Middle panel: raw USDA series on the shared date axis |
| GFP-230 | Bottom panel: who is above market, dot plot by commodity |
| GFP-231 | SPIKE: can pyqtgraph (MIT) replace hand-painted `QPainter` panels? |

## Three constraints that shape all of the above

**Units are percent, not dollars.** USDA wholesale is $/cwt on carcass or cutout
weight, which is *not* what a shopper pays per pound — the gap includes
processing, cut yield and transport, none of which is retailer margin. Plotting
both in dollars renders a ~3x gap that reads as gouging and mostly is not.
Percent-versus-baseline is unitless, puts every commodity on one comparable
axis, and "Whole Foods is 40% above market on beef" is a sentence a nutritionist
can repeat to a client without it being wrong.

**The height floor is real, and worse than it looks.** Measured off the running
widget tree on 2026-08-07, not estimated:

| | measured |
| --- | --- |
| `TrendsPane` non-chart chrome (title 16, tab bar 24, selectors 26, subtitle 32, legend 16, latest row 96) | **210px** |
| layout margins + spacing | **58px** |
| `TrendChart.setMinimumHeight` (`gui/trends.py:192`) | **220px** |
| main-window chrome outside the pane | **163px** |

So the window floor is 651px today, 871px at two panels, and **1091px at
three** — taller than a maximised window gets on a 1080p screen (~950px usable).
The app already can't honour its own default: `app.py:65` asks for 920×560 and
the layout minimums force it to 920×802.

50/25/25 therefore cannot be three widgets sharing one minimum. GFP-227 owns the
fix; the options are lowering the new panels' minimums to ~90px (floor 831px,
desktop-only), a `QSplitter` for redistribution (doesn't lower the floor by
itself), collapsing to zero when there's no USDA data (non-negotiable either
way), and falling back to a third tab below some height threshold.

**Hand-painted, unless GFP-231 says otherwise.** Qt Charts *and* Qt Graphs are
GPLv3-or-commercial while the rest of the app is LGPL, and this product gets
handed to nutritionists — both stay excluded. That is not an argument against
every library, only against Qt's own, so GFP-231 evaluates pyqtgraph (0.14.0,
MIT, OS-independent, PySide6-capable). Its cost is `numpy`: the frozen GUI is
51MB today on a deliberately lean dependency list, and the ZIP is handed out by
hand. GFP-231 must measure the real binary delta on both platforms before
anyone commits to it.

And the two that run through the whole product: the optimiser's invariant (same
inputs always produce the same plan, so USDA data is snapshotted and the solve
path never touches the network) and no PII goes up (these are anonymous public
GETs — keep them that way).
