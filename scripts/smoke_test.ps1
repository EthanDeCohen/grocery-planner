<#
.SYNOPSIS
    End-to-end smoke test for the `gplan` CLI. Exercises every command against
    generated sample data in an isolated, throwaway database.

.DESCRIPTION
    Self-contained: creates its own data/<store>/*.csv and a temp SQLite DB, so
    it depends on nothing in your real data dir and runs anywhere (incl. CI).
    Exits non-zero if any case fails.

.PARAMETER IncludeScrape
    Also run the live Food Lion scrape (hits the network). Off by default.

.PARAMETER KeepData
    Keep the temp data/DB folder for inspection instead of deleting it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
    powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -IncludeScrape
#>
param(
    [switch]$IncludeScrape,
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"

# --- Resolve the gplan command (prefer repo venv, else PATH) ---
$venvGplan = Join-Path $PSScriptRoot "..\.venv\Scripts\gplan.exe"
$gplan = if (Test-Path $venvGplan) { (Resolve-Path $venvGplan).Path } else { "gplan" }
Write-Host "Using gplan: $gplan" -ForegroundColor Cyan

# --- Throwaway workspace ---
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("gplan_smoke_" + [Guid]::NewGuid().ToString("N").Substring(0,8))
$dataDir = Join-Path $work "data"
$env:GROCERY_PLANNER_DB = Join-Path $work "smoke.sqlite3"
# GFP-91: the config file too, now that `gplan config set` can WRITE it. The DB
# has been isolated since the start; without the same treatment here, running
# the smoke test would overwrite the developer's own ZIP code.
$previousConfig = $env:GROCERY_PLANNER_CONFIG
$env:GROCERY_PLANNER_CONFIG = Join-Path $work "config.json"

$dealsHeader  = "item_name,sub_category,deal_type,deal_description,regular_price,sale_price,discount_amount,discount_percent,valid_from,valid_to,loyalty_required,notes"
$pricesHeader = "item_name,brand,category,regular_price,sale_price,unit,price_per_unit,on_sale,loyalty_required,date_collected,notes"

$sample = @{
    foodlion = @{
        deals = @(
            "Boneless Chicken Breast,Meat & Seafood,Weekly Ad,`$1.99/lb,,1.99,,,2026-06-10,2026-06-16,Y,",
            "Gala Apples,Produce,Weekly Ad,`$0.99/lb,,0.99,,,2026-06-10,2026-06-16,Y,",
            "Mystery Flyer Item,Weekly Ad Feature (price not listed),Weekly Ad (price not listed),Weekly ad item,,,,,2026-06-10,2026-06-16,Y,price_missing=true"
        )
        prices = @("Whole Milk,Food Lion,Dairy,3.49,,gallon,3.49,N,N,2026-06-10,")
    }
    wholefoods = @{
        deals  = @("Wild Salmon,Meat & Seafood,Weekly Ad,`$9.99/lb,,9.99,,,2026-06-10,2026-06-16,N,")
        prices = @("Organic Eggs,365,Dairy,4.99,3.99,dozen,3.99,Y,N,2026-06-10,on sale")
    }
    harristeeter = @{
        deals  = @("Ribeye Steak,Meat & Seafood,Weekly Ad,`$8.99/lb,,8.99,,,2026-06-10,2026-06-16,Y,")
        prices = @("Greek Yogurt,HT,Dairy,1.29,,each,1.29,N,N,2026-06-10,")
    }
}

foreach ($store in $sample.Keys) {
    $folder = Join-Path $dataDir $store
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    ($dealsHeader,  $sample[$store].deals)  | ForEach-Object { $_ } | Set-Content (Join-Path $folder "deals.csv")  -Encoding utf8
    ($pricesHeader, $sample[$store].prices) | ForEach-Object { $_ } | Set-Content (Join-Path $folder "prices.csv") -Encoding utf8
}

# --- Test harness ---
$pass = 0; $fail = 0; $skip = 0; $failed = @()
function Test-Case {
    param([string]$Name, [string[]]$CmdArgs, [int]$ExpectedExit = 0, [string]$Contains)
    # Native tools write to stderr on guard/error paths; don't let that throw.
    $ErrorActionPreference = "Continue"
    $out  = (& $gplan @CmdArgs 2>&1 | Out-String)
    $code = $LASTEXITCODE
    $ok = ($code -eq $ExpectedExit)
    if ($ok -and $Contains) { $ok = ($out -match [regex]::Escape($Contains)) }
    if ($ok) {
        $script:pass++; Write-Host ("  PASS  " + $Name) -ForegroundColor Green
    } else {
        $script:fail++; $script:failed += $Name
        Write-Host ("  FAIL  " + $Name + "  (exit=$code, expected=$ExpectedExit)") -ForegroundColor Red
        Write-Host ($out.Trim()) -ForegroundColor DarkGray
    }
}
function Skip-Case {
    # GFP-4: a registered scraper can be "not ready" (needs manual, out-of-
    # band setup -- e.g. Whole Foods' hand-minted session cookie) rather than
    # unimplemented. A store that requires that kind of setup must never make
    # this smoke test (and therefore CI, on every fresh runner with no such
    # setup) red -- it's skipped and called out here, not silently ignored.
    param([string]$Name, [string]$Reason)
    $script:skip++
    Write-Host ("  SKIP  " + $Name + "  (" + $Reason + ")") -ForegroundColor Yellow
}

Write-Host "`n=== gplan smoke test ===`n" -ForegroundColor Cyan

# GFP-4: is Whole Foods actually configured on THIS machine (a hand-minted
# wholefoods_session.json)? Every fresh CI runner answers "no" -- that must
# not be a failure, just a fact the store-specific cases below branch on.
$storesProbe = (& $gplan stores 2>&1 | Out-String)
$wholefoodsReady = -not ($storesProbe -match "(?m)^wholefoods\b.*needs setup")

try {
    Test-Case "version"                  @("version")                                      0 "Protein Ledger"
    Test-Case "db-path"                  @("db-path")                                      0
    Test-Case "stores (empty)"           @("stores")                                       0 "Food Lion"
    Test-Case "import sample data"       @("import", $dataDir)                             0 "Imported"
    Test-Case "stores (after import)"    @("stores")                                       0 "foodlion"
    Test-Case "list deals"               @("list", "deals")                                0 "Chicken"
    Test-Case "list deals --on-sale"     @("list", "deals", "--on-sale", "-s", "foodlion") 0
    Test-Case "list deals --limit 0"     @("list", "deals", "--limit", "0")                0
    # GFP-16: the sample deals ended 2026-06-16, so they must read as expired.
    Test-Case "list deals (expired mark)" @("list", "deals", "-s", "foodlion")             0 "(expired)"
    Test-Case "list deals --hide-expired" @("list", "deals", "--hide-expired")             0 "0 shown of 0"
    # GFP-17: filter flags shared with the GUI controls.
    Test-Case "list deals --search"      @("list", "deals", "--search", "chicken")         0 "Boneless Chicken Breast"
    Test-Case "list deals --category"    @("list", "deals", "-c", "Produce", "-n", "0")    0 "Gala Apples"
    Test-Case "list deals --type coupon" @("list", "deals", "-t", "coupon")                0 "0 shown of 0"
    Test-Case "list deals --valid-on"    @("list", "deals", "--valid-on", "2026-06-12")    0 "Chicken"
    Test-Case "list deals bad --type"    @("list", "deals", "-t", "nonsense")              1
    Test-Case "categories"               @("categories", "-s", "foodlion")                 0 "Produce"
    Test-Case "list prices"              @("list", "prices")                               0
    Test-Case "list prices -s wholefoods" @("list", "prices", "-s", "wholefoods")          0 "Organic Eggs"
    Test-Case "profile set"              @("profile", "set", "weight", "82")               0
    Test-Case "profile list"             @("profile", "list")                              0 "weight"
    Test-Case "formula set"              @("formula", "set", "target_protein", "weight * 1.6") 0
    Test-Case "formula eval (profile)"   @("formula", "eval", "target_protein")            0 "131.2"
    Test-Case "formula eval (override)"  @("formula", "eval", "target_protein", "--var", "weight=120") 0 "192"
    Test-Case "formula list"             @("formula", "list")                              0 "target_protein"
    # GFP-8: value ranking. Sample deals carry no sizes, so nothing is comparable
    # — the command must say so rather than print an empty table.
    # GFP-11: CSV export (the GUI's Export button drives the same service call).
    $exportPath = Join-Path $work "export.csv"
    Test-Case "export csv"               @("export", $exportPath, "--include-expired")     0 "Wrote"
    Test-Case "export -c Produce"        @("export", $exportPath, "-c", "Produce", "--include-expired") 0 "Wrote"
    Test-Case "best (all expired)"       @("best")                                         0 "0 ranked of 0"
    Test-Case "best (no sizes)"          @("best", "--include-expired")                    0 "no readable size"
    Test-Case "best --unit oz"           @("best", "-u", "oz", "--include-expired")        0
    Test-Case "best bad formula"         @("best", "--score", "nope")                      1
    # GFP-7: schedules + job history (no waiting; --once only catches up).
    Test-Case "schedule set"             @("schedule", "set", "foodlion", "--every", "12h") 0 "every 12h"
    Test-Case "schedule list"            @("schedule", "list")                             0 "foodlion"
    Test-Case "schedule bad cadence"     @("schedule", "set", "foodlion", "--every", "soon") 1
    # GFP-4: wholefoods is a REGISTERED scraper that needs a hand-minted
    # session cookie before it's usable (scrapers/wholefoods.py). On a fresh
    # machine (no cookie) both of these must be rejected up front, cleanly --
    # that's a real, deterministic assertion, not a guess about the future.
    # On a machine that HAS been configured, forcing that same rejection
    # would itself be wrong (and forcing a live network scrape here would be
    # its own bug -- this smoke test is offline by default) -- skip instead
    # of failing either way.
    if ($wholefoodsReady) {
        Skip-Case "schedule unscrapable (wholefoods)" "session cookie already configured on this machine"
        Skip-Case "scrape guard (wholefoods)"         "session cookie already configured on this machine"
    } else {
        Test-Case "schedule unscrapable" @("schedule", "set", "wholefoods", "--every", "6h") 2 "not ready"
        Test-Case "scrape guard (needs setup)" @("scrape", "wholefoods")                    2 "session cookie"
    }
    Test-Case "jobs (empty)"             @("jobs")                                         0
    # GFP-75: durable records. A fresh throwaway DB has no price_history at
    # all, so the empty case must say so rather than print a bare table --
    # and --backfill on nothing must be a clean no-op, not a crash.
    Test-Case "records (empty)"          @("records")                                      0 "No records yet"
    Test-Case "records --backfill (none)" @("records", "--backfill")                       0 "Backfilled 0"
    # GFP-40: same shape for trends -- an empty window is a normal early state,
    # so it explains itself and exits 0. An unscoped price series is the one
    # thing the command refuses outright, because the number would be junk.
    Test-Case "trends (empty)"           @("trends")                                       0 "No protein prices"
    Test-Case "trends by food (empty)"   @("trends", "--by", "food")                       0 "No protein prices"
    Test-Case "trends price unscoped"    @("trends", "--metric", "price")                  2 "--food"
    Test-Case "trends bad metric"        @("trends", "--metric", "bogus")                  2 "unknown metric"
    # GFP-106: the curated seed catalog is always present, so classification has
    # real work to do even on a throwaway DB, and re-running must be a no-op.
    Test-Case "nutrition classify"       @("nutrition", "classify")                        0 "foods:"
    Test-Case "nutrition classify again" @("nutrition", "classify")                        0 "Nothing new to classify"
    Test-Case "nutrition classify show"  @("nutrition", "classify", "--show", "chicken")   0 "chicken"
    # GFP-107: the imported sample deals carry no parseable size, so nothing is
    # rankable -- which must be a plain sentence and exit 0, not an empty table.
    Test-Case "cheapest (nothing rankable)" @("cheapest")                                  0 "Nothing to rank yet"
    Test-Case "cheapest --all-protein"   @("cheapest", "--all-protein")                    0
    # GFP-33: client CRUD, the same service calls the GUI roster makes. The
    # weight cases are the point: 150 entered as POUNDS must come back as
    # pounds and produce a ~109 g/day target, not the ~240 a kilogram reading
    # would invent. A weight with no unit is refused outright rather than
    # defaulted to either one.
    Test-Case "client add"               @("client", "add", "Ana Ruiz", "-w", "150", "-u", "lb") 0 "Added Ana Ruiz"
    Test-Case "client add (no unit)"     @("client", "add", "No Unit", "--weight", "150")  1
    Test-Case "client add (weightless)"  @("client", "add", "Dev Patel")                   0 "Added Dev Patel"
    Test-Case "client list"              @("client", "list")                               0 "150 lb"
    Test-Case "client show"              @("client", "show", "Ana Ruiz")                   0 "150 lb"
    # "Absent stays absent": no weight on file means no target, said out loud.
    Test-Case "client show (no weight)"  @("client", "show", "Dev Patel")                  0 "no protein target"
    # No --unit, so the pounds already on file are what 145 is read in.
    Test-Case "client edit weight"       @("client", "edit", "Ana Ruiz", "--weight", "145") 0 "145 lb"
    Test-Case "client edit rename"       @("client", "edit", "Ana Ruiz", "--name", "Ana Ruiz-Mendez") 0 "Updated Ana Ruiz-Mendez"
    Test-Case "client edit (bad unit)"   @("client", "edit", "Ana Ruiz-Mendez", "-w", "70", "-u", "stone") 2
    # Client records are hand-typed and irreplaceable: an unconfirmed delete
    # must remove nothing (here stdin is closed, so the prompt aborts), and a
    # confirmed one must still be recoverable.
    Test-Case "client delete (no confirm)" @("client", "delete", "Dev Patel")              1
    Test-Case "client survived it"       @("client", "list")                               0 "Dev Patel"
    Test-Case "client delete"            @("client", "delete", "Dev Patel", "--yes")       0 "Removed Dev Patel"
    Test-Case "client restore"           @("client", "restore", "Dev Patel")               0 "Restored Dev Patel"
    Test-Case "client delete (unknown)"  @("client", "delete", "Nobody At All", "--yes")   1
    Test-Case "schedule remove"          @("schedule", "remove", "foodlion")               0 "Removed"
    Test-Case "schedule remove (gone)"   @("schedule", "remove", "foodlion")               1
    Test-Case "schedule run (none set)"  @("schedule", "run", "--once")                    1 "No schedules"
    Test-Case "unknown store error"      @("list", "deals", "-s", "bogus")                 1
    # GFP-91: the first command the installer tells a brand-new user to run.
    # Exercised against the real binary because that is the form they run it in.
    Test-Case "config set"               @("config", "set", "postal_code", "10001")        0 "postal_code = 10001"
    Test-Case "config took effect"       @("config")                                       0 "10001"
    Test-Case "config set (bad value)"   @("config", "set", "postal_code", "banana")       1 "5-digit"
    Test-Case "config set (bad key)"     @("config", "set", "zip", "10001")                1 "no setting called"
    Test-Case "config survived it"       @("config")                                       0 "10001"
    # GFP-102. Deliberately only the read-only and dry-run forms: registering a
    # real Scheduled Task is a machine-wide side effect, and a smoke test that
    # a developer runs twenty times a day is the wrong place for one. CI
    # registers it for real on a throwaway runner.
    Test-Case "timer status"             @("timer", "status")                              0 "ProteinLedger"
    Test-Case "timer install --dry-run"  @("timer", "install", "--dry-run")                0 "DAILY"
    Test-Case "timer remove --dry-run"   @("timer", "remove", "--dry-run")                 0 "Delete"
    Test-Case "timer bad time"           @("timer", "install", "--at", "99:99")            1 "expected HH:MM"
    Test-Case "uninstall-plan"           @("uninstall-plan")                               0 "directory"

    if ($IncludeScrape) {
        Test-Case "live scrape foodlion" @("scrape", "foodlion")                           0 "deals"
    }
}
finally {
    if ($previousConfig) { $env:GROCERY_PLANNER_CONFIG = $previousConfig }
    else { Remove-Item Env:\GROCERY_PLANNER_CONFIG -ErrorAction SilentlyContinue }
    if (-not $KeepData) {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
        Remove-Item Env:\GROCERY_PLANNER_DB -ErrorAction SilentlyContinue
    } else {
        Write-Host "`nKept workspace: $work" -ForegroundColor Yellow
    }
}

Write-Host "`n=== $pass passed, $fail failed, $skip skipped ===" -ForegroundColor Cyan
if ($fail -gt 0) { Write-Host ("Failed: " + ($failed -join ", ")) -ForegroundColor Red; exit 1 }
exit 0
