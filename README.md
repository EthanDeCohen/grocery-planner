# Grocery Planner

Excel-based grocery price and deal planner for comparing stores in the Greensboro, NC area (ZIP 27401). Store data lives in CSV files under `data/`. Python scripts build the workbook template and (eventually) generate those CSVs from store sources. VBA macros read the CSVs and populate the Excel sheets.

**Project location:** `C:\Users\edeco\OneDrive\Desktop\groceryPlanner`

---

## How it is supposed to work (target architecture)

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│  Grocery store      │     │  data/<store>/         │     │  GroceryPlanner.xlsm    │
│  sources (ads,      │ --> │  prices.csv            │ --> │  (Excel + VBA macros)   │
│  websites, flyers)  │     │  deals.csv             │     │                         │
└─────────────────────┘     └──────────────────────┘     └─────────────────────────┘
         ^                            ^                              ^
         │                            │                              │
    Python scripts              CSV files on disk            RefreshGroceryData
    scrape / capture /          (one folder per store)       macro loads CSVs into
    parse store data                                           per-store + summary sheets
```

### Intended pipeline

1. **Collect** — Python scripts fetch or capture grocery data from each store (weekly ads, price pages, etc.).
2. **Write CSVs** — Parsed results are saved as `data/<store>/prices.csv` and `data/<store>/deals.csv`.
3. **Refresh Excel** — Open `GroceryPlanner.xlsm` in the project root and run the `RefreshGroceryData` macro. VBA reads every store CSV and rebuilds all workbook sheets.
4. **Compare** — Review per-store sheets plus combined `All Prices`, `All Deals`, and `Savings Summary`.

### Tracked stores

| Display name    | Data folder      | Prices sheet      | Deals sheet           |
|-----------------|------------------|-------------------|-----------------------|
| Whole Foods     | `wholefoods`     | Whole Foods       | Whole Foods Deals     |
| Food Lion       | `foodlion`       | Food Lion         | Food Lion Deals       |
| Harris Teeter   | `harristeeter`   | Harris Teeter     | Harris Teeter Deals   |

---

## How it currently works

The VBA import path is **fully implemented**. The Python CSV-generation path is **partially started** — Food Lion weekly ad deals can be scraped via `scripts/scrape_foodlion.py`; other stores are still manual.

### What works today

| Component | Status | Notes |
|-----------|--------|-------|
| CSV layout + sample data | Done | Example rows in `data/<store>/` |
| VBA `RefreshGroceryData` | Done | Reads CSVs into all sheets |
| Template workbook build | Done | Two methods (see below) |
| Personal workbook setup | Done | `setup_personal_workbook.ps1` |
| Food Lion deals scraper | Done | `scripts/scrape_foodlion.py` via Flipp API |
| Harris Teeter / Whole Foods scrapers | **Not done** | CSVs still manual |

### Current manual workflow

1. Run `python scripts/scrape_foodlion.py` for Food Lion deals, or edit other store CSVs by hand.
2. Ensure `GroceryPlanner.xlsm` sits in the project root next to the `data/` folder.
3. Open the workbook in Excel, press **Alt+F8**, run **`RefreshGroceryData`**.
4. VBA reloads all store sheets and combined summary sheets from disk.

---

## Directory layout

```
groceryPlanner/
├── README.md                          # This file
├── .gitignore                         # Excludes data/ and personal workbooks
├── GroceryPlanner.xlsm                # YOUR personal workbook (gitignored)
│
├── data/                              # Store CSV data (gitignored)
│   ├── wholefoods/
│   │   ├── prices.csv
│   │   └── deals.csv
│   ├── foodlion/
│   │   ├── prices.csv
│   │   └── deals.csv
│   ├── harristeeter/
│   │   ├── prices.csv
│   │   └── deals.csv
│
├── template/                          # Tracked shared template (committed)
│   ├── GroceryPlanner.template.xlsx   # Static template (no macros)
│   └── GroceryPlanner.template.xlsm   # Macro-enabled template (with VBA)
│
├── vba/                               # VBA source (committed; edit here, re-import)
│   ├── GroceryStoreConfig.cls         # Store name / folder / sheet mapping
│   ├── GroceryCsvImporter.cls         # Core CSV → sheet import logic
│   └── GroceryPlannerModule.bas       # Public macros (RefreshGroceryData, etc.)
│
└── scripts/
    ├── create_template_workbook.py    # Build .xlsx template from current CSVs (openpyxl)
    ├── build_template.py              # Build .xlsm with embedded VBA (pywin32 + Excel COM)
    ├── import_vba.ps1                 # Inject vba/ into template → .xlsm (PowerShell + Excel COM)
    ├── setup_personal_workbook.ps1    # Copy template to GroceryPlanner.xlsm
    └── scrape_foodlion.py             # Fetch Food Lion weekly ad → data/foodlion/deals.csv
