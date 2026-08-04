<#
.SYNOPSIS
    Install Protein Ledger for the current user on Windows (GFP-91).

.DESCRIPTION
    There was no install story before this beyond a Python dev setup, which is
    not something a nutritionist can follow. This script turns the contents of a
    release ZIP into an installed application.

    THREE DECISIONS WORTH KNOWING ABOUT, because they are not the obvious ones:

    1. PER-USER, NO ADMIN, NO UAC PROMPT. Everything goes under
       %LOCALAPPDATA%, the PATH entry is the *user* PATH, and the Add/Remove
       Programs entry is under HKCU. A nutritionist on a work laptop may simply
       not have an administrator password, and an installer they cannot run is
       not an installer. The cost is that this installs for one user rather
       than the machine, which for a single-seat desktop tool is the right
       trade.

    2. NO THIRD-PARTY INSTALLER TOOLCHAIN. No Inno Setup, no NSIS, no WiX.
       Those would each need installing on the build machine and in CI, and
       they buy very little for a per-user copy-and-shortcut install. A script
       is also the only form that is directly readable by whoever is about to
       run it, which matters more than usual for software downloaded from a
       GitHub release page.

    3. IT WRITES A MANIFEST, and the uninstaller (GFP-92) reads it rather than
       guessing. An uninstaller that assumes default locations silently leaves
       files behind on any install that used -Prefix, and leaving files behind
       here means leaving CREDENTIALS behind (see GFP-102's checklist).

    IDEMPOTENT, as the ticket requires: running it twice installs the same
    thing twice and changes nothing the second time. Every step is written as
    "make it so", not "add one" -- the PATH entry is checked before being
    appended, the shortcut is overwritten, the registry values are set.

.PARAMETER Prefix
    Install somewhere other than %LOCALAPPDATA%\Programs\ProteinLedger.
    Mainly for testing an install without touching a real one.

.PARAMETER NoTimer
    Do not register the daily background refresh (GFP-102). The app still
    works; it just will not update prices while it is closed.

.PARAMETER NoIntegrate
    Copy the files but do not touch PATH, the Start Menu, the registry or the
    background timer.
    This is what makes the script testable on a developer machine: the file
    copy is exercised for real while every machine-wide side effect is left
    alone. CI (GFP-116) runs WITHOUT this flag on a throwaway runner, which is
    where those steps get their real test.

.PARAMETER DryRun
    Print every action without performing any of it. Same spirit as
    `gplan scrape --dry-run` (GFP-87): an operation that changes a machine
    should be able to answer "what would this do?" without doing it.

.EXAMPLE
    ./install.ps1
    ./install.ps1 -DryRun
    ./install.ps1 -Prefix C:\Temp\gp-test -NoIntegrate
#>
[CmdletBinding()]
param(
    [string]$Prefix,
    [switch]$NoIntegrate,
    [switch]$NoTimer,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------- #
# Pinned identifiers.
#
# GFP-102 rule 1: these names are pinned by that ticket and MUST NOT change
# between releases. You cannot hand-remove what you cannot name, and a rename
# in v1.1 orphans every v1.0 install permanently. They are mirrored in
# grocery_planner/install_paths.py, and tests/test_installer_scripts.py fails
# if the two ever disagree.
# --------------------------------------------------------------------------- #
$AppName          = "Protein Ledger"
$InstallDirName   = "ProteinLedger"
$StartMenuFolder  = "Protein Ledger"
$RegistryKey      = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ProteinLedger"
$TaskPath         = "\ProteinLedger\"
$TaskName         = "Refresh"
$ManifestName     = "install-manifest.json"
$Publisher        = "grocery-planner"

$sourceDir = $PSScriptRoot
$installRoot = if ($Prefix) { $Prefix } else {
    Join-Path $env:LOCALAPPDATA "Programs\$InstallDirName"
}

function Write-Step { param([string]$Message) Write-Host "  $Message" -ForegroundColor Gray }
function Write-Did  { param([string]$Message) Write-Host "  [ok] $Message" -ForegroundColor Green }
function Write-Skip { param([string]$Message) Write-Host "  [--] $Message" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Message) Write-Host "  [!!] $Message" -ForegroundColor Yellow }

function Invoke-Action {
    <#  Performs $Action unless -DryRun, in which case it only describes it.
        Every mutation in this script goes through here so that -DryRun cannot
        drift out of sync with what the real run does. #>
    param([string]$Describe, [scriptblock]$Action)
    if ($DryRun) { Write-Host "  would: $Describe" -ForegroundColor Cyan; return }
    & $Action
    Write-Did $Describe
}

Write-Host ""
Write-Host "=== Installing $AppName ===" -ForegroundColor Cyan
if ($DryRun) { Write-Host "    DRY RUN -- nothing will be changed." -ForegroundColor Yellow }
Write-Host ""
Write-Step "source:  $sourceDir"
Write-Step "target:  $installRoot"
Write-Host ""

# --------------------------------------------------------------------------- #
# 1. Find what there is to install.
#
# The GUI is optional on purpose: a CLI-only release is a legitimate thing to
# ship, and refusing to install one would be worse than installing it.
# --------------------------------------------------------------------------- #
$payload = @()
foreach ($name in @("gplan.exe", "gplan-gui.exe")) {
    $candidate = Join-Path $sourceDir $name
    if (Test-Path $candidate) { $payload += $candidate }
}
if (-not $payload) {
    Write-Host "No binaries found next to this script." -ForegroundColor Red
    Write-Host "Expected gplan.exe (and optionally gplan-gui.exe) in: $sourceDir" -ForegroundColor Yellow
    Write-Host "Run install.ps1 from inside the unzipped release folder." -ForegroundColor Yellow
    exit 1
}
foreach ($file in $payload) {
    Write-Step ("found {0,-16} {1,7:N1} MB" -f (Split-Path $file -Leaf), ((Get-Item $file).Length / 1MB))
}

# --------------------------------------------------------------------------- #
# 2. Refuse to overwrite a running binary.
#
# Windows locks a running executable, so the copy would fail partway and leave
# a half-installed directory. Failing FIRST with an instruction is much better
# than failing at file three with an access-denied trace.
# --------------------------------------------------------------------------- #
$running = @(Get-Process -Name "gplan", "gplan-gui" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0 -and -not $DryRun) {
    Write-Host ""
    Write-Host "Protein Ledger is currently running. Close it and run this again." -ForegroundColor Red
    $running | ForEach-Object { Write-Host "  running: $($_.ProcessName) (pid $($_.Id))" -ForegroundColor Yellow }
    exit 1
}

# --------------------------------------------------------------------------- #
# 3. Copy the payload.
# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "Files" -ForegroundColor Cyan
Invoke-Action "create $installRoot" { New-Item -ItemType Directory -Force -Path $installRoot | Out-Null }

$installed = @()
foreach ($file in $payload) {
    $leaf = Split-Path $file -Leaf
    $target = Join-Path $installRoot $leaf
    $installed += $target
    # Copy-Item overwrites, so a second run replaces rather than duplicating.
    Invoke-Action "install $leaf" { Copy-Item -LiteralPath $file -Destination $target -Force }.GetNewClosure()
}

# The uninstall checklist and the install notes ship WITH the install, not just
# in the ZIP -- GFP-102's acceptance criteria require a user whose uninstall
# failed to be able to read the manual removal steps, and by then the ZIP is
# usually long gone.
foreach ($doc in @("INSTALL.md", "UNINSTALL.md", "uninstall.ps1")) {
    $candidate = Join-Path $sourceDir $doc
    if (-not (Test-Path $candidate)) { continue }
    $target = Join-Path $installRoot $doc
    $installed += $target
    Invoke-Action "install $doc" { Copy-Item -LiteralPath $candidate -Destination $target -Force }.GetNewClosure()
}

# --------------------------------------------------------------------------- #
# 3b. The bundled Kroger credential (GFP-146).
#
# v1 ships with the API key so that a customer does not have to register an
# application at developer.kroger.com. It goes into the USER-DATA dir, not next
# to the binary and never as a constant inside it, and that placement is the
# whole point of the ticket: a key in a binary is recoverable either way, but a
# key in a file can be ROTATED with a file swap, while a key in a binary needs
# a rebuild, a re-release, and every user to notice and act. Kroger's 10,000
# calls/day ceiling is per credential, so one extracted key is one revocation
# for everybody -- the recovery path is the thing worth protecting.
#
# GFP-147 replaces this with a hosted key. Until then the risk is bounded and
# taken knowingly.
#
# NEVER OVERWRITES. An operator or developer with their own credential already
# in place keeps it. Silently replacing someone's working key with the shared
# one would move them onto a quota pool they did not ask to share, and they
# would have no way to tell that it had happened.
# --------------------------------------------------------------------------- #
$credentialName = "kroger-env.config"
$bundledCredential = Join-Path $sourceDir $credentialName
if (Test-Path $bundledCredential) {
    $dataDir = Join-Path $env:LOCALAPPDATA "grocery-planner\grocery-planner"
    $credentialTarget = Join-Path $dataDir $credentialName
    if (Test-Path $credentialTarget) {
        Write-Skip "Kroger credential already present -- keeping yours"
    } else {
        Invoke-Action "install Kroger credential" {
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            Copy-Item -LiteralPath $bundledCredential -Destination $credentialTarget -Force
        }.GetNewClosure()
    }
} else {
    # A CLI-only or credential-free build is legitimate; the app already tells
    # the user how to configure one via `gplan credentials`.
    Write-Skip "no Kroger credential bundled -- run 'gplan credentials' to see what is needed"
}

# --------------------------------------------------------------------------- #
# 4. Prove the thing that was installed actually runs.
#
# A copy that succeeded is not an install that worked: a binary built against
# the wrong architecture, or missing a data file the spec forgot, copies
# perfectly and then fails on first launch -- at which point the user blames
# the app rather than the install. Running `gplan version` costs a second and
# converts that into an install-time failure with a visible cause.
# --------------------------------------------------------------------------- #
$version = "unknown"
$installedCli = Join-Path $installRoot "gplan.exe"
if (-not $DryRun) {
    Write-Host ""
    Write-Host "Verifying" -ForegroundColor Cyan
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = (& $installedCli version 2>&1 | Out-String).Trim()
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) {
        Write-Host "The installed binary does not run. Install aborted." -ForegroundColor Red
        Write-Host $output -ForegroundColor DarkGray
        exit 1
    }
    if ($output -match "grocery-planner\s+(\S+)") { $version = $Matches[1] }
    Write-Did "gplan runs: $output"
}

