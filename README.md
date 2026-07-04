# Grocery Planner

A local-first command-line tool for scraping, storing, and comparing grocery
deals and prices across stores in the Greensboro, NC area (ZIP 27401). Everything
runs on your machine against a single-file SQLite database — no server, no
account, no cloud.

The runtime is the **`gplan`** CLI (the `grocery_planner` Python package). Store
weekly ads and digital coupons are fetched from the Flipp/Wishabi flyer API;
results are normalized into a `deals` table you can query, filter, and feed into
your own savings formulas.

> Excel was the original runtime and has been retired — see the git history if
> you need the old VBA workbook and template builders.

---

## How it works

```
┌────────────────────┐    gplan scrape     ┌──────────────────────┐    gplan list / formula
│  Flipp flyer API    │  ───────────────▶   │  SQLite (one file)   │  ───────────────▶  you
│  (weekly ads +      │    gplan import     │  deals · prices ·    │    query, filter,
│   digital coupons)  │  ──── CSVs ────▶    │  profile · formulas  │    compare, evaluate
└────────────────────┘                     └──────────────────────┘
```

1. **Collect** — `gplan scrape <store>` pulls a store's active weekly ad plus
   grocery digital coupons and writes normalized rows into the database.
   `gplan import` loads CSVs (`data/<store>/{prices,deals}.csv`) instead.
2. **Store** — one SQLite file in your user-data dir (`gplan db-path`). Disposable:
   delete that one folder and everything is gone.
3. **Compare** — `gplan list deals|prices` to browse/filter; `gplan formula` to
   evaluate your own savings/nutrition expressions against `gplan profile` values.

### Stores

| Store         | Key            | Scraper                        |
|---------------|----------------|--------------------------------|
| Food Lion     | `foodlion`     | ✅ weekly ad + digital coupons |
| Harris Teeter | `harristeeter` | ✅ weekly ad + digital coupons |
| Whole Foods   | `wholefoods`   | ⏳ manual CSV for now (GFP-4)  |

The Flipp dependency (undocumented, unauthenticated `flippback.com` endpoints —
no license fee, but ToS/breakage/rate-limit risk) is isolated in one place:
`grocery_planner/scrapers/base.py`. Adding a Flipp-backed store is a thin module
plus a registry entry (see `foodlion.py` / `harristeeter.py`).

---

## Install

Requires Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Then run via `.venv\Scripts\gplan.exe` (or activate the venv and use `gplan`).

> On Windows the `gplan` name is used deliberately — `gp` is a built-in
> PowerShell alias for `Get-ItemProperty`.

---

## Commands

```
gplan scrape <store> [-z ZIP]      fetch fresh weekly ad + coupons into the DB
gplan import [DATA_DIR]            load data/<store>/{prices,deals}.csv into the DB
gplan list deals  [-s STORE] [-n N] [--on-sale]
gplan list prices [-s STORE] [-n N]
gplan stores                      tracked stores + row counts
gplan db-path                     print the SQLite database path
gplan formula set NAME EXPR [--desc TEXT]
gplan formula list
gplan formula eval NAME [-v key=value ...]
gplan profile set KEY VALUE
gplan profile list
gplan version
```

Examples:

```powershell
gplan scrape foodlion                     # Food Lion weekly ad + coupons
gplan scrape harristeeter -z 27401
gplan list deals -s foodlion --on-sale -n 30
gplan profile set weight 82
gplan formula set target_protein "weight * 1.6" --desc "grams/day"
gplan formula eval target_protein         # uses profile[weight]
gplan formula eval target_protein -v weight=120
```

Formulas are evaluated with `simpleeval` (a safe expression evaluator — no raw
`eval`), with `gplan profile` values available as variables.

---

## Data model

One SQLite file (`gplan db-path`) with tables `stores`, `deals`, `prices`,
`profile`, `formulas`, and `scraping_jobs`. The `deals` and `prices` schemas
mirror the CSV layout below, so imports are loss-less.

### `data/<store>/deals.csv`

Weekly promotions, BOGO offers, digital coupons.

| Column | Description |
|--------|-------------|
| `item_name` | Product name |
| `sub_category` | Item grouping (e.g. Meat & Seafood); no-price rows get promo labels |
| `deal_type` | Weekly Ad, Weekly Ad (price not listed), Bogo, Digital Coupon, Percent Off Coupon |
| `deal_description` | Human-readable deal text |
| `regular_price` | Pre-deal price |
| `sale_price` | Deal price |
| `dollar_price` | One comparable numeric price (from a field or parsed from the text) |
| `discount_amount` | Dollar savings (coupons) |
| `discount_percent` | Percent savings (coupons) |
| `valid_from` / `valid_to` | Deal window |
| `loyalty_required` | Y/N (MVP, VIC card, etc.) |
| `notes` | Provenance tags (source, flyer/coupon id, loyalty) |

### `data/<store>/prices.csv`

Regular/sale shelf pricing for individual items.

| Column | Description |
|--------|-------------|
| `item_name` | Product name |
| `brand` | Brand (optional) |
| `category` | e.g. Meat, Dairy, Produce |
| `regular_price` | Shelf price |
| `sale_price` | Promotional price (if on sale) |
| `unit` | lb, each, dozen, gallon, etc. |
| `price_per_unit` | Normalized price for comparison |
| `on_sale` | Y/N |
| `loyalty_required` | Y/N |
| `date_collected` | ISO date collected |
| `notes` | Free text |

Re-importing or re-scraping a store replaces that store's prior rows for the same
source, so the commands are idempotent. `data/` is gitignored (personal data).

---

## Development

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"

pytest                                                   # offline unit tests
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1   # e2e CLI smoke test
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -IncludeScrape  # + live network
```

CI (`.github/workflows/ci.yml`) runs `pytest` and the smoke test on
`windows-latest` for Python 3.11 and 3.13 on every push/PR. Tests ship with every
change and CI must be green to merge.

Contribution and Jira-tracking workflow: see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Planned work

- **GFP-4 Whole Foods** — identify a data source (likely not Flipp).
- **GFP-5 shelf prices** — optional `prices.csv` writer for non-flyer prices.
- **GFP-7 scheduled refresh** — background/resumable weekly scrape jobs.
- **GFP-8 savings logic** — best-deal ranking, cost-per-gram protein, etc.
- **GFP-10 packaged binary**, **GFP-11 desktop GUI**.