```

---

## CSV file formats

Each store folder contains two files. Headers must match these columns (extra columns are fine; missing files show a placeholder message in Excel).

### `prices.csv`

Regular and sale pricing for individual items.

| Column | Description |
|--------|-------------|
| `item_name` | Product name |
| `brand` | Brand (optional) |
| `category` | e.g. Meat, Dairy, Produce |
| `regular_price` | Shelf price |
| `sale_price` | Promotional price (if on sale) |
| `unit` | lb, each, dozen, gallon, etc. |
| `price_per_unit` | Normalized price used for comparison |
| `on_sale` | Y/N |
| `loyalty_required` | Y/N (MVP, VIC card, etc.) |
| `date_collected` | ISO date when price was recorded |
| `notes` | Free text |

### `deals.csv`

Weekly promotions, BOGO offers, manager specials, etc.

| Column | Description |
|--------|-------------|
| `item_name` | Product name |
| `sub_category` | Item grouping (e.g. Meat & Seafood, Beverages). No-price flyer rows get explicit promo labels |
| `deal_type` | e.g. Weekly Ad, Weekly Ad (price not listed), Bogo |
| `deal_description` | Human-readable deal text |
| `regular_price` | Pre-deal price |
| `sale_price` | Deal price |
| `discount_amount` | Dollar savings |
| `discount_percent` | Percent savings |
| `valid_from` | Deal start date |
| `valid_to` | Deal end date |
| `loyalty_required` | Y/N |
| `notes` | Free text |

When VBA imports a CSV, it prepends two columns to every row:

- `store` — display name (e.g. "Food Lion")
- `row_type` — `"price"` or `"deal"`

---

## Excel workbook sheets

The template and personal workbook contain these sheets:

| Sheet | Contents |
|-------|----------|
| Instructions | Usage steps, last refresh timestamp (B4), refresh summary (B5) |
| Whole Foods | `data/wholefoods/prices.csv` |
| Whole Foods Deals | `data/wholefoods/deals.csv` |
| Food Lion | `data/foodlion/prices.csv` |
| Food Lion Deals | `data/foodlion/deals.csv` |
| Harris Teeter | `data/harristeeter/prices.csv` |
| Harris Teeter Deals | `data/harristeeter/deals.csv` |
| All Prices | Combined price rows from all stores |
| All Deals | Combined deal rows from all stores |
| Savings Summary | Row counts + data folder path |

---

## VBA macros

Defined in `vba/GroceryPlannerModule.bas`. After VBA is imported into the workbook, run via **Alt+F8** or assign to a button on the Instructions sheet.

### `RefreshGroceryData`

Main macro. Flow:

1. Locate `data/` relative to the workbook path (`<workbook_dir>/data` or `<workbook_dir>/../data`).
2. For each configured store (Whole Foods, Food Lion, Harris Teeter):
   - Clear the store's prices and deals sheets.
   - If the CSV exists, import via Excel `QueryTable` (comma-delimited).
   - Prepend `store` and `row_type` columns.
   - Append rows to `All Prices` or `All Deals`.
3. Format headers (bold, light blue background) and auto-fit columns.
4. Update `Savings Summary` with total row counts and data folder path.
5. Write refresh timestamp and summary to `Instructions!B4` and `Instructions!B5`.
6. Show a success or error message box.

### `OpenDataFolder`

Opens the resolved `data/` folder in Windows Explorer. Fails with a message if the workbook is unsaved or `data/` cannot be found.

### VBA source modules

- **`GroceryStoreConfig`** — Maps store display name → folder name → sheet names; builds CSV paths.
- **`GroceryCsvImporter`** — All import, combine, format, and summary logic.
- **`GroceryPlannerModule`** — Thin public entry points (`RefreshGroceryData`, `OpenDataFolder`).

---

## Python scripts

### `scripts/create_template_workbook.py` (primary template builder)

- **Purpose:** Create `template/GroceryPlanner.template.xlsx` from whatever CSVs currently exist in `data/`.
- **Dependencies:** `pandas`, `openpyxl`
- **Behavior:** Reads each store's `prices.csv` and `deals.csv`, adds `store` and `row_type` columns, writes all sheets including combined views. Does **not** embed VBA.
- **Run:**
  ```powershell
  cd C:\Users\edeco\OneDrive\Desktop\groceryPlanner
  python scripts/create_template_workbook.py
  ```

### `scripts/build_template.py` (alternative: one-step .xlsm)

- **Purpose:** Build `template/GroceryPlanner.template.xlsm` with sheets **and** embedded VBA in a single step.
- **Dependencies:** `pywin32` (requires Excel installed)
- **Behavior:** Uses Excel COM to create workbook, add sheets, inject all `vba/*.cls` and `vba/*.bas` source, save as macro-enabled.
- **Run:**
  ```powershell
  pip install pywin32
  python scripts/build_template.py
  ```

### `scripts/import_vba.ps1` (recommended VBA injection)

- **Purpose:** Take the `.xlsx` template and produce `template/GroceryPlanner.template.xlsm` with VBA modules added.
- **Requires:** Excel installed + Trust Center setting enabled (see Setup below).
- **Run:**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts/import_vba.ps1
  ```

### `scripts/setup_personal_workbook.ps1`

- **Purpose:** Copy `template/GroceryPlanner.template.xlsx` → `GroceryPlanner.xlsm` in the project root (your gitignored working copy).
- **Run:**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts/setup_personal_workbook.ps1
  ```
- **Note:** If you need macros in the personal copy, use the `.xlsm` template instead (copy manually or extend this script).

### `scripts/scrape_foodlion.py`

- **Purpose:** Fetch the active Food Lion weekly ad for your ZIP code from the public Flipp API and write `data/foodlion/deals.csv`.
- **Dependencies:** `httpx` (see `requirements.txt`)
- **No browser required** — plain HTTP only; lightweight and fast.
- **Run:**
  ```powershell
  pip install -r requirements.txt
  python scripts/scrape_foodlion.py
  python scripts/scrape_foodlion.py --postal-code 27401
  ```
- **Output:** Overwrites `data/foodlion/deals.csv` with weekly ad items (name, sale price, valid dates).
- **Then:** Open `GroceryPlanner.xlsm` → **Alt+F8** → **RefreshGroceryData**.

---

## First-time setup

### Prerequisites

- Windows with Microsoft Excel
- Python 3.x
- PowerShell

### Python packages (by task)

```powershell
pip install -r requirements.txt

# Alternative .xlsm build (also needs pywin32)
pip install pywin32
```

### Excel Trust Center (required for `import_vba.ps1` and `build_template.py`)

1. Excel → **File** → **Options** → **Trust Center** → **Trust Center Settings**
2. **Macro Settings** → check **"Trust access to the VBA project object model"**
3. Click OK and restart Excel if prompted.

### Build template and personal workbook

```powershell
cd C:\Users\edeco\OneDrive\Desktop\groceryPlanner

# Step 1: Create .xlsx template preloaded from sample CSVs
python scripts/create_template_workbook.py

# Step 2: Inject VBA → macro-enabled template
powershell -ExecutionPolicy Bypass -File scripts/import_vba.ps1

# Step 3: Create your personal workbook copy
powershell -ExecutionPolicy Bypass -File scripts/setup_personal_workbook.ps1
```

If `setup_personal_workbook.ps1` copies the `.xlsx` (no macros), either:

- Manually copy `template/GroceryPlanner.template.xlsm` to `GroceryPlanner.xlsm`, or
- Re-run setup after pointing it at the `.xlsm` template.

### Daily use

1. Update CSVs in `data/<store>/` (manually today; via Python scripts in the future).
2. Open `GroceryPlanner.xlsm` from the project root (must be saved — unsaved workbooks have no path, so VBA cannot find `data/`).
3. **Alt+F8** → **RefreshGroceryData**.
4. Review sheets.

---

## Git strategy

Git is initialized in this project. The repo tracks source code and templates; it does **not** track personal data.

**Workflow:** All changes go through feature branches and pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md). The `main` branch is protected — no direct pushes.

### Tracked (committed)

- `README.md`
- `vba/`
- `scripts/`
- `template/` (shared templates)
- `.gitignore`

### Gitignored (local only)

- `data/` — store CSVs (personal shopping data)
- `GroceryPlanner.xlsm` / `GroceryPlanner.xlsx` — your working workbook
- `MyGroceryPlanner.*`, `*.local.xlsm`, Excel temp files (`~$*`)

### Commit history

```
d4c1561 Add personal workbook setup script and tidy VBA importer
6f29d30 Initial grocery planner: CSV layout, VBA importer, Excel template
```

---

## Planned work (not yet built)

These are the gaps between the **target architecture** and **current state**:

1. **Harris Teeter scraper** — Same Flipp API pattern as Food Lion; write `data/harristeeter/deals.csv`.
2. **Whole Foods scraper** — Identify data source (may differ from Flipp).
3. **Food Lion prices** — Optional `prices.csv` writer for non-flyer shelf prices.
4. **Shared scraper library** — Extract common Flipp fetch/CSV code used by multiple store scripts.
4. **Scheduled refresh** — Optional Task Scheduler job to regenerate CSVs weekly, then open Excel and refresh.
5. **Savings logic** — `Savings Summary` currently shows row counts only; future: cost-per-gram protein, best-deal ranking, etc. (see `groceryInfo.txt` on Desktop for research context).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Could not locate the data folder" | Save `GroceryPlanner.xlsm` in the project root (same folder as `data/`). |
| "No CSV found at …" | Create the missing file or check the store folder name matches (`foodlion`, not `food-lion`). |
| VBA import script fails | Enable "Trust access to the VBA project object model" in Excel Trust Center. |
| Macro not visible | Open the `.xlsm` file, not `.xlsx`. Re-run `import_vba.ps1`. |
| `create_template_workbook.py` errors | `pip install pandas openpyxl` |

---

## Related files outside this repo

- `C:\Users\edeco\OneDrive\Desktop\groceryInfo.txt` — Detailed protein-sourcing and deal research for Greensboro 27401 (June 2026). Not part of the git repo; useful background for what to track in CSVs.

---

## Quick reference for future sessions

**Tell the assistant:**

> Project is at `C:\Users\edeco\OneDrive\Desktop\groceryPlanner`. CSVs in `data/<store>/` feed Excel via the `RefreshGroceryData` VBA macro. Food Lion deals scraper exists (`scripts/scrape_foodlion.py`); other stores are manual. Read `README.md` for full layout.

**Most common commands:**

```powershell
cd C:\Users\edeco\OneDrive\Desktop\groceryPlanner
python scripts/scrape_foodlion.py                   # fetch Food Lion weekly deals
python scripts/create_template_workbook.py          # rebuild .xlsx from CSVs
powershell -File scripts/import_vba.ps1             # rebuild .xlsm with macros
```

Then in Excel: **Alt+F8** → **RefreshGroceryData**.