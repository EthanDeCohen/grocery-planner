"""Broker: the credentialled, quota-rationing, ZIP-pooling half (GFP-164/189/190).

The desktop app calls this instead of calling Kroger itself. Three reasons, all
of them recorded elsewhere in the project and none of them about convenience:

* **The credential never ships.** A binary is not a secret (broker.py), so the
  Kroger client_secret lives here and only here.
* **The quota is per credential, not per install.** 10,000 calls/day is a
  ceiling on the CREDENTIAL, and every Kroger banner draws on the same budget
  (GFP-192). Only a component that sees every draw can ration it.
* **ZIP demand pools.** Many installs in one ZIP should cause ONE upstream
  fetch (GFP-56), and only a shared service can collapse them.

WHAT NEVER CROSSES THIS BOUNDARY
--------------------------------
A request carries a STORE and a ZIP. Nothing else. No client name, weight,
height, age, sex, notes, target or plan -- the constraint that runs through the
entire hosted design, and the reason GFP-213 is still an open decision about
anything stronger. A test asserts the request model has no field that could
carry one.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, HTTPException

RENDERER_URL = os.environ.get("RENDERER_URL", "http://renderer:8081")

#: Kroger's documented daily budget, PER CREDENTIAL. Not per store and not per
#: install -- which is the whole reason this lives server-side.
DAILY_CALL_BUDGET = int(os.environ.get("KROGER_DAILY_BUDGET", "10000"))

#: How long a pooled answer serves every install asking for the same (store,
#: ZIP). Weekly ads change weekly and shelf prices daily; twelve hours is well
#: inside both and collapses a day's demand into two fetches.
POOL_TTL_SECONDS = int(os.environ.get("POOL_TTL_SECONDS", str(12 * 60 * 60)))

#: Stores this broker fetches with a credential it holds.
CREDENTIALLED = {"harristeeter-api"}

#: Stores that need a browser, so the broker delegates to the renderer. Every
#: one of these was measured on 2026-08-09 returning a JavaScript shell to
#: plain HTTP.
RENDERED = {"publix", "wegmans", "lowesfoods", "acme", "shoprite"}


@dataclass
class Pool:
    """Pooled answers plus the quota draw, which is the point of the service."""

    entries: dict[tuple[str, str], tuple[float, dict]] = field(default_factory=dict)
    calls_today: int = 0
    day: str = ""
    hits: int = 0
    misses: int = 0

    def _roll(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.calls_today = today, 0

    def remaining(self) -> int:
        self._roll()
        return max(DAILY_CALL_BUDGET - self.calls_today, 0)

    def get(self, key: tuple[str, str]) -> dict | None:
        found = self.entries.get(key)
        if found and time.time() - found[0] < POOL_TTL_SECONDS:
            self.hits += 1
            return found[1]
        return None

    def put(self, key: tuple[str, str], value: dict, cost: int = 0) -> None:
        self._roll()
        self.entries[key] = (time.time(), value)
        self.calls_today += cost
        self.misses += 1


POOL = Pool()
app = FastAPI(title="gfp-broker")


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "renderer": RENDERER_URL,
            "pooled_keys": len(POOL.entries)}


@app.get("/quota")
async def quota() -> dict:
    """What the pool is actually saving, in numbers an operator can act on."""
    served = POOL.hits + POOL.misses
    return {
        "daily_budget": DAILY_CALL_BUDGET,
        "calls_today": POOL.calls_today,
        "remaining": POOL.remaining(),
        "pool_hits": POOL.hits,
        "pool_misses": POOL.misses,
        "requests_served": served,
        "upstream_fetches_avoided": POOL.hits,
        "hit_rate": round(POOL.hits / served, 3) if served else None,
    }


@app.get("/deals")
async def deals(store: str, zip: str) -> dict:
    """Deals for one (store, ZIP). The ONLY two things a client may send."""
    if not zip.isdigit() or len(zip) != 5:
        raise HTTPException(400, "zip must be five digits")

    key = (store, zip)
    pooled = POOL.get(key)
    if pooled is not None:
        return {**pooled, "pooled": True}

    if store in CREDENTIALLED:
        if POOL.remaining() <= 0:
            # Refuse rather than serve a stale answer silently. An exhausted
            # quota is an operational fact the caller must be able to see.
            raise HTTPException(429, "daily credential budget exhausted")
        payload = {"store": store, "zip": zip, "source": "kroger-api",
                   "note": "credentialled fetch would happen here",
                   "rows": []}
        POOL.put(key, payload, cost=20)          # ~20 calls per store scrape
        return {**payload, "pooled": False}

    if store in RENDERED:
        async with httpx.AsyncClient(timeout=90) as client:
            try:
                probe = await client.get(f"{RENDERER_URL}/health")
                probe.raise_for_status()
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"renderer unreachable: {exc}"[:150])
        payload = {"store": store, "zip": zip, "source": "rendered",
                   "renderer": RENDERER_URL, "rows": []}
        POOL.put(key, payload, cost=0)           # no credential is drawn
        return {**payload, "pooled": False}

    raise HTTPException(404, f"no route configured for store {store!r}")
