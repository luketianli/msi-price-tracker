"""Walmart scraper -- best effort.

Walmart serves real prices to datacenter IPs, but the delivery mechanism moves
around: sometimes a `__NEXT_DATA__` blob, sometimes other embedded JSON, and
sometimes an interstitial. We try the structured paths first and fall back to
reading a price out of the JSON near the model string.

This source failing is expected and non-fatal.
"""

from __future__ import annotations

import json
import logging
import re

from ..http import fetch
from ..match import classify_condition, is_target_model, parse_price
from ..models import Listing
from .base import register

log = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)
# Fallback: a "price":123.45 within ~1200 chars of the model name.
_NEARBY_PRICE_RE = re.compile(r'"(?:currentPrice|price)"\s*:\s*\{?\s*"?(?:price"\s*:\s*)?([\d.]{2,9})')


def _walk_products(node, out: list[dict]) -> None:
    """Collect dict nodes that look like a product with a price."""
    if isinstance(node, dict):
        name = node.get("name")
        if isinstance(name, str) and "321UP" in name.upper():
            price_info = node.get("priceInfo")
            price = None
            if isinstance(price_info, dict):
                current = price_info.get("currentPrice")
                if isinstance(current, dict):
                    price = current.get("price")
            if price is None:
                price = node.get("price")
            if price is not None:
                out.append(
                    {
                        "name": name,
                        "price": price,
                        "url": node.get("canonicalUrl") or node.get("productUrl") or "",
                        "availability": str(
                            node.get("availabilityStatusDisplayValue")
                            or node.get("availabilityStatus")
                            or ""
                        ),
                    }
                )
        for value in node.values():
            _walk_products(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_products(value, out)


def _from_structured(html: str) -> list[dict]:
    found: list[dict] = []
    match = _NEXT_DATA_RE.search(html)
    blobs = [match.group(1)] if match else []
    if not blobs:
        blobs = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S)
    for blob in blobs:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        _walk_products(data, found)
    return found


def _from_proximity(html: str) -> list[dict]:
    """Last resort: scan a window after each model mention for a price."""
    found: list[dict] = []
    for m in re.finditer(r"321UP", html, re.I):
        window = html[m.start(): m.start() + 1200]
        price_match = _NEARBY_PRICE_RE.search(window)
        if not price_match:
            continue
        # Recover enough surrounding text to run the model test on.
        name_match = re.search(r'"name"\s*:\s*"([^"]{10,200})"', html[max(0, m.start() - 600): m.start() + 600])
        found.append(
            {
                "name": name_match.group(1) if name_match else "MSI MAG 321UP QD-OLED X24",
                "price": price_match.group(1),
                "url": "",
                "availability": "",
            }
        )
    return found


@register("walmart")
def scrape(src_cfg: dict, product: dict, http_cfg: dict) -> list[Listing]:
    html = fetch(
        src_cfg["url"],
        timeout=http_cfg.get("timeout_seconds", 25),
        retries=http_cfg.get("retries", 3),
        backoff=http_cfg.get("backoff_seconds", 2.0),
    )
    match_cfg = product.get("match", {})
    require_all = match_cfg.get("require_all", [])
    reject_any = match_cfg.get("reject_any", [])

    candidates = _from_structured(html) or _from_proximity(html)

    listings: list[Listing] = []
    for cand in candidates:
        if not is_target_model(cand["name"], require_all, reject_any):
            continue
        price = parse_price(str(cand["price"]))
        if price is None:
            continue
        url = cand["url"] or src_cfg["url"]
        if url.startswith("/"):
            url = f"https://www.walmart.com{url}"
        availability = cand["availability"].lower()
        listings.append(
            Listing(
                source="walmart",
                retailer="Walmart",
                title=cand["name"],
                price=price,
                currency="USD",
                url=url,
                condition=classify_condition(cand["name"]),
                in_stock=None if not availability else ("out of stock" not in availability),
            )
        )

    best: dict[str, Listing] = {}
    for item in listings:
        current = best.get(item.condition)
        if current is None or item.price < current.price:
            best[item.condition] = item

    result = sorted(best.values(), key=lambda l: l.price)
    log.info("walmart: %d matching listing(s)", len(result))
    return result
