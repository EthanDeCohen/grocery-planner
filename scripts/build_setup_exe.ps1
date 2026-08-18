<#
.SYNOPSIS
    Build the one-click Windows installer, ProteinLedger-Setup.exe (GFP-340).

.DESCRIPTION
    Compiles packaging/setup/Setup.cs and appends a release ZIP to it, so the
    result is one downloadable file that unpacks itself and runs the ZIP's own
    install.ps1.

    NO INSTALLER TOOLCHAIN, which is the part of GFP-91 this ticket does NOT
    overturn. csc.exe lives in the .NET Framework directory of every Windows
    machine and every GitHub Windows runner, so there is nothing to install on
    a build machine and nothing to add to CI.

    The layout of what comes out:

        [ stub.exe ][ payload.zip ][ "PLSFX001" ][ payload length, Int64 ]

    The stub reads the last sixteen bytes of its own file to find the payload.
    Appending beats embedding as a resource because the compiler never has to
    hold a quarter of a gigabyte -- the build is a compile of a few KB followed
    by a byte copy.

.PARAMETER Payload
    The release ZIP to wrap. Must contain install.ps1 inside a single
    top-level folder, which is the shape release.yml produces.

.PARAMETER Output
    Where to write the installer. Defaults to
    dist/ProteinLedger-Setup-v<version>.exe.

.PARAMETER Version
    Version stamped into the file's properties. Defaults to
    grocery_planner.__version__.

.EXAMPLE
    ./scripts/build_setup_exe.ps1 -Payload protein-ledger-v1.1.5-windows.zip
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Payload,
    [string]$Output,
    [string]$Version
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$setupSource = Join-Path $repoRoot "packaging\setup\Setup.cs"
$iconPath = Join-Path $repoRoot "packaging\icons\icon.ico"

# "PLSFX001". Must match Setup.cs's Magic.
$magic = [byte[]](0x50, 0x4C, 0x53, 0x46, 0x58, 0x30, 0x30, 0x31)

function Write-Step { param([string]$Message) Write-Host "  $Message" -ForegroundColor Gray }
function Write-Did  { param([string]$Message) Write-Host "  [ok] $Message" -ForegroundColor Green }

# --------------------------------------------------------------------------- #
# What we are wrapping
# --------------------------------------------------------------------------- #
if (-not (Test-Path $Payload -PathType Leaf)) {
    throw "payload not found: $Payload"
}
$payloadPath = (Resolve-Path $Payload).Path
$payloadSize = (Get-Item $payloadPath).Length

if (-not $Version) {
    $initPath = Join-Path $repoRoot "grocery_planner\__init__.py"
    $match = [regex]::Match((Get-Content $initPath -Raw), '__version__ = "([^"]+)"')
    if (-not $match.Success) { throw "could not read __version__ from $initPath" }
    $Version = $match.Groups[1].Value
}
# Assembly versions are four numbers and nothing else, so a suffix like
# -rc1 is dropped here rather than failing the compile.
$numericVersion = ([regex]::Match($Version, '^\d+(\.\d+){0,3}')).Value
while (($numericVersion -split '\.').Count -lt 4) { $numericVersion += ".0" }

if (-not $Output) {
    $Output = Join-Path $repoRoot "dist\ProteinLedger-Setup-v$Version.exe"
}
$outputDir = Split-Path -Parent $Output
if ($outputDir -and -not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}

Write-Host ""
Write-Host "=== Building ProteinLedger-Setup ===" -ForegroundColor Cyan
Write-Host ""
Write-Step ("payload: {0} ({1:N1} MB)" -f (Split-Path -Leaf $payloadPath), ($payloadSize / 1MB))
Write-Step "version: $Version"
Write-Step "output:  $Output"

# --------------------------------------------------------------------------- #
# The compiler and the one assembly the stub needs
# --------------------------------------------------------------------------- #
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) {
    throw "csc.exe not found under $env:WINDIR\Microsoft.NET -- .NET Framework 4.x is required to build the setup stub"
}

