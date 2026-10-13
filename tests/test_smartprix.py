"""Offline test of the Smartprix aggregator parser."""

import pytest

from tracker.http import FetchError
from tracker.sources import smartprix

PRODUCT = {"match": {"require_all": ["321UP", "X24"], "reject_any": ["BUNDLE"]}}
HTTP = {"timeout_seconds": 5, "retries": 1, "backoff_seconds": 0}
CFG = {"url": "https://us.smartprix.com/monitors/msi-mag-321up-qd-oled-x24"}

LD_JSON = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"MSI MAG 321UP QD-OLED X24 32 inch Ultra HD",
 "offers":{"@type":"Offer","priceCurrency":"USD","price":799.49,
           "availability":"http://schema.org/InStock"}}
</script>
"""

FIXTURE = f"""
<html><body>
  {LD_JSON}
  <a href="https://l.smartprix.com/l?k=amazonkey">Amazon $799.49</a>
  <a href="https://l.smartprix.com/l?k=neweggkey">Newegg $838.26</a>
  <a href="">NEW (2) From $799.49</a>
</body></html>
"""


def test_parses_each_retailer_row(monkeypatch):
    monkeypatch.setattr(smartprix, "fetch", lambda *a, **k: FIXTURE)
    listings = smartprix.scrape(CFG, PRODUCT, HTTP)

    by_retailer = {l.retailer: l for l in listings}
    assert set(by_retailer) == {"Amazon", "Newegg"}, "summary row should not become a listing"
    assert by_retailer["Amazon"].price == 799.49
    assert by_retailer["Newegg"].price == 838.26
    assert all(l.condition == "new" for l in listings)
    assert all(l.currency == "USD" for l in listings)


def test_falls_back_to_jsonld_when_no_rows(monkeypatch):
    monkeypatch.setattr(smartprix, "fetch", lambda *a, **k: f"<html>{LD_JSON}</html>")
    listings = smartprix.scrape(CFG, PRODUCT, HTTP)
    assert len(listings) == 1
    assert listings[0].price == 799.49


def test_refuses_a_page_about_a_different_product(monkeypatch):
    """If Smartprix re-points the URL at the 165 Hz model, report nothing
    rather than quietly tracking the wrong monitor."""
    wrong = """
    <html><script type="application/ld+json">
    {"@type":"Product","name":"MSI MAG 321UP QD-OLED 32 inch 165Hz",
     "offers":{"@type":"Offer","price":671.14,"priceCurrency":"USD"}}
    </script>
    <a href="#">Amazon $671.14</a></html>"""
    monkeypatch.setattr(smartprix, "fetch", lambda *a, **k: wrong)
    with pytest.raises(ValueError, match="does not match target"):
        smartprix.scrape(CFG, PRODUCT, HTTP)


def test_fetch_failure_propagates(monkeypatch):
    def boom(*a, **k):
        raise FetchError("https://example.test -> HTTP 403")

    monkeypatch.setattr(smartprix, "fetch", boom)
    with pytest.raises(FetchError):
        smartprix.scrape(CFG, PRODUCT, HTTP)
