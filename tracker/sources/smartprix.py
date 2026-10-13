"""Smartprix aggregator.

One request yields current Amazon and Newegg pricing, and it answers HTTP 200
to datacenter IPs -- which makes it the backbone of this tracker, since
Amazon and B&H simply refuse GitHub Actions' address ranges.

The page is product-specific, so we verify the page's own product name before
trusting any row on it.
"""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from ..http import fetch
from ..match import classify_condition, is_target_model, parse_price
from ..models import Listing
from .base import register

log = logging.getLogger(__name__)

# "Amazon $799.49", "Best Buy $1,049.00"
_ROW_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 .&'\-]{1,28})\s*\$\s*([\d,]+(?:\.\d{1,2})?)$")

# Rows like "NEW (2) From $799.49" summarise the others; they name no retailer.
_SUMMARY_RE = re.compile(r"^\s*(NEW|USED|REFURB\w*)\b.*\bFROM\b", re.I)


def _product_name(soup: BeautifulSoup) -> str | None:
    """Read the page's schema.org Product name."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Product":
                name = node.get("name")
                if isinstance(name, str):
                    return name
    heading = soup.find("h1")
    return heading.get_text(" ", strip=True) if heading else None


def _jsonld_offer(soup: BeautifulSoup) -> tuple[float, str] | None:
    """Fallback: the aggregate best price from Product.offers."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict) or node.get("@type") != "Product":
                continue
            offers = node.get("offers")
            for offer in offers if isinstance(offers, list) else [offers]:
                if not isinstance(offer, dict):
                    continue
                price = parse_price(str(offer.get("price") or offer.get("lowPrice") or ""))
                if price is not None:
                    return price, str(offer.get("priceCurrency") or "USD")
    return None


@register("smartprix")
def scrape(src_cfg: dict, product: dict, http_cfg: dict) -> list[Listing]:
    html = fetch(
        src_cfg["url"],
        timeout=http_cfg.get("timeout_seconds", 25),
        retries=http_cfg.get("retries", 3),
        backoff=http_cfg.get("backoff_seconds", 2.0),
    )
    soup = BeautifulSoup(html, "html.parser")
    match_cfg = product.get("match", {})

    name = _product_name(soup)
    if not name or not is_target_model(
        name, match_cfg.get("require_all", []), match_cfg.get("reject_any", [])
    ):
        # The configured URL now points at a different product -- better to
        # report nothing than to track the wrong monitor.
        raise ValueError(f"page product name does not match target: {name!r}")

    listings: list[Listing] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a"):
        text = anchor.get_text(" ", strip=True)
        if "$" not in text or len(text) > 60 or _SUMMARY_RE.match(text):
            continue
        row = _ROW_RE.match(text)
        if not row:
            continue
        retailer = row.group(1).strip()
        price = parse_price(row.group(2))
        if price is None or retailer.lower() in seen:
            continue
        seen.add(retailer.lower())
        listings.append(
            Listing(
                source="smartprix",
                retailer=retailer,
                title=name,
                price=price,
                currency="USD",
                url=anchor.get("href") or src_cfg["url"],
                condition=classify_condition(text),
                in_stock=True,
            )
        )

    if not listings:
        offer = _jsonld_offer(soup)
        if offer:
            price, currency = offer
            listings.append(
                Listing(
                    source="smartprix",
                    retailer="Smartprix (best offer)",
                    title=name,
                    price=price,
                    currency=currency,
                    url=src_cfg["url"],
                    condition="new",
                    in_stock=True,
                )
            )

    log.info("smartprix: %d listing(s)", len(listings))
    return listings