# --------------------------------------------------------------------------- #
# 5. Integration: PATH, Start Menu, Add/Remove Programs.
# --------------------------------------------------------------------------- #
$integrations = @{}
if ($NoIntegrate) {
    Write-Host ""
    Write-Skip "PATH, Start Menu and Add/Remove Programs skipped (-NoIntegrate)"
} else {
    Write-Host ""
    Write-Host "Integration" -ForegroundColor Cyan

    # --- user PATH ---------------------------------------------------------- #
    # Read the REGISTRY value, not $env:PATH: the process environment is the
    # merged machine+user PATH, so testing against it would refuse to add an
    # entry that is only present because some other install put it there.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    $entries = $userPath.Split(";") | Where-Object { $_ -ne "" }
    $already = $entries | Where-Object { $_.TrimEnd("\") -ieq $installRoot.TrimEnd("\") }
    if ($already) {
        Write-Skip "PATH already contains $installRoot"
        $integrations["path"] = $installRoot
    } else {
        Invoke-Action "add $installRoot to your PATH" {
            $updated = (@($entries) + $installRoot) -join ";"
            [Environment]::SetEnvironmentVariable("Path", $updated, "User")
        }.GetNewClosure()
        $integrations["path"] = $installRoot
        Write-Warn "open a new terminal before `gplan` is on your PATH"
    }

    # --- Start Menu shortcut ------------------------------------------------ #
    $installedGui = Join-Path $installRoot "gplan-gui.exe"
    if (Test-Path $installedGui) {
        $menuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$StartMenuFolder"
        $shortcut = Join-Path $menuDir "$AppName.lnk"
        Invoke-Action "create Start Menu shortcut" {
            New-Item -ItemType Directory -Force -Path $menuDir | Out-Null
            $shell = New-Object -ComObject WScript.Shell
            $link = $shell.CreateShortcut($shortcut)
            $link.TargetPath = $installedGui
            $link.WorkingDirectory = $installRoot
            $link.Description = "Protein Ledger - cost per gram of protein"
            $link.Save()
        }.GetNewClosure()
        $integrations["start_menu"] = $menuDir
    } else {
        Write-Skip "no GUI in this release, so no Start Menu shortcut"
    }

    # --- Add/Remove Programs ------------------------------------------------ #
    # HKCU, matching the per-user install. Set-ItemProperty is a write, not an
    # append, so a reinstall updates the entry in place.
    #
    # Registered only when the uninstaller is actually present. An Add/Remove
    # Programs entry whose Uninstall button does nothing is worse than no entry
    # at all: the user believes they have removed the app, and the credentials
    # in the data directory stay on the machine.
    $uninstallScript = Join-Path $installRoot "uninstall.ps1"
    if (-not (Test-Path $uninstallScript)) {
      Write-Skip "no uninstall.ps1 in this release, so no Add/Remove Programs entry"
    } else {
    # --- background refresh (GFP-102) --------------------------------------- #
    # Registered by the installer, because price history only accumulates going
    # forward and cannot be backfilled: every day the machine does not scrape
    # is a permanent hole in the trends chart. Opt-out rather than opt-in for
    # that reason, with two ways out -- -NoTimer here, and the
    # `background_refresh` setting at any time afterwards.
    if ($NoTimer) {
        Write-Skip "background refresh not registered (-NoTimer)"
    } elseif (-not $DryRun) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $timerOut = (& $installedCli timer install 2>&1 | Out-String).Trim()
        $timerCode = $LASTEXITCODE
        $ErrorActionPreference = $previous
        if ($timerCode -eq 0) {
            Write-Did "background refresh registered (daily)"
            $integrations["timer"] = "$TaskPath$TaskName"
        } else {
            # Not fatal. An install that works but does not refresh by itself
            # is a much better outcome than no install.
            Write-Warn "could not register the background refresh -- the app still works, and you can retry with: gplan timer install"
            Write-Host $timerOut -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  would: register the daily background refresh" -ForegroundColor Cyan
    }

    Invoke-Action "register in Add/Remove Programs" {
        New-Item -Path $RegistryKey -Force | Out-Null
        $size = (Get-ChildItem $installRoot -Recurse -File | Measure-Object Length -Sum).Sum
        $values = @{
            DisplayName     = $AppName
            DisplayVersion  = $version
            Publisher       = $Publisher
            InstallLocation = $installRoot
            UninstallString = "powershell.exe -ExecutionPolicy Bypass -File `"$uninstallScript`""
            NoModify        = 1
            NoRepair        = 1
            EstimatedSize   = [int]($size / 1KB)
        }
        foreach ($name in $values.Keys) {
            Set-ItemProperty -Path $RegistryKey -Name $name -Value $values[$name]
        }
    }.GetNewClosure()
    $integrations["registry"] = $RegistryKey
    }
}

# --------------------------------------------------------------------------- #
# 6. The manifest.
#
# Written LAST, so its presence means the install got all the way through.
# GFP-92's uninstaller removes exactly what is listed here -- including a
# -Prefix install it could never have guessed -- and reports anything it could
# not remove by full resolved path.
# --------------------------------------------------------------------------- #
$manifestPath = Join-Path $installRoot $ManifestName
$manifest = [ordered]@{
    schema        = 1
    app           = $AppName
    version       = $version
    platform      = "windows"
    install_root  = $installRoot
    installed_at  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    files         = @($installed)
    integrations  = $integrations
}
Invoke-Action "write $ManifestName" {
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
}.GetNewClosure()

# --------------------------------------------------------------------------- #
# 7. Hand over to a working first run.
# --------------------------------------------------------------------------- #
Write-Host ""
if ($DryRun) {
    Write-Host "DRY RUN complete -- nothing was changed." -ForegroundColor Yellow
    exit 0
}
Write-Host "=== $AppName $version installed ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps" -ForegroundColor Cyan
Write-Host "  1. Set your ZIP code so prices are local to you:" -ForegroundColor Gray
Write-Host "       gplan config set postal_code 27401" -ForegroundColor White
Write-Host "  2. Open Protein Ledger from the Start Menu -- it fetches today's" -ForegroundColor Gray
Write-Host "     prices on first run." -ForegroundColor Gray
Write-Host "     Or from a terminal:  gplan scrape harristeeter" -ForegroundColor White
Write-Host ""
Write-Host "To uninstall: $installRoot\uninstall.ps1" -ForegroundColor DarkGray
Write-Host ""
