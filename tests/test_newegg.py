"""Offline test of the Newegg scraper against a fixture built from the real page.

The fixture reproduces the four-way ambiguity the live search returns, three
of which sit near the $650 threshold:

  new X24            $838.26  <- track
  open-box X24       $664.99  <- track, but not alert-worthy by default
  new 165 Hz model   $671.14  <- wrong monitor, must be dropped
  used 165 Hz model  $403.99  <- wrong monitor, must be dropped
"""

import json

from tracker.http import FetchError
from tracker.sources import newegg

PRODUCT = {
    "match": {
        "require_all": ["321UP", "X24"],
        "reject_any": ["BUNDLE", "STAND ONLY", "REPLACEMENT PANEL"],
    }
}
HTTP = {"timeout_seconds": 5, "retries": 1, "backoff_seconds": 0}

FIXTURE = """
<html><body>
  <div class="item-cell">
    <a class="item-title"
       href="https://www.newegg.com/msi-321up-qd-oled-x24-32-qd-oled-240hz-mag/p/N82E16824475582">
       MSI 32 inch UHD 4K 240Hz AMD FreeSync Premium Pro &amp; G-Sync Compatible USB-C OLED</a>
    <li class="price-current">$<strong>838</strong><sup>.26</sup> &ndash;</li>
  </div>
  <div class="item-cell">
    <a class="item-title"
       href="https://www.newegg.com/msi-mag-321up-qd-oled-x24-32-uhd-240hz/p/N82E16824475582R">
       Open BoxMSI 32 inch UHD 4K 240Hz AMD FreeSync Premium Pro USB-C</a>
    <li class="price-current">$<strong>664</strong><sup>.99</sup> &ndash;</li>
  </div>
  <div class="item-cell">
    <a class="item-title"
       href="https://www.newegg.com/msi-mag-321up-qd-oled-32-uhd-165-hz-metallic-black/p/N82E16824475398">
       MSI 32" (31.5" viewable) UHD 4K 165Hz with AMD FreeSync Premium Pro</a>
    <li class="price-current">$<strong>671</strong><sup>.14</sup> &ndash;</li>
  </div>
  <div class="item-cell">
    <a class="item-title"
       href="https://www.newegg.com/msi-mag-321up-qd-oled-32-uhd-165hz-metallic-black/p/9UD24-475-398">
       Used - AcceptableMSI 32" (31.5" viewable) UHD 4K 165Hz</a>
    <li class="price-current">$<strong>403</strong><sup>.99</sup> &ndash;</li>
  </div>
</body></html>
"""


def test_only_x24_variants_survive(monkeypatch):
    monkeypatch.setattr(newegg, "fetch", lambda *a, **k: FIXTURE)
    listings = newegg.scrape({"url": "https://example.test"}, PRODUCT, HTTP)

    by_condition = {l.condition: l for l in listings}
    assert set(by_condition) == {"new", "open_box"}
    assert by_condition["new"].price == 838.26
    assert by_condition["open_box"].price == 664.99

    # The wrong-monitor prices must be absent entirely.
    prices = {l.price for l in listings}
    assert 671.14 not in prices, "the 165 Hz non-X24 model leaked through"
    assert 403.99 not in prices, "a used 165 Hz unit leaked through"


def test_cheapest_per_condition_is_kept(monkeypatch):
    doubled = FIXTURE.replace("</body>", """
      <div class="item-cell">
        <a class="item-title"
           href="https://www.newegg.com/msi-321up-qd-oled-x24-mag/p/N82E16824475582">
           MSI 32 inch UHD 4K 240Hz OLED</a>
        <li class="price-current">$<strong>799</strong><sup>.00</sup></li>
      </div></body>""")
    monkeypatch.setattr(newegg, "fetch", lambda *a, **k: doubled)
    listings = newegg.scrape({"url": "https://example.test"}, PRODUCT, HTTP)
    new_items = [l for l in listings if l.condition == "new"]
    assert len(new_items) == 1
    assert new_items[0].price == 799.00


