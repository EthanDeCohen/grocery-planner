<#
.SYNOPSIS
    The local pre-merge gate (GFP-94): everything CI used to check on Windows.

.DESCRIPTION
    GitHub Actions no longer runs on push or pull_request -- it is macOS-only
    and dispatched by hand (see .github/workflows/ci.yml for why). This script
    is what replaces it. It runs the same pytest suite and the same
    scripts/smoke_test.ps1 the Windows CI jobs used to run, on the machine
    development actually happens on.

    Run it before merging. Not "run the tests at some point" -- one command,
    one exit code, so "verified locally" means something that actually ran.

    Exit code is 0 only if every stage passed, so this is safe to chain:
        ./scripts/verify.ps1; if ($?) { gh pr merge ... }

    What this does NOT cover: macOS. PyInstaller cannot cross-compile, so a
    macOS binary can only be built on a macOS runner. If your change could
    behave differently there -- paths, filesystem case-sensitivity, timezones,
    subprocess/shell invocation, the PyInstaller spec, or a new dependency --
    dispatch the workflow too:
        gh workflow run ci.yml --ref <branch>

.PARAMETER IncludeScrape
    Also run the smoke test's live-network cases (hits real store endpoints).
    Off by default so the normal path stays offline and fast.

.PARAMETER IncludeBinary
    Also build and smoke test the Windows binary via scripts/build_binary.ps1.
    Slow (PyInstaller); worth it when you touched packaging/, dependencies, or
    anything imported lazily, since a missing hidden import surfaces nowhere
    else.

.PARAMETER SkipSmoke
    Run pytest only. For a fast inner loop -- not for a pre-merge check.

.EXAMPLE
    ./scripts/verify.ps1
    ./scripts/verify.ps1 -IncludeBinary
    ./scripts/verify.ps1 -IncludeScrape -IncludeBinary
#>
[CmdletBinding()]
param(
    [switch]$IncludeScrape,
    [switch]$IncludeBinary,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

# Prefer the repo venv, same as build_binary.ps1, so verification uses the
# pinned dependency set rather than whatever `python` happens to resolve to.
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$stages = @()
$failed = @()

function Invoke-Stage {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Body
    )

    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    $started = Get-Date

    # Native executables write progress to stderr; with $ErrorActionPreference
    # = 'Stop' that would throw NativeCommandError on perfectly healthy output
    # (a gotcha already learned in smoke_test.ps1). Relax it inside the stage
    # and judge the stage by $LASTEXITCODE instead, which is the only honest
    # signal here.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Body
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }

    $elapsed = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    # Windows PowerShell 5.1 cannot parse an `if` expression in a hashtable
    # value position, so the result is computed before the literal.
    $result = "pass"
    if ($code -ne 0) {
        $result = "FAIL"
        Write-Host "FAILED ($($elapsed)s, exit $code): $Name" -ForegroundColor Red
        $script:failed += $Name
    } else {
        Write-Host "passed ($($elapsed)s): $Name" -ForegroundColor Green
    }
    $script:stages += [pscustomobject]@{ Stage = $Name; Result = $result; Seconds = $elapsed }
}

Write-Host "Verifying $root" -ForegroundColor Cyan
Write-Host "Using python: $python" -ForegroundColor Cyan

Invoke-Stage "pytest" {
    & $python -m pytest -q
}

if (-not $SkipSmoke) {
    Invoke-Stage "smoke test (CLI end-to-end)" {
        $smoke = Join-Path $PSScriptRoot "smoke_test.ps1"
        if ($IncludeScrape) {
            & $smoke -IncludeScrape
        } else {
            & $smoke
        }
    }
}

if ($IncludeBinary) {
    Invoke-Stage "Windows binary (PyInstaller)" {
        & (Join-Path $PSScriptRoot "build_binary.ps1")
    }
}

Write-Host ""
Write-Host "--- summary ---" -ForegroundColor Cyan
$stages | Format-Table -AutoSize | Out-String | Write-Host

if ($failed.Count -gt 0) {
    Write-Host "VERIFY FAILED: $($failed -join ', ')" -ForegroundColor Red
    Write-Host "Do not merge." -ForegroundColor Red
    exit 1
}

Write-Host "All local checks passed." -ForegroundColor Green
if (-not $IncludeBinary) {
    Write-Host "Note: the Windows binary was not built (-IncludeBinary)." -ForegroundColor Yellow
}
Write-Host "macOS is NOT covered here -- if this change could affect it, run:" -ForegroundColor Yellow
Write-Host "  gh workflow run ci.yml --ref $(git -C $root rev-parse --abbrev-ref HEAD 2>$null)" -ForegroundColor Yellow
exit 0
