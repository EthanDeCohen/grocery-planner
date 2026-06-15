"""Build GroceryPlanner.template.xlsm with embedded VBA from vba/ source files."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VBA_DIR = ROOT / "vba"
TEMPLATE_PATH = ROOT / "template" / "GroceryPlanner.template.xlsm"

SHEETS = [
    ("Instructions", [
        ("A1", "Grocery Planner"),
        ("A3", "Last refreshed"),
        ("A4", "Refresh timestamp"),
        ("A5", "Refresh summary"),
        ("A7", "How to use"),
        ("A8", "1. Copy template/GroceryPlanner.template.xlsm to GroceryPlanner.xlsm in the project root."),
        ("A9", "2. Add or update CSV files under data/<store>/prices.csv and deals.csv."),
        ("A10", "3. Run the RefreshGroceryData macro (Alt+F8) or click Refresh on the Instructions sheet."),
        ("A11", "4. Review per-store sheets plus All Prices, All Deals, and Savings Summary."),
        ("A13", "Tracked in git: template/, vba/, scripts/. Gitignored: data/, your GroceryPlanner.xlsm copy."),
    ]),
    ("Whole Foods", [("A1", "Run RefreshGroceryData to load data/wholefoods/*.csv")]),
    ("Whole Foods Deals", [("A1", "Run RefreshGroceryData to load data/wholefoods/deals.csv")]),
    ("Food Lion", [("A1", "Run RefreshGroceryData to load data/foodlion/*.csv")]),
    ("Food Lion Deals", [("A1", "Run RefreshGroceryData to load data/foodlion/deals.csv")]),
    ("Harris Teeter", [("A1", "Run RefreshGroceryData to load data/harristeeter/*.csv")]),
    ("Harris Teeter Deals", [("A1", "Run RefreshGroceryData to load data/harristeeter/deals.csv")]),
    ("All Prices", [("A1", "Combined price rows from all stores")]),
    ("All Deals", [("A1", "Combined deal rows from all stores")]),
    ("Savings Summary", [("A1", "Metric"), ("B1", "Value")]),
]


def read_vba_source(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("VERSION "):
        lines = lines[4:]
    return "\n".join(lines)


def build_workbook(excel):
    if TEMPLATE_PATH.exists():
        TEMPLATE_PATH.unlink()

    wb = excel.Workbooks.Add()
    while wb.Worksheets.Count > 1:
        wb.Worksheets(1).Delete()

    for idx, (name, cells) in enumerate(SHEETS):
        if idx == 0:
            ws = wb.Worksheets(1)
        else:
            ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
        ws.Name = name
        for addr, value in cells:
            ws.Range(addr).Value = value
        if name == "Instructions":
            ws.Range("A1").Font.Bold = True
            ws.Range("A1").Font.Size = 16
            ws.Columns("A:B").AutoFit()

    vba_project = wb.VBProject
    for filename, component_type, module_name in [
        ("GroceryStoreConfig.cls", 2, "GroceryStoreConfig"),
        ("GroceryCsvImporter.cls", 2, "GroceryCsvImporter"),
        ("GroceryPlannerModule.bas", 1, "GroceryPlannerModule"),
    ]:
        source = read_vba_source(VBA_DIR / filename)
        component = vba_project.VBComponents.Add(component_type)
        component.Name = module_name
        component.CodeModule.AddFromString(source)

    wb.SaveAs(str(TEMPLATE_PATH), FileFormat=52)
    wb.Close(SaveChanges=False)
    return TEMPLATE_PATH


def main() -> int:
    try:
        import win32com.client  # type: ignore
    except ImportError:
        print("pywin32 is required: pip install pywin32", file=sys.stderr)
        return 1

    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        path = build_workbook(excel)
        print(f"Created {path}")
        return 0
    finally:
        excel.Quit()


if __name__ == "__main__":
    raise SystemExit(main())