<#
.SYNOPSIS
    Build the standalone gplan binaries with PyInstaller (GFP-10).

.DESCRIPTION
    Produces dist/gplan.exe (CLI) and optionally dist/gplan-gui.exe, then smoke
    tests whatever it built against a throwaway database. A binary that builds
    but cannot run is not a successful build, so the check is part of the script
    rather than a thing to remember afterwards.

.PARAMETER IncludeGui
    Also build the desktop app. Needs the `gui` extra (PySide6) and takes
    noticeably longer; the result is ~3x the size because it carries Qt.

.PARAMETER SkipTest
    Build only, skip the smoke test.

.EXAMPLE
    ./scripts/build_binary.ps1
    ./scripts/build_binary.ps1 -IncludeGui
#>
[CmdletBinding()]
param(
    [switch]$IncludeGui,
    [switch]$SkipTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$packaging = Join-Path $root "packaging"
$dist = Join-Path $root "dist"

# Prefer the repo venv so the build uses the pinned dependency set.
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
Write-Host "Using python: $python" -ForegroundColor Cyan

& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller is missing. Install it with:" -ForegroundColor Red
    Write-Host "  $python -m pip install -e `".[build]`"" -ForegroundColor Yellow
    exit 1
}

$specs = @("gplan.spec")
if ($IncludeGui) { $specs += "gplan-gui.spec" }

foreach ($spec in $specs) {
    Write-Host "`n=== Building $spec ===" -ForegroundColor Cyan
    Push-Location $packaging
    try {
        # PyInstaller logs INFO to stderr. With $ErrorActionPreference='Stop'
        # PowerShell turns that into a NativeCommandError and aborts a build
        # that was going fine, so judge it by its exit code instead.
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $python -m PyInstaller $spec --noconfirm --distpath $dist --workpath (Join-Path $root "build")
        $code = $LASTEXITCODE
        $ErrorActionPreference = $previousPreference
        if ($code -ne 0) { throw "PyInstaller failed for $spec (exit $code)" }
    } finally {
        Pop-Location
    }
}

# GFP-320: one-directory builds, so report the folder totals rather than a
# single file size. An empty-looking folder here is a real failure mode -- a
# COLLECT that dropped its payload leaves a launcher that fails on first run.
Get-ChildItem $dist -Directory | ForEach-Object {
    $files = Get-ChildItem $_.FullName -Recurse -File
    Write-Host ("  {0,-18} {1,8:N1} MB across {2} files" -f `
        $_.Name, (($files | Measure-Object Length -Sum).Sum / 1MB), $files.Count) -ForegroundColor Green
}

if ($SkipTest) { Write-Host "`nSkipping smoke test (-SkipTest)."; exit 0 }

# --- Smoke test the CLI binary against a throwaway DB ---------------------- #
Write-Host "`n=== Smoke testing dist/gplan ===" -ForegroundColor Cyan
$exe = Join-Path $dist "gplan\gplan.exe"
if (-not (Test-Path $exe)) { $exe = Join-Path $dist "gplan\gplan" }
if (-not (Test-Path $exe)) {
    Write-Host "No launcher at $exe -- the build produced no runnable binary." -ForegroundColor Red
    exit 1
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("gplan_bin_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$previousDb = $env:GROCERY_PLANNER_DB
$env:GROCERY_PLANNER_DB = Join-Path $work "binary.sqlite3"

$pass = 0; $fail = 0
function Test-Binary {
    param([string]$Name, [string[]]$CmdArgs, [string]$Contains)
    $ErrorActionPreference = "Continue"
    $out = (& $exe @CmdArgs 2>&1 | Out-String)
    $ok = ($LASTEXITCODE -eq 0)
    if ($ok -and $Contains) { $ok = ($out -match [regex]::Escape($Contains)) }
    if ($ok) { $script:pass++; Write-Host "  PASS  $Name" -ForegroundColor Green }
    else {
        $script:fail++
        Write-Host "  FAIL  $Name (exit=$LASTEXITCODE)" -ForegroundColor Red
        Write-Host $out.Trim() -ForegroundColor DarkGray
    }
}

try {
    Test-Binary "version"        @("version")                                  "grocery-planner"
    Test-Binary "db-path"        @("db-path")                                  "binary.sqlite3"
    Test-Binary "stores"         @("stores")                                   "Food Lion"
    Test-Binary "list (empty)"   @("list", "deals")
    # The scheduler is the real risk: APScheduler resolves triggers by name, so
    # a missing hidden import only shows up here, never at build time.
    Test-Binary "schedule set"   @("schedule", "set", "foodlion", "--every", "6h") "every 6h"
    Test-Binary "schedule list"  @("schedule", "list")                         "foodlion"
    Test-Binary "jobs"           @("jobs")
    Test-Binary "best"           @("best")
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    if ($previousDb) { $env:GROCERY_PLANNER_DB = $previousDb }
    else { Remove-Item Env:\GROCERY_PLANNER_DB -ErrorAction SilentlyContinue }
}

Write-Host "`n=== $pass passed, $fail failed ===" -ForegroundColor $(if ($fail) { "Red" } else { "Green" })
if ($fail) { exit 1 }
