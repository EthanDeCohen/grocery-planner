<#
.SYNOPSIS
    Remove Grocery Planner and all of its data from this user account (GFP-92).

.DESCRIPTION
    THE FAILURE THIS SCRIPT IS DESIGNED AROUND is not leaving a file behind. It
    is destroying a nutritionist's client list. Those records are hand-entered
    over months and exist nowhere else, so nothing is deleted before an
    explicit confirmation, the confirmation names the database file, and
    -KeepData exists for the very common case of "I am reinstalling, not
    leaving".

    THE SECOND FAILURE is the opposite one: a half-finished uninstall that
    leaves a Kroger client_secret and a live Whole Foods session cookie on a
    machine that is then resold, repaired or handed on. So this reports every
    path it could NOT remove, by full resolved path, and says plainly that
    those are credentials.

    WHAT IT REMOVES, and the two rules behind the list (both from GFP-102):

      1. THE BACKGROUND TIMER FIRST. A leftover Scheduled Task keeps firing
         against a binary that no longer exists. Removing it last would let a
         firing timer race the removal of what it invokes.
      2. THE DIRECTORY, NOT A LIST OF FILENAMES. The data directory accumulates
         things no uninstaller can enumerate in advance -- SQLite's -wal/-shm
         files, and hand-made backups nobody registered anywhere.

    The plan comes from `gplan uninstall-plan`, so environment overrides are
    RESOLVED rather than assumed: GROCERY_PLANNER_DB and friends relocate files
    out of the data directory, and "delete the folder" then silently misses
    them. If gplan will not run -- which is a normal state during an uninstall
    -- this falls back to the documented default locations and says so.

    IDEMPOTENT, as the ticket requires: on a machine with nothing installed it
    prints that there is nothing to do and exits 0.

.PARAMETER Yes
    Skip the confirmation. For CI and for anyone scripting this deliberately.

.PARAMETER KeepData
    Remove the program but leave the database, settings and credentials alone.
    What you want when reinstalling or upgrading by hand.

.PARAMETER DryRun
    Print everything that would be removed, and remove nothing.

.EXAMPLE
    ./uninstall.ps1 -DryRun
    ./uninstall.ps1
    ./uninstall.ps1 -KeepData
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$KeepData,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Pinned by GFP-102 -- mirrored in grocery_planner/install_paths.py, and
# tests/test_uninstaller_scripts.py fails if they disagree.
$AppName         = "Grocery Planner"
$InstallDirName  = "GroceryPlanner"
$StartMenuFolder = "Grocery Planner"
$RegistryKey     = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GroceryPlanner"
$TaskPath        = "\GroceryPlanner\"
$TaskName        = "Refresh"
$ManifestName    = "install-manifest.json"

$removed = New-Object System.Collections.ArrayList
$failed  = New-Object System.Collections.ArrayList
$absent  = 0

