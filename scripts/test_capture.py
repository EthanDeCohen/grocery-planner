"""One-off test: capture Food Lion weekly ad screenshot for vision/OCR evaluation."""
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "captures" / "test"
URL = (
    "https://ad.foodlion.com/flyers/foodlion-weekly"
    "?locale=en-US&postal_code=27401&store_code=1473&type=1"
)

OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 2200})
    page.goto(URL, wait_until="networkidle", timeout=90000)
    time.sleep(8)
    path = OUT / "foodlion_weekly_test.png"
    page.screenshot(path=str(path), full_page=True)
    browser.close()
    print(path)