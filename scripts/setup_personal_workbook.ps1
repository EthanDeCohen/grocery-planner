# Creates your gitignored personal workbook from the macro-enabled template.
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TemplateWorkbook = (Join-Path $ProjectRoot "template\GroceryPlanner.template.xlsm"),
    [string]$OutputWorkbook = (Join-Path $ProjectRoot "GroceryPlanner.xlsm")
)

if (-not (Test-Path $TemplateWorkbook)) {
    throw @"
Macro-enabled template not found: $TemplateWorkbook
Run:
  python scripts/create_template_workbook.py
  powershell -ExecutionPolicy Bypass -File scripts/import_vba.ps1
"@
}

Copy-Item $TemplateWorkbook $OutputWorkbook -Force
Write-Host "Created personal workbook: $OutputWorkbook"
Write-Host "Open it in Excel and run RefreshGroceryData (Alt+F8)."