function Write-Gray { param([string]$m) Write-Host "  $m" -ForegroundColor Gray }
function Write-Did  { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Write-Skip { param([string]$m) Write-Host "  [--] $m" -ForegroundColor DarkGray }
function Write-Bad  { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Red }

Write-Host ""
Write-Host "=== Uninstalling $AppName ===" -ForegroundColor Cyan
if ($DryRun) { Write-Host "    DRY RUN -- nothing will be removed." -ForegroundColor Yellow }

# --------------------------------------------------------------------------- #
# 1. Locate the install. The manifest is authoritative because it is the only
#    thing that knows about a -Prefix install; the default is the fallback.
# --------------------------------------------------------------------------- #
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\$InstallDirName"
$manifest = $null
foreach ($candidate in @($PSScriptRoot, $installRoot)) {
    $file = Join-Path $candidate $ManifestName
    if (Test-Path $file) {
        try {
            $manifest = Get-Content $file -Raw | ConvertFrom-Json
            if ($manifest.schema -ne 1) {
                # Refusing an unknown schema rather than deleting by guesswork:
                # this file names paths, and misreading it deletes the wrong ones.
                Write-Bad "manifest at $file has schema $($manifest.schema), which this version does not understand -- ignoring it"
                $manifest = $null
                continue
            }
            $installRoot = $manifest.install_root
            Write-Gray "manifest: $file"
            break
        } catch {
            Write-Bad "manifest at $file could not be read -- falling back to default locations"
            $manifest = $null
        }
    }
}
if (-not $manifest) { Write-Gray "no manifest found -- using default locations" }
Write-Gray "install: $installRoot"

# --------------------------------------------------------------------------- #
# 2. Ask the app where its data actually is.
#
# A failure here is EXPECTED, not exceptional -- the binary may already be
# gone, or half-removed by a previous attempt. Falling back is the normal path,
# not the error path, so it degrades quietly and says which mode it is in.
# --------------------------------------------------------------------------- #
$dataItems = @()
$gplan = Join-Path $installRoot "gplan.exe"
if (-not (Test-Path $gplan)) { $gplan = Join-Path $PSScriptRoot "gplan.exe" }
$resolved = $false
if (Test-Path $gplan) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $planText = (& $gplan uninstall-plan 2>&1 | Out-String)
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -eq 0) {
        foreach ($line in $planText -split "`r?`n") {
            if (-not $line.Trim()) { continue }
            $parts = $line -split "`t"
            if ($parts.Count -lt 4) { continue }
            $dataItems += [pscustomobject]@{
                Kind = $parts[0]; Flags = $parts[1]; Label = $parts[2]; Target = $parts[3]
            }
        }
        $resolved = $true
    }
}
if (-not $resolved) {
    Write-Gray "gplan could not be run -- using the documented default locations"
    $dataItems = @(
        [pscustomobject]@{ Kind = "task"; Flags = "-"; Label = "Scheduled task (background refresh)"; Target = "$TaskPath$TaskName" },
        [pscustomobject]@{ Kind = "directory"; Flags = "irreplaceable,sensitive"; Label = "All application data"; Target = (Join-Path $env:LOCALAPPDATA "grocery-planner\grocery-planner") }
    )
}

# --------------------------------------------------------------------------- #
# 3. Say plainly what will go, then ask.
# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "This will remove:" -ForegroundColor Cyan
Write-Gray "PROGRAM"
Write-Gray "  $installRoot"
Write-Gray "  Start Menu shortcut, PATH entry, Add/Remove Programs entry"
Write-Gray "  Scheduled task $TaskPath$TaskName (the background refresh)"
if ($KeepData) {
    Write-Host ""
    Write-Host "  Your data will be KEPT (-KeepData):" -ForegroundColor Green
    foreach ($item in @($dataItems | Where-Object { $_.Kind -notin @("task", "agent") })) {
        Write-Gray "  keeping  $($item.Target)"
    }
} else {
    Write-Host ""
    Write-Host "DATA -- this cannot be undone" -ForegroundColor Yellow
    # The task is listed under PROGRAM, not here: it is not data, and putting
    # it in the irreversible list makes that list look longer and less exact
    # than it is -- which is how people stop reading it.
    foreach ($item in @($dataItems | Where-Object { $_.Kind -notin @("task", "agent") })) {
        $note = ""
        if ($item.Flags -match "relocated:(\S+)") { $note = "   (moved here by $($Matches[1]))" }
        Write-Gray "  $($item.Target)$note"
    }
    Write-Host ""
    Write-Host "  Your client records are hand-entered and CANNOT be recovered." -ForegroundColor Yellow
    Write-Host "  Export them first if you may want them:  gplan export --help" -ForegroundColor Yellow
    Write-Host "  Or run this with -KeepData to remove only the program." -ForegroundColor Yellow
}

if (-not $Yes -and -not $DryRun) {
    Write-Host ""
    $answer = Read-Host "Type REMOVE to continue, anything else to cancel"
    if ($answer -ne "REMOVE") {
        Write-Host "Cancelled. Nothing was removed." -ForegroundColor Green
        exit 0
    }
}

