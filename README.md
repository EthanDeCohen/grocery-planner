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
   Set a cadence with `gplan schedule set <store> --every 12h` and run
   `gplan schedule run` to keep it fresh by itself. The schedule lives in the
   database, so it survives restarts, and anything missed while the machine was
   asleep is caught up on the next start rather than skipped.
3. **Compare** — `gplan list deals|prices` to browse/filter; `gplan formula` to
   evaluate your own savings/nutrition expressions against `gplan profile` values.
   Deals whose `valid_to` has passed are marked `(expired)` rather than silently
   shown as current; `--hide-expired` (or the GUI's "Hide expired" box) drops them.
   Every filter is defined once in `service.fetch_deals()`, so a CLI flag and the
   matching GUI control always mean the same thing.

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
gplan list deals  [-s STORE] [-n N] [--on-sale] [--hide-expired]
                  [-c CATEGORY] [-t all|weekly|coupon|bogo] [-q SEARCH]
                  [--loyalty] [--valid-on YYYY-MM-DD]
gplan list prices [-s STORE] [-n N]
gplan categories  [-s STORE]      sub-categories available to --category
gplan stores                      tracked stores + row counts
gplan db-path                     print the SQLite database path
gplan schedule set STORE --every 6h | --cron "0 6 * * *"
gplan schedule list | remove STORE
gplan schedule run [--once]       background refresh (Ctrl-C to stop)
gplan jobs [-n N] [-s STORE]      history of automatic scrape runs
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
gplan list deals --hide-expired           # only deals still valid today
gplan list deals -q chicken -t weekly     # search the weekly ad
gplan list deals -c "Meat & Seafood" --on-sale
gplan profile set weight 82
gplan formula set target_protein "weight * 1.6" --desc "grams/day"
gplan formula eval target_protein         # uses profile[weight]
gplan formula eval target_protein -v weight=120
```

Formulas are evaluated with `simpleeval` (a safe expression evaluator — no raw
`eval`), with `gplan profile` values available as variables.

---

## Desktop GUI (preview)

An early cross-platform (Windows + macOS) desktop shell is available behind the
optional `gui` extra — a minimal window to pick a store, run a scrape, and browse
the resulting deals without touching the CLI. It drives the same
`grocery_planner.service` core as `gplan`.

```powershell
.venv\Scripts\python -m pip install -e ".[gui]"     # installs PySide6 (Qt)
.venv\Scripts\python -m grocery_planner.gui          # or: gplan-gui
```

This is the MVP shell (GFP-14); the full GUI — formula editor, schedule settings,
per-job progress, Export to Excel — is GFP-11.

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
- **GFP-10 packaged binary** — single-file `.exe` / `.app` (PyInstaller).
- **GFP-11 desktop GUI** — full-featured GUI on top of the GFP-14 preview shell.
