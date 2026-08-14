"""Renderer: a browser for stores that will not answer plain HTTP (GFP-165).

Exists because of a measurement. On 2026-08-09 every remaining catalogue
candidate -- Publix, Wegmans, Lowes Foods, ShopRite, Albertsons -- returned a
150-800KB JavaScript shell with no product data to httpx. Albertsons served
usable structured data on 2 of 12 pages, deterministically, and its aisle pages
sit behind Imperva. A browser is the only way in.

Holds NO secret, so it is the cheap half to hand to a vendor later: swap this
container's URL for a managed browser API and the broker does not change.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

#: How long to let a page settle after load. Product data arrives by XHR, so
#: returning at DOMContentLoaded gets the shell and nothing else -- the exact
#: failure this service exists to fix.
SETTLE_MS = int(os.environ.get("RENDER_SETTLE_MS", "5000"))
NAV_TIMEOUT_MS = int(os.environ.get("RENDER_NAV_TIMEOUT_MS", "45000"))

#: Markers that mean a bot control answered instead of the site. Reported, never
#: worked around: a control that says no is an answer (GFP-246).
BLOCK_MARKERS = ("Pardon Our Interruption", "reeseSkipExpirationCheck",
                 "Access Denied", "X-DataDome")

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        args=["--no-sandbox", "--disable-dev-shm-usage"])
    _state["pw"], _state["browser"] = pw, browser
    _state["rendered"] = 0
    try:
        yield
    finally:
        await browser.close()
        await pw.stop()


app = FastAPI(title="gfp-renderer", lifespan=lifespan)


def _extract(html: str) -> dict:
    """Structured product data, or honest absence.

    Reads schema.org first because it is a contract rather than a guess. Falls
    back to nothing at all: an unparsed page returns ``product: null``, never a
    scraped-from-markup approximation, because a wrong size produces a
    confident $/g figure that sends someone to a shop.
    """
    product = None
    for block in re.findall(
            r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == "Product":
                product = item
    protein = None
    match = re.search(r"[Pp]rotein[^0-9]{0,40}([0-9.]+)\s*g", html)
    if match:
        protein = float(match.group(1))
    return {"product": product, "protein_grams": protein}


@app.get("/health")
async def health() -> dict:
    return {"ok": _state.get("browser") is not None,
            "rendered": _state.get("rendered", 0)}


@app.get("/render")
async def render(url: str, settle_ms: int | None = None) -> dict:
    browser = _state.get("browser")
    if browser is None:
        raise HTTPException(503, "browser not started")
    context = await browser.new_context(user_agent=UA, locale="en-US",
                                        viewport={"width": 1366, "height": 900})
    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded",
                                   timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(settle_ms or SETTLE_MS)
        html = await page.content()
        blocked = [m for m in BLOCK_MARKERS if m in html]
        _state["rendered"] = _state.get("rendered", 0) + 1
        return {
            "url": url,
            "status": response.status if response else None,
            "bytes": len(html),
            "blocked_by": blocked or None,
            "title": await page.title(),
            **_extract(html),
        }
    except Exception as exc:                       # noqa: BLE001
        # A render failure is UNKNOWN, never "this product does not exist".
        raise HTTPException(502, f"{type(exc).__name__}: {exc}"[:200])
    finally:
        await page.close()
        await context.close()