def test_listing_without_price_is_skipped(monkeypatch):
    broken = """
    <div class="item-cell">
      <a class="item-title" href="/msi-321up-qd-oled-x24/p/N82E1">MSI 240Hz OLED</a>
    </div>"""
    monkeypatch.setattr(newegg, "fetch", lambda *a, **k: broken)
    assert newegg.scrape({"url": "https://example.test"}, PRODUCT, HTTP) == []


def test_react_shell_yields_nothing_rather_than_guessing(monkeypatch):
    """Newegg sometimes serves a client-rendered page with no prices in it.
    That must read as 'no data', never as a price."""
    shell = "<html><head><title>MAG 321UP | Newegg.com</title></head><body></body></html>"
    monkeypatch.setattr(newegg, "fetch", lambda *a, **k: shell)
    assert newegg.scrape({"url": "https://example.test"}, PRODUCT, HTTP) == []


# --- realtime API path -------------------------------------------------------

API_TITLE = (
    "MSI 32 inch UHD 4K 240Hz AMD FreeSync Premium Pro & G-Sync Compatible USB-C "
    "OLED Gaming Computer Monitor with Height Adjustable, Swivel, Pivot & Tilt "
    "Stand MAG 321UP QD-OLED X24"
)

API_CFG = {
    "url": "https://example.test/search",
    "items": [
        {"item_number": "N82E16824475582", "condition": "new"},
        {"item_number": "N82E16824475582R", "condition": "open_box"},
    ],
}


def _api_payload(title, price, instock):
    return json.dumps({
        "MainItem": {
            "Description": {"Title": title},
            "FinalPrice": price,
            "Instock": instock,
        }
    })


def test_api_path_reads_price_stock_and_declared_condition(monkeypatch):
    responses = {
        "N82E16824475582": _api_payload(API_TITLE, 781.99, False),
        "N82E16824475582R": _api_payload(API_TITLE, 680.06, True),
    }

    def fake_fetch(url, **kwargs):
        for item, body in responses.items():
            # The open-box SKU is the new SKU plus "R"; match the longer first.
            if f"ItemNumber={item}" in url and url.endswith(item):
                return body
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(newegg, "fetch", fake_fetch)
    listings = newegg.scrape(API_CFG, PRODUCT, HTTP)

    by_condition = {l.condition: l for l in listings}
    assert set(by_condition) == {"new", "open_box"}

    assert by_condition["new"].price == 781.99
    assert by_condition["new"].in_stock is False
    # Identical titles -- the split must come from config, not the title.
    assert by_condition["open_box"].price == 680.06
    assert by_condition["open_box"].in_stock is True


def test_api_rejects_a_sku_repointed_at_another_product(monkeypatch):
    monkeypatch.setattr(
        newegg, "fetch",
        lambda *a, **k: _api_payload("MSI MAG 321UP QD-OLED 165Hz", 671.14, True),
    )
    assert newegg.scrape(API_CFG, PRODUCT, HTTP) == []


def test_api_failure_falls_back_to_search(monkeypatch):
    def fake_fetch(url, **kwargs):
        if "ProductRealtime" in url:
            raise FetchError("HTTP 503")
        return FIXTURE

    monkeypatch.setattr(newegg, "fetch", fake_fetch)
    listings = newegg.scrape(API_CFG, PRODUCT, HTTP)
    assert {l.condition for l in listings} == {"new", "open_box"}
    assert {l.price for l in listings} == {838.26, 664.99}


def test_api_tolerates_malformed_json(monkeypatch):
    monkeypatch.setattr(
        newegg, "fetch",
        lambda url, **k: "not json" if "ProductRealtime" in url else FIXTURE,
    )
    # Falls back to the search page rather than raising.
    assert newegg.scrape(API_CFG, PRODUCT, HTTP)