# --------------------------------------------------------------------------- #
# 4. Remove. Every removal reports what happened, by full path.
# --------------------------------------------------------------------------- #
function Remove-Thing {
    param([string]$Label, [string]$Target, [scriptblock]$Test, [scriptblock]$Action)
    if (-not (& $Test)) { $script:absent++; Write-Skip "$Label -- not present"; return }
    if ($DryRun) { Write-Host "  would remove: $Label  ($Target)" -ForegroundColor Cyan; return }
    try {
        & $Action
        [void]$script:removed.Add($Target)
        Write-Did "$Label  ($Target)"
    } catch {
        [void]$script:failed.Add($Target)
        Write-Bad "COULD NOT REMOVE $Target -- $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "Removing" -ForegroundColor Cyan

# --- the timer, FIRST ------------------------------------------------------- #
Remove-Thing "Scheduled task $TaskPath$TaskName" "$TaskPath$TaskName" `
    { [bool](Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue) } `
    { Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false }

# schtasks cannot delete a folder, and an empty GroceryPlanner folder left in
# Task Scheduler reads as a failed uninstall. The Schedule.Service COM object
# can, so use it -- and treat its absence as fine, because the folder only
# exists once GFP-102 has run.
if (-not $DryRun) {
    try {
        $service = New-Object -ComObject "Schedule.Service"
        $service.Connect()
        $root = $service.GetFolder("\")
        $root.DeleteFolder($TaskPath.Trim("\"), 0)
        Write-Did "Task Scheduler folder $TaskPath"
    } catch {
        Write-Skip "Task Scheduler folder $TaskPath -- not present"
    }
}

# --- PATH ------------------------------------------------------------------- #
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }
$entries = @($userPath.Split(";") | Where-Object { $_ -ne "" })
$keep = @($entries | Where-Object { $_.TrimEnd("\") -ine $installRoot.TrimEnd("\") })
Remove-Thing "PATH entry" $installRoot `
    { $keep.Count -ne $entries.Count } `
    { [Environment]::SetEnvironmentVariable("Path", ($keep -join ";"), "User") }.GetNewClosure()

# --- Start Menu ------------------------------------------------------------- #
$menuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$StartMenuFolder"
Remove-Thing "Start Menu folder" $menuDir `
    { Test-Path $menuDir } `
    { Remove-Item -Recurse -Force $menuDir }.GetNewClosure()

# --- Add/Remove Programs ---------------------------------------------------- #
Remove-Thing "Add/Remove Programs entry" $RegistryKey `
    { Test-Path $RegistryKey } `
    { Remove-Item -Recurse -Force $RegistryKey }.GetNewClosure()

# --- program files ---------------------------------------------------------- #
# The binaries must not be running, or the directory removal fails partway and
# leaves the app half-present.
$running = @(Get-Process -Name "gplan", "gplan-gui" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0 -and -not $DryRun) {
    Write-Bad "$AppName is running -- close it and run this again"
    $running | ForEach-Object { Write-Gray "running: $($_.ProcessName) (pid $($_.Id))" }
    exit 1
}
Remove-Thing "Program files" $installRoot `
    { Test-Path $installRoot } `
    { Remove-Item -Recurse -Force $installRoot }.GetNewClosure()

# --- data ------------------------------------------------------------------- #
if ($KeepData) {
    Write-Host ""
    Write-Skip "application data kept (-KeepData)"
} else {
    foreach ($item in $dataItems) {
        if ($item.Kind -in @("task", "agent")) { continue }   # already handled, first
        $target = $item.Target
        Remove-Thing $item.Label $target `
            { Test-Path $target } `
            { Remove-Item -Recurse -Force $target }.GetNewClosure()
    }
}

# --------------------------------------------------------------------------- #
# 5. Report. "Some files could not be removed" is not good enough when one of
#    them is a credential.
# --------------------------------------------------------------------------- #
Write-Host ""
if ($DryRun) {
    Write-Host "DRY RUN complete -- nothing was removed." -ForegroundColor Yellow
    exit 0
}
if ($removed.Count -eq 0 -and $failed.Count -eq 0) {
    Write-Host "Nothing to remove -- $AppName is not installed for this user." -ForegroundColor Green
    exit 0
}
Write-Host "=== Removed $($removed.Count) item(s) ===" -ForegroundColor Green
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "COULD NOT REMOVE $($failed.Count) item(s). Delete these by hand:" -ForegroundColor Red
    foreach ($path in $failed) { Write-Host "  $path" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "At least one of these may hold a stored credential (a Kroger" -ForegroundColor Yellow
    Write-Host "client_secret or a Whole Foods session cookie). Leaving them on a" -ForegroundColor Yellow
    Write-Host "machine that is later resold or repaired is what this warning is for." -ForegroundColor Yellow
    Write-Host "See UNINSTALL.md for the full manual removal checklist." -ForegroundColor Yellow
    exit 1
}
Write-Host ""
Write-Host "$AppName has been removed." -ForegroundColor Green
if ($KeepData) { Write-Host "Your data was kept and is still where it was." -ForegroundColor Gray }
Write-Host ""
