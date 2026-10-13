"""Amazon search-results scraper.

Worth knowing: Amazon serves a full 1 MB results page to a GitHub Actions
runner but a ~2 KB stub to other clients, so this parser was built and tested
against HTML captured on the runner itself (see `tools/probe_sources.py
--dump`). The fixture in `tests/test_amazon.py` is that capture, trimmed.

Amazon's search page is the richest source of near-misses in this project.
Searching for the X24 returns, among others:

  msi-MAG-321UP-QD-OLED-X24          (the monitor we want)
  MSI-MAG-321UP-QD-OLED-Adaptive     $671.14   the 165 Hz sibling
  msi-MAG-272UP-QD-OLED-X24          $629.00   a 27" X24 -- already under $650
  msi-MAG-272QP-QD-OLED-X24          $349.99   another 27" X24
  MSI-MAG-321UPX-QD-OLED-Quantum               "321UPX", not "321UP ... X24"

Two of those sit below the $650 target, so loose matching here would have
produced an immediate false alert for a monitor half the size.

Two parsing details matter:

* **Titles are unusable.** The `h2` in each result renders as just "msi" in
  the server HTML, so the model has to come from the product URL slug.
* **The slug must exclude the ASIN.** ASINs are alphanumeric, so one shaped
  like `B0X24...` would satisfy the "X24" token on its own and let the 165 Hz
  model through. Only the path segment before `/dp/` is matched.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..http import fetch
from ..match import classify_condition, is_target_model, parse_price
from ..models import Listing
from .base import register

log = logging.getLogger(__name__)

BASE = "https://www.amazon.com"

# Amazon bot walls don't use HTTP 403; they return 200 with a challenge page.
_BLOCK_HINTS = (
    "enter the characters you see below",
    "type the characters you see in this image",
    "/errors/validatecaptcha",
    "api-services-support@amazon.com",
)


def _slug(href: str) -> str:
    """The product-name portion of a result URL, without the ASIN."""
    path = href.split("?", 1)[0]
    return path.split("/dp/", 1)[0]


def _clean_url(href: str) -> str:
    """An absolute product URL with Amazon's tracking segments removed."""
    path = href.split("?", 1)[0]
    path = re.sub(r"/ref=[^/]*$", "", path)
    if not path.startswith("http"):
        path = BASE + ("" if path.startswith("/") else "/") + path
    return path


def _price_of(result) -> float | None:
    """Read a result's price, preferring the screen-reader copy."""
    off = result.select_one(".a-price .a-offscreen")
    if off:
        price = parse_price(off.get_text(strip=True))
        if price is not None:
            return price
    whole = result.select_one(".a-price-whole")
    if whole:
        frac = result.select_one(".a-price-fraction")
        text = whole.get_text(strip=True).rstrip(".,")
        if frac:
            text = f"{text}.{frac.get_text(strip=True)}"
        return parse_price(text)
    return None


@register("amazon")
def scrape(src_cfg: dict, product: dict, http_cfg: dict) -> list[Listing]:
    html = fetch(
        src_cfg["url"],
        timeout=http_cfg.get("timeout_seconds", 25),
        retries=http_cfg.get("retries", 3),
        backoff=http_cfg.get("backoff_seconds", 2.0),
    )

    low = html.lower()
    if any(hint in low for hint in _BLOCK_HINTS):
        # A challenge page is a failure, not "no listings" -- raising keeps it
        # out of the health counter's blind spot.
        raise ValueError("Amazon returned a bot-challenge page")

    soup = BeautifulSoup(html, "html.parser")
    match_cfg = product.get("match", {})
    require_all = match_cfg.get("require_all", [])
    reject_any = match_cfg.get("reject_any", [])

    results = soup.select('div[data-component-type="s-search-result"]')
    log.info("amazon: %d result block(s) on page", len(results))

    listings: list[Listing] = []
    for result in results:
        link = result.select_one("a.a-link-normal[href], h2 a[href]")
        href = link.get("href") if link else None
        if not href:
            continue

        slug = _slug(href)
        if not is_target_model(slug, require_all, reject_any):
            continue

        price = _price_of(result)
        if price is None:
            # No price usually means currently unavailable; there is nothing
            # to track, but it is worth seeing in the log.
            log.info("amazon: %s matched but has no price (unavailable?)", slug)
            continue

        text = result.get_text(" ", strip=True).lower()
        out_of_stock = any(
            m in text for m in ("currently unavailable", "out of stock",
                                "temporarily out of stock")
        )

        listings.append(
            Listing(
                source="amazon",
                retailer="Amazon",
                title=slug.strip("/").replace("-", " "),
                price=price,
                currency="USD",
                url=_clean_url(href),
                condition=classify_condition(slug, text[:400]),
                in_stock=not out_of_stock,
            )
        )

    # Several ASINs can list the same monitor (Amazon itself plus marketplace
    # sellers at a markup); keep the cheapest per condition.
    best: dict[str, Listing] = {}
    for item in listings:
        current = best.get(item.condition)
        if current is None or item.price < current.price:
            best[item.condition] = item

    result_list = sorted(best.values(), key=lambda l: l.price)
    log.info("amazon: %d matching listing(s) after filtering", len(result_list))
    return result_list
