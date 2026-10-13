"""Generic schema.org / JSON-LD product scraper.

Most retailers publish a Product node with an Offer for SEO reasons, so this
one scraper covers any product page you add to `sources.jsonld.urls` without
new code. It is disabled by default because B&H, Micro Center, Adorama and
PCPartPicker all answer HTTP 403 to GitHub Actions IP ranges -- enable it if
you run the tracker somewhere with a residential address.
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


def _iter_products(data):
    """Yield Product dicts from an arbitrary JSON-LD document."""
    if isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                yield from _iter_products(node)
        types = data.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in [t for t in types if isinstance(t, str)]:
            yield data
        for value in data.values():
            if isinstance(value, (dict, list)):
                yield from _iter_products(value)
    elif isinstance(data, list):
        for node in data:
            yield from _iter_products(node)


def _offers(product: dict):
    offers = product.get("offers")
    if offers is None:
        return
    for offer in offers if isinstance(offers, list) else [offers]:
        if isinstance(offer, dict):
            if offer.get("@type") == "AggregateOffer":
                yield offer.get("lowPrice") or offer.get("price"), offer
            else:
                yield offer.get("price"), offer


def _retailer_from_url(url: str) -> str:
    host = url.split("//", 1)[-1].split("/", 1)[0]
    host = host.removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title() or host


@register("jsonld")
def scrape(src_cfg: dict, product_cfg: dict, http_cfg: dict) -> list[Listing]:
    match_cfg = product_cfg.get("match", {})
    require_all = match_cfg.get("require_all", [])
    reject_any = match_cfg.get("reject_any", [])

    listings: list[Listing] = []
    errors: list[str] = []

    for url in src_cfg.get("urls", []):
        try:
            html = fetch(
                url,
                timeout=http_cfg.get("timeout_seconds", 25),
                retries=http_cfg.get("retries", 3),
                backoff=http_cfg.get("backoff_seconds", 2.0),
                via_proxy=bool(src_cfg.get("via_proxy")),
            )
        except FetchError as exc:
            errors.append(str(exc))
            continue

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for prod in _iter_products(data):
                name = prod.get("name")
                if not isinstance(name, str):
                    continue
                if not is_target_model(f"{name} {url}", require_all, reject_any):
                    continue
                for raw_price, offer in _offers(prod):
                    price = parse_price(str(raw_price or ""))
                    if price is None:
                        continue
                    availability = str(offer.get("availability") or "")
                    listings.append(
                        Listing(
                            source="jsonld",
                            retailer=_retailer_from_url(url),
                            title=name,
                            price=price,
                            currency=str(offer.get("priceCurrency") or "USD"),
                            url=str(offer.get("url") or url),
                            condition=classify_condition(
                                name, str(offer.get("itemCondition") or "")
                            ),
                            in_stock="InStock" in availability or availability == "",
                        )
                    )

    if not listings and errors:
        raise FetchError("; ".join(errors[:4]))

    # Cheapest per retailer+condition.
    best: dict[tuple[str, str], Listing] = {}
    for item in listings:
        k = (item.retailer, item.condition)
        if k not in best or item.price < best[k].price:
            best[k] = item

    result = sorted(best.values(), key=lambda l: l.price)
    log.info("jsonld: %d matching listing(s)", len(result))
    return result
