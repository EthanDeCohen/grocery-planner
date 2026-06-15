# Creates your gitignored personal workbook from the tracked template.
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$OutputWorkbook = (Join-Path $ProjectRoot "GroceryPlanner.xlsm")
)

$template = Join-Path $ProjectRoot "template\GroceryPlanner.template.xlsx"
if (-not (Test-Path $template)) {
    throw "Template not found. Run: python scripts/create_template_workbook.py"
}

Copy-Item $template $OutputWorkbook -Force
Write-Host "Created personal workbook: $OutputWorkbook"
Write-Host "Next: open it in Excel, import vba\*.cls and vba\GroceryPlannerModule.bas, then Save As .xlsm if needed."