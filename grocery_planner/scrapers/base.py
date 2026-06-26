"""Shared Flipp/Wishabi flyer client (the GFP-6 "shared scraper library").

This is the ONE place that owns the Flipp dependency. Flipp's endpoints are
undocumented and unauthenticated: there's no license/key, but expect ToS,
breakage, and rate-limit risk. If Flipp changes or blocks us, swap it here.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any

import httpx

FLIPP_DATA_URL = "https://flyers-ng.flippback.com/api/flipp/data"
FLIPP_ITEMS_URL = "https://flyers-ng.flippback.com/api/flipp/flyers/{flyer_id}/flyer_items"
USER_AGENT = "grocery-planner/0.1 (+local personal use)"


def generate_sid() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(16))


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_date(value: str | None) -> str:
    parsed = parse_dt(value)
    return parsed.date().isoformat() if parsed else ""


def normalize_price(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return f"{float(text):.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return text


class FlippClient:
    """Thin wrapper over the Flipp flyer + flyer-items endpoints."""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})

    def __enter__(self) -> "FlippClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch_flyers(self, postal_code: str) -> list[dict[str, Any]]:
        resp = self._client.get(
            FLIPP_DATA_URL,
            params={"locale": "en", "postal_code": postal_code, "sid": generate_sid()},
        )
        resp.raise_for_status()
        flyers = resp.json().get("flyers", [])
        if not isinstance(flyers, list):
            raise ValueError("Unexpected Flipp response: missing flyers list")
        return flyers

    def fetch_flyer_items(self, flyer_id: int | str) -> list[dict[str, Any]]:
        resp = self._client.get(
            FLIPP_ITEMS_URL.format(flyer_id=flyer_id),
            params={"locale": "en", "sid": generate_sid()},
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            raise ValueError(f"Unexpected Flipp response for flyer {flyer_id}")
        return items


def pick_weekly_flyer(
    flyers: list[dict[str, Any]], merchant: str, now: datetime
) -> dict[str, Any] | None:
    """Pick the active 'weekly' flyer for a merchant; fall back to newest."""
    candidates = [
        f for f in flyers
        if (f.get("merchant") or "").strip().lower() == merchant.lower()
        and "weekly" in (f.get("name") or "").lower()
    ]
    if not candidates:
        return None

    active = []
    for f in candidates:
        start, end = parse_dt(f.get("valid_from")), parse_dt(f.get("valid_to"))
        if start and end and start <= now <= end:
            active.append(f)

    pool = active or candidates
    pool.sort(key=lambda f: f.get("valid_from", ""), reverse=True)
    return pool[0]
