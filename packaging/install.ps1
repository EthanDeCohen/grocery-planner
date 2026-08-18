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

.PARAMETER Unblock
    Remove the mark-of-the-web from the installed binaries (GFP-162).

    Windows tags every file extracted from a downloaded ZIP as "came from the
    internet". That one tag causes both problems a new user hits: PowerShell
    refuses install.ps1 because of it, and SmartScreen objects to gplan.exe
    because of it.

    OFF by default. This is the Windows counterpart of install.sh's
    --clear-quarantine and takes the same position: silently removing a
    security marker on the user's behalf is not the installer's call to make.
    Install.cmd passes it, because that is the double-click path used by
    somebody who would otherwise have no way forward.

    It does NOT make an unsigned binary signed -- it suppresses the warnings
    that come from "downloaded", not the absence of a signature.

.PARAMETER StopRunning
    Close a running copy of the app instead of refusing to install over it
    (GFP-340).

    Without this, a running gplan.exe or gplan-gui.exe stops the install dead:
    Windows locks a running executable, so the copy would fail partway. That
    refusal is the right answer for somebody who typed ./install.ps1 in a
    terminal -- they can read it, close the app, and try again.

    It is the WRONG answer for the one-click setup (GFP-340), which has nobody
    reading a console. There, an upgrade over an app the user happens to have
    open must simply work. So the setup passes this, and it is off everywhere
    else.

    Closes gracefully first and only then ends the process, and it stops the
    background refresh task before either -- the task launches the app, so
    ending the app without ending the task leaves it free to start a new one
    in the middle of the file copy.

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
    [switch]$Unblock,
    [switch]$StopRunning,
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
#
# TWO PACKAGING SHAPES (GFP-320). A one-file build is a single .exe; a
# one-directory build is a folder holding the launcher and its _internal. Both
# are accepted, because they are what PyInstaller produces from the two specs
# and a release should not fail on which one it was cut from. The directory
# form is FLATTENED into the install root rather than nested, so the PATH entry
# and the Start Menu shortcut stay exactly as they were.
# --------------------------------------------------------------------------- #
$payload = @()
foreach ($name in @("gplan", "gplan-gui")) {
    $asDirectory = Join-Path $sourceDir $name
    $asFile = Join-Path $sourceDir "$name.exe"
    if ((Test-Path $asDirectory -PathType Container) -and
        (Test-Path (Join-Path $asDirectory "$name.exe") -PathType Leaf)) {
        $payload += @{ Name = $name; Path = $asDirectory; IsDirectory = $true }
    } elseif (Test-Path $asFile -PathType Leaf) {
        $payload += @{ Name = $name; Path = $asFile; IsDirectory = $false }
    }
}
if (-not $payload) {
    Write-Host "No binaries found next to this script." -ForegroundColor Red
    Write-Host "Expected gplan.exe or a gplan\ folder (and optionally the same for" -ForegroundColor Yellow
    Write-Host "gplan-gui) in: $sourceDir" -ForegroundColor Yellow
    Write-Host "Run install.ps1 from inside the unzipped release folder." -ForegroundColor Yellow
    exit 1
}
foreach ($item in $payload) {
    if ($item.IsDirectory) {
        $bytes = (Get-ChildItem $item.Path -Recurse -File | Measure-Object Length -Sum).Sum
        $shape = "folder"
    } else {
        $bytes = (Get-Item $item.Path).Length
        $shape = "one file"
    }
    Write-Step ("found {0,-16} {1,7:N1} MB  ({2})" -f $item.Name, ($bytes / 1MB), $shape)
}

# --------------------------------------------------------------------------- #
# 2. Deal with a running copy of the app.
#
# Windows locks a running executable, so the copy would fail partway and leave
# a half-installed directory. Failing FIRST with an instruction is much better
# than failing at file three with an access-denied trace.
#
# WHICH PROCESSES COUNT (GFP-340). Only the ones running out of the directory
# we are about to overwrite. A gplan.exe running from a development checkout
# locks nothing we are going to touch, so neither refusing to install nor
# killing it would be the installer's business. A process whose path cannot be
# read counts anyway: an unreadable path is not evidence that it is somebody
# else's, and a locked file is the failure being prevented.
#
# WHAT HAPPENS THEN depends on -StopRunning, and the default stays "refuse".
# See the parameter's own note for why the one-click setup needs the other
# answer and a terminal user does not.
# --------------------------------------------------------------------------- #
$StopTimeoutSeconds = 15

