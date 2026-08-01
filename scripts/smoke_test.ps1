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
$pass = 0; $fail = 0; $failed = @()
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

Write-Host "`n=== gplan smoke test ===`n" -ForegroundColor Cyan
try {
    Test-Case "version"                  @("version")                                      0 "grocery-planner"
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
    Test-Case "scrape guard (unimpl)"    @("scrape", "wholefoods")                         2
    Test-Case "unknown store error"      @("list", "deals", "-s", "bogus")                 1

    if ($IncludeScrape) {
        Test-Case "live scrape foodlion" @("scrape", "foodlion")                           0 "deals"
    }
}
finally {
    if (-not $KeepData) {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
        Remove-Item Env:\GROCERY_PLANNER_DB -ErrorAction SilentlyContinue
    } else {
        Write-Host "`nKept workspace: $work" -ForegroundColor Yellow
    }
}

Write-Host "`n=== $pass passed, $fail failed ===" -ForegroundColor Cyan
if ($fail -gt 0) { Write-Host ("Failed: " + ($failed -join ", ")) -ForegroundColor Red; exit 1 }
exit 0