# System.IO.Compression holds ZipArchive. Reference assemblies first (what a
# compiler is meant to build against), then the GAC copy, which is present on
# any machine that has .NET 4.5 at all -- so a runner without the developer
# pack still builds.
$compression = $null
$referenceRoot = Join-Path ${env:ProgramFiles(x86)} "Reference Assemblies\Microsoft\Framework\.NETFramework"
if (Test-Path $referenceRoot) {
    $compression = Get-ChildItem $referenceRoot -Filter "System.IO.Compression.dll" -Recurse -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $compression) {
    $gac = Join-Path $env:WINDIR "Microsoft.NET\assembly\GAC_MSIL\System.IO.Compression"
    $compression = Get-ChildItem $gac -Filter "System.IO.Compression.dll" -Recurse -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $compression) {
    throw "System.IO.Compression.dll not found in the reference assemblies or the GAC"
}
Write-Step "compiler: $csc"

# --------------------------------------------------------------------------- #
# Compile the stub
#
# The version metadata is not decoration. The installer is unsigned until
# GFP-341, so the properties dialog is the only place a cautious user can
# confirm what they downloaded claims to be.
# --------------------------------------------------------------------------- #
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("pl-setup-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    $assemblyInfo = Join-Path $work "AssemblyInfo.cs"
    @"
using System.Reflection;

[assembly: AssemblyTitle("Protein Ledger Setup")]
[assembly: AssemblyDescription("Installs Protein Ledger for the current user.")]
[assembly: AssemblyProduct("Protein Ledger")]
[assembly: AssemblyCompany("decohen-partners")]
[assembly: AssemblyVersion("$numericVersion")]
[assembly: AssemblyFileVersion("$numericVersion")]
"@ | Set-Content -LiteralPath $assemblyInfo -Encoding UTF8

    $stub = Join-Path $work "stub.exe"
    $cscArgs = @(
        "/nologo", "/target:exe", "/platform:anycpu", "/optimize+",
        "/out:$stub",
        "/reference:$compression"
    )
    if (Test-Path $iconPath) { $cscArgs += "/win32icon:$iconPath" }
    $cscArgs += $setupSource
    $cscArgs += $assemblyInfo

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $compilerOutput = & $csc $cscArgs
    $compilerCode = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($compilerCode -ne 0) {
        $compilerOutput | ForEach-Object { Write-Host $_ -ForegroundColor Red }
        throw "the setup stub did not compile"
    }
    Write-Did ("compiled the stub ({0:N0} bytes)" -f (Get-Item $stub).Length)

    # ----------------------------------------------------------------------- #
    # stub + payload + trailer, streamed. Never Get-Content -Raw on a 240 MB
    # file: that reads the whole thing into memory to write it straight back
    # out again.
    # ----------------------------------------------------------------------- #
    $out = [System.IO.File]::Create($Output)
    try {
        foreach ($part in @($stub, $payloadPath)) {
            $in = [System.IO.File]::OpenRead($part)
            try { $in.CopyTo($out) } finally { $in.Dispose() }
        }
        $out.Write($magic, 0, $magic.Length)
        $lengthBytes = [BitConverter]::GetBytes([Int64]$payloadSize)
        $out.Write($lengthBytes, 0, $lengthBytes.Length)
    } finally {
        $out.Dispose()
    }
} finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

# --------------------------------------------------------------------------- #
# Read the trailer back the way the stub will.
#
# A build that produced a file the stub cannot open is a failure that would
# otherwise be discovered by a customer double-clicking it.
# --------------------------------------------------------------------------- #
$built = Get-Item $Output
$check = [System.IO.File]::OpenRead($Output)
try {
    $trailer = New-Object byte[] 16
    $check.Seek(-16, [System.IO.SeekOrigin]::End) | Out-Null
    $check.Read($trailer, 0, 16) | Out-Null
    for ($i = 0; $i -lt $magic.Length; $i++) {
        if ($trailer[$i] -ne $magic[$i]) { throw "the trailer magic did not read back" }
    }
    $recorded = [BitConverter]::ToInt64($trailer, 8)
    if ($recorded -ne $payloadSize) { throw "the trailer records $recorded bytes but the payload is $payloadSize" }

    # And the payload really does start where the stub will look for it.
    $offset = $built.Length - 16 - $recorded
    $check.Seek($offset, [System.IO.SeekOrigin]::Begin) | Out-Null
    $signature = New-Object byte[] 2
    $check.Read($signature, 0, 2) | Out-Null
    if ($signature[0] -ne 0x50 -or $signature[1] -ne 0x4B) {
        throw "no ZIP signature at the recorded payload offset"
    }
} finally {
    $check.Dispose()
}

Write-Did ("wrote {0} ({1:N1} MB)" -f (Split-Path -Leaf $Output), ($built.Length / 1MB))
Write-Host ""