function Get-BlockingProcess {
    <#  gplan / gplan-gui processes running out of $installRoot. #>
    $root = $installRoot.TrimEnd("\") + "\"
    $candidates = @(Get-Process -Name "gplan", "gplan-gui" -ErrorAction SilentlyContinue)
    return @($candidates | Where-Object {
        $path = $null
        try { $path = $_.Path } catch { $path = $null }
        (-not $path) -or $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Stop-RefreshTask {
    <#  End a running background refresh (GFP-102), quietly.

        Cmdlets rather than schtasks.exe on purpose: schtasks writes "cannot
        find the file specified" to stderr when the task is absent, which is
        the normal case on a first install and reads as a failure to anyone
        watching. -ErrorAction handles it without printing anything. #>
    try {
        $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task -and $task.State -eq "Running") {
            Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
        }
    } catch {
        # Never fatal. Not being able to ask about a task is not a reason to
        # refuse an install.
    }
}

$running = @(Get-BlockingProcess)
if ($running.Count -gt 0 -and -not $DryRun) {
    if (-not $StopRunning) {
        Write-Host ""
        Write-Host "Protein Ledger is currently running. Close it and run this again." -ForegroundColor Red
        $running | ForEach-Object { Write-Host "  running: $($_.ProcessName) (pid $($_.Id))" -ForegroundColor Yellow }
        Write-Host "  (or pass -StopRunning to have the installer close it for you)" -ForegroundColor DarkGray
        exit 1
    }

    Write-Host ""
    Write-Host "Closing the running copy" -ForegroundColor Cyan
    Stop-RefreshTask

    # ASK FIRST, and only wait for the ones that can answer. CloseMainWindow
    # returns false when there is no main window to post the message to --
    # which is every run of the CLI. Waiting the full timeout for a window
    # that does not exist would make the commonest case the slowest one, and
    # it would look like the installer had hung.
    foreach ($proc in $running) {
        $closable = $false
        try { $closable = [bool]$proc.CloseMainWindow() } catch { $closable = $false }
        if ($closable) {
            Write-Step "asked $($proc.ProcessName) (pid $($proc.Id)) to close"
        } else {
            Write-Step "ending $($proc.ProcessName) (pid $($proc.Id))"
            try { $proc.Kill() } catch { }
        }
    }

    # One shared deadline, not one per process: fifteen seconds is the budget
    # for the whole step, not fifteen times however many copies are up. A GUI
    # is free to ignore the request, so the kill is unconditional at the end.
    $deadline = (Get-Date).AddSeconds($StopTimeoutSeconds)
    foreach ($proc in $running) {
        try {
            $remaining = [int]($deadline - (Get-Date)).TotalMilliseconds
            if ($remaining -gt 0) { $proc.WaitForExit($remaining) | Out-Null }
            if (-not $proc.HasExited) {
                Write-Step "$($proc.ProcessName) (pid $($proc.Id)) ignored the request -- ending it"
                $proc.Kill()
                $proc.WaitForExit(5000) | Out-Null
            }
        } catch {
            # An already-exited process throws on most of these. That is the
            # outcome we wanted, so it is not an error.
        }
    }

    # Ask again rather than trusting the loop. A copy relaunched by the timer,
    # or started by the user while this ran, locks the files just as well as
    # the one we set out to close.
    $stubborn = @(Get-BlockingProcess)
    if ($stubborn.Count -gt 0) {
        Write-Host ""
        Write-Host "Could not close Protein Ledger. Install aborted." -ForegroundColor Red
        $stubborn | ForEach-Object { Write-Host "  still running: $($_.ProcessName) (pid $($_.Id))" -ForegroundColor Yellow }
        Write-Host "Close it by hand and run this again." -ForegroundColor Yellow
        exit 1
    }
    Write-Did "closed the running copy"
}

# --------------------------------------------------------------------------- #
# 3. Copy the payload.
# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "Files" -ForegroundColor Cyan
Invoke-Action "create $installRoot" { New-Item -ItemType Directory -Force -Path $installRoot | Out-Null }

$installed = @()
foreach ($item in $payload) {
    $name = $item.Name
    $source = $item.Path
    if ($item.IsDirectory) {
        # Contents, not the folder itself -- see the flattening note above.
        # -Recurse -Force overwrites in place, so a reinstall replaces rather
        # than nesting a second copy inside the first.
        foreach ($entry in Get-ChildItem -LiteralPath $source -Force) {
            $installed += Join-Path $installRoot $entry.Name
        }
        Invoke-Action "install $name (folder)" {
            Copy-Item -Path (Join-Path $source "*") -Destination $installRoot -Recurse -Force
        }.GetNewClosure()
    } else {
        $target = Join-Path $installRoot "$name.exe"
        $installed += $target
        # Copy-Item overwrites, so a second run replaces rather than duplicating.
        Invoke-Action "install $name.exe" {
            Copy-Item -LiteralPath $source -Destination $target -Force
        }.GetNewClosure()
    }
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
# 3c. Mark-of-the-web (GFP-162).
#
# Windows tags every file extracted from a downloaded ZIP as "came from the
# internet", and that tag is what makes SmartScreen object when the app is
# first launched. Stripping it from the binaries we just installed removes the
# warning; it does NOT make an unsigned binary signed.
#
# OPT-IN, exactly like install.sh's --clear-quarantine, and for the same
# reason: silently removing a security marker on somebody's behalf is not the
# installer's call to make. Install.cmd -- the double-click path, used by
# somebody who would otherwise be stuck -- passes it and says so on screen.
# --------------------------------------------------------------------------- #
if ($Unblock) {
    Invoke-Action "tell Windows the installed files are safe to run" {
        Get-ChildItem -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
    }
} else {
    Write-Skip "mark-of-the-web left in place (pass -Unblock, or use Install.cmd)"
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
    # GFP-159: match the VERSION, not the product name -- see install.sh for
    # the full reasoning. On Windows this value also lands in the registry as
    # DisplayVersion, so a stale parse showed "unknown" in Add/Remove Programs
    # and not merely in the closing banner.
    if ($output -match "(\d+\.\d+\.\d+\S*)\s*$") { $version = $Matches[1] }
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
