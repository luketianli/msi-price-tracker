"""Newegg scraper.

Two strategies, in order of preference.

**1. The per-item realtime endpoint** (`/product/api/ProductRealtime`). Given an
item number it returns JSON with the live price, stock flag and the *full*
product title -- which, unlike the search page, spells out
"MAG 321UP QD-OLED X24" in full. This is the reliable path, and it is how we
get a trustworthy new-vs-open-box split, since the two SKUs differ only by a
trailing "R" and share an identical title.

**2. The search results page**, as a fallback. Newegg A/B-serves two templates
for `/p/pl`: a server-rendered one containing `div.item-cell` elements, and a
React shell whose results load client-side and therefore contain no prices at
all. The fallback handles the first and quietly finds nothing in the second --
which is precisely why strategy 1 exists.

On the search page Newegg's visible titles are spec blurbs that often omit the
model number, so there the model test runs against title *and* URL slug.
"""

from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup

from ..http import FetchError, fetch
from ..match import classify_condition, is_target_model, parse_price
from ..models import Listing
from .base import register

log = logging.getLogger(__name__)

API_URL = (
    "https://www.newegg.com/product/api/ProductRealtime"
    "?ItemGroup=Single&ItemNumber={item}"
)
ITEM_PAGE = "https://www.newegg.com/p/{item}"

# The endpoint is an XHR target; it expects to be called like one.
_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Referer": "https://www.newegg.com/",
    "X-Requested-With": "XMLHttpRequest",
}


def _from_api(items: list, product: dict, http_cfg: dict) -> list[Listing]:
    """Query the realtime endpoint for each configured item number."""
    match_cfg = product.get("match", {})
    require_all = match_cfg.get("require_all", [])
    reject_any = match_cfg.get("reject_any", [])

    listings: list[Listing] = []
    for entry in items:
        if isinstance(entry, str):
            entry = {"item_number": entry}
        item_number = str(entry.get("item_number") or "").strip()
        if not item_number:
            continue

        try:
            raw = fetch(
                API_URL.format(item=item_number),
                timeout=http_cfg.get("timeout_seconds", 25),
                retries=http_cfg.get("retries", 3),
                backoff=http_cfg.get("backoff_seconds", 2.0),
                extra_headers=_API_HEADERS,
            )
            payload = json.loads(raw)
        except FetchError as exc:
            log.warning("newegg api %s: %s", item_number, exc)
            continue
        except json.JSONDecodeError as exc:
            log.warning("newegg api %s: response was not JSON (%s)", item_number, exc)
            continue

        main = payload.get("MainItem") or {}
        title = str((main.get("Description") or {}).get("Title") or "")
        if not title:
            log.warning("newegg api %s: no title in response", item_number)
            continue

        # The API titles name the model in full, so this check is meaningful:
        # it catches a SKU that Newegg has re-pointed at a different monitor.
        if not is_target_model(title, require_all, reject_any):
            log.warning(
                "newegg api %s: title is not the target model (%r)", item_number, title[:80]
            )
            continue

        price = parse_price(str(main.get("FinalPrice") or ""))
        if price is None:
            log.info("newegg api %s: no usable price (price may be hidden)", item_number)
            continue

        # Condition comes from config: the open-box SKU shares the new SKU's
        # title verbatim, so there is nothing in the response to infer it from.
        condition = str(entry.get("condition") or "").strip().lower()
        if not condition:
            condition = classify_condition(title)

        listings.append(
            Listing(
                source="newegg",
                retailer="Newegg",
                title=title,
                price=price,
                currency="USD",
                url=str(entry.get("url") or ITEM_PAGE.format(item=item_number)),
                condition=condition,
                in_stock=bool(main.get("Instock")),
            )
        )

    return listings


def _from_search(url: str, product: dict, http_cfg: dict) -> list[Listing]:
    """Parse the server-rendered search results template, if we got it."""
    html = fetch(
        url,
        timeout=http_cfg.get("timeout_seconds", 25),
        retries=http_cfg.get("retries", 3),
        backoff=http_cfg.get("backoff_seconds", 2.0),
    )
    soup = BeautifulSoup(html, "html.parser")
    match_cfg = product.get("match", {})
    require_all = match_cfg.get("require_all", [])
    reject_any = match_cfg.get("reject_any", [])

    cells = soup.select("div.item-cell")
    if not cells:
        log.info("newegg search: client-rendered template, no prices in HTML")
        return []
    log.info("newegg search: %d result cell(s)", len(cells))

    listings: list[Listing] = []
    for cell in cells:
        title_el = cell.select_one("a.item-title")
        if title_el is None:
            continue
        title = title_el.get_text(" ", strip=True)
        item_url = title_el.get("href") or url

        # Search-page titles often omit the model; the slug carries it.
        if not is_target_model(f"{title} {item_url}", require_all, reject_any):
            continue

        price_el = cell.select_one("li.price-current")
        price = parse_price(price_el.get_text(" ", strip=True)) if price_el else None
        if price is None:
            log.info("newegg search: skipping %r - no readable price", title[:60])
            continue

        promo = cell.select_one("p.item-promo, span.item-promo")
        badge = promo.get_text(" ", strip=True) if promo else ""
        out_of_stock = bool(cell.select_one("p.item-promo-out-of-stock")) or (
            "out of stock" in cell.get_text(" ", strip=True).lower()
        )

        listings.append(
            Listing(
                source="newegg",
                retailer="Newegg",
                title=title,
                price=price,
                currency="USD",
                url=item_url,
                condition=classify_condition(title, badge),
                in_stock=not out_of_stock,
            )
        )

    return listings


@register("newegg")
def scrape(src_cfg: dict, product: dict, http_cfg: dict) -> list[Listing]:
    listings: list[Listing] = []

    items = src_cfg.get("items") or []
    if items:
        listings = _from_api(items, product, http_cfg)
        log.info("newegg: %d listing(s) from realtime api", len(listings))

    if not listings and src_cfg.get("url"):
        log.info("newegg: falling back to search page")
        listings = _from_search(src_cfg["url"], product, http_cfg)

    # Cheapest per condition, so one retailer can't flood the alert email.
    best: dict[str, Listing] = {}
    for item in listings:
        current = best.get(item.condition)
        if current is None or item.price < current.price:
            best[item.condition] = item

    result = sorted(best.values(), key=lambda l: l.price)
    log.info("newegg: %d listing(s) after filtering", len(result))
    return result
