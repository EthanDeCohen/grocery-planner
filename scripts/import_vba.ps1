# Imports tracked VBA modules into the template workbook and saves a macro-enabled copy.
# Requires Excel setting: File > Options > Trust Center > Trust Center Settings >
# Macro Settings > check "Trust access to the VBA project object model"

param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$SourceWorkbook = (Join-Path $ProjectRoot "template\GroceryPlanner.template.xlsx"),
    [string]$OutputWorkbook = (Join-Path $ProjectRoot "template\GroceryPlanner.template.xlsm")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "vba_import_utils.ps1")

if (-not (Test-Path $SourceWorkbook)) {
    throw "Source workbook not found: $SourceWorkbook. Run scripts/create_template_workbook.py first."
}

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false

try {
    $wb = $excel.Workbooks.Open($SourceWorkbook)
    $vbaProject = $wb.VBProject

    $modules = @(
        @{ Path = Join-Path $ProjectRoot "vba\GroceryStoreConfig.bas"; Type = 1; Name = "GroceryStoreConfig" },
        @{ Path = Join-Path $ProjectRoot "vba\GroceryCsvImporter.bas"; Type = 1; Name = "GroceryCsvImporter" },
        @{ Path = Join-Path $ProjectRoot "vba\GroceryPlannerModule.bas"; Type = 1; Name = "GroceryPlannerModule" }
    )

    foreach ($module in $modules) {
        $source = Read-VbaImportSource -Path $module.Path
        $component = $vbaProject.VBComponents.Add($module.Type)
        $component.Name = $module.Name
        $component.CodeModule.AddFromString($source)
    }

    $thisWorkbookPath = Join-Path $ProjectRoot "vba\ThisWorkbook.cls"
    if (Test-Path $thisWorkbookPath) {
        $thisWorkbook = $vbaProject.VBComponents.Item("ThisWorkbook")
        $thisWorkbookSource = Read-VbaImportSource -Path $thisWorkbookPath
        $thisWorkbookModule = $thisWorkbook.CodeModule
        if ($thisWorkbookModule.CountOfLines -gt 0) {
            $thisWorkbookModule.DeleteLines(1, $thisWorkbookModule.CountOfLines)
        }
        $thisWorkbookModule.AddFromString($thisWorkbookSource)
    }

    if (Test-Path $OutputWorkbook) {
        Remove-Item $OutputWorkbook -Force
    }

    $wb.SaveAs($OutputWorkbook, 52)
    $wb.Close($false)
    Write-Host "Created macro-enabled template: $OutputWorkbook"
}
catch {
    Write-Error @"
VBA import failed. Enable Excel trust for programmatic VBA access:
File > Options > Trust Center > Trust Center Settings > Macro Settings >
check 'Trust access to the VBA project object model', then rerun this script.

Original error: $($_.Exception.Message)
"@
    exit 1
}
finally {
    $excel.Quit() | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
}