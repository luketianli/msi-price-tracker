"""Tests for the Amazon scraper, against HTML captured on a real CI runner.

`tests/fixtures/amazon_search.html` is a trimmed copy of the actual search page
a GitHub Actions runner receives for "MSI MAG 321UP QD-OLED X24". It contains
16 results, including several monitors that are cheaper than the $650 target
and would fire a false alert if the model match were loose.
"""

from pathlib import Path

import pytest

from tracker.http import FetchError
from tracker.sources import amazon

FIXTURE = (Path(__file__).parent / "fixtures" / "amazon_search.html").read_text(
    encoding="utf-8"
)

PRODUCT = {
    "match": {
        "require_all": ["321UP", "X24"],
        "reject_any": ["BUNDLE", "STAND ONLY", "REPLACEMENT PANEL"],
    }
}
HTTP = {"timeout_seconds": 5, "retries": 1, "backoff_seconds": 0}
CFG = {"url": "https://www.amazon.com/s?k=MSI+MAG+321UP+QD-OLED+X24"}


@pytest.fixture
def listings(monkeypatch):
    monkeypatch.setattr(amazon, "fetch", lambda *a, **k: FIXTURE)
    return amazon.scrape(CFG, PRODUCT, HTTP)


class TestRealPageParsing:
    def test_finds_the_x24_and_nothing_else(self, listings):
        assert len(listings) == 1
        item = listings[0]
        assert item.retailer == "Amazon"
        assert item.condition == "new"
        # Of the two X24 listings on the page, one has no price at all; the
        # other is a marketplace seller at $1,072.05.
        assert item.price == 1072.05
        assert "321UP-QD-OLED-X24" in item.url

    @pytest.mark.parametrize("price,what", [
        (671.14, "the 165 Hz MAG 321UP, no X24"),
        (629.00, "MAG 272UP QD-OLED X24 - a 27in panel, already under target"),
        (349.99, "MAG 272QP QD-OLED X24 - also 27in"),
        (704.99, "MAG 322UP QD-OLED E16"),
        (749.99, "MPG 321URXW"),
        (679.99, "MPG 271QR QD-OLED X50"),
        (519.99, "MAG 271QPX"),
    ])
    def test_near_miss_models_are_rejected(self, listings, price, what):
        assert price not in {l.price for l in listings}, f"leaked: {what}"

    def test_no_sub_target_price_leaks_through(self, listings):
        """The page contains monitors at $629, $349 and $269. None of them is
        this monitor, so none may appear."""
        assert all(l.price > 650 for l in listings)

    def test_url_is_absolute_and_stripped_of_tracking(self, listings):
        url = listings[0].url
        assert url.startswith("https://www.amazon.com/")
        assert "/ref=" not in url
        assert "?" not in url


class TestModelMatching:
    def test_asin_cannot_satisfy_the_x24_token(self, monkeypatch):
        """ASINs are alphanumeric, so one shaped like B0X24... would match
        "X24" if the whole href were tested. Only the slug may be."""
        html = """
        <div data-component-type="s-search-result" data-asin="B0X24ABCDE">
          <h2><a class="a-link-normal"
                href="/MSI-MAG-321UP-QD-OLED-Adaptive/dp/B0X24ABCDE/ref=sr_1_1">msi</a></h2>
          <span class="a-price"><span class="a-offscreen">$671.14</span></span>
        </div>"""
        monkeypatch.setattr(amazon, "fetch", lambda *a, **k: html)
        assert amazon.scrape(CFG, PRODUCT, HTTP) == []

    def test_accepts_the_x24_slug(self, monkeypatch):
        html = """
        <div data-component-type="s-search-result" data-asin="B0H616RSZQ">
          <h2><a class="a-link-normal"
                href="/msi-MAG-321UP-QD-OLED-X24/dp/B0H616RSZQ/ref=sr_1_1">msi</a></h2>
          <span class="a-price"><span class="a-offscreen">$739.00</span></span>
        </div>"""
        monkeypatch.setattr(amazon, "fetch", lambda *a, **k: html)
        out = amazon.scrape(CFG, PRODUCT, HTTP)
        assert [l.price for l in out] == [739.00]


class TestPriceAndStock:
    def _one(self, monkeypatch, inner):
        html = (
            '<div data-component-type="s-search-result" data-asin="B0H616RSZQ">'
            '<h2><a class="a-link-normal" '
            'href="/msi-MAG-321UP-QD-OLED-X24/dp/B0H616RSZQ">msi</a></h2>'
            + inner + "</div>"
        )
        monkeypatch.setattr(amazon, "fetch", lambda *a, **k: html)
        return amazon.scrape(CFG, PRODUCT, HTTP)

    def test_falls_back_to_whole_and_fraction(self, monkeypatch):
        out = self._one(monkeypatch, (
            '<span class="a-price"><span class="a-price-whole">812.</span>'
            '<span class="a-price-fraction">49</span></span>'
        ))
        assert [l.price for l in out] == [812.49]

    def test_listing_without_a_price_is_skipped(self, monkeypatch):
        assert self._one(monkeypatch, "") == []

    def test_currently_unavailable_is_marked_out_of_stock(self, monkeypatch):
        out = self._one(monkeypatch, (
            '<span class="a-price"><span class="a-offscreen">$739.00</span></span>'
            "<span>Currently unavailable</span>"
        ))
        assert out[0].in_stock is False

    def test_renewed_units_are_classified(self, monkeypatch):
        html = """
        <div data-component-type="s-search-result" data-asin="B0H616RSZQ">
          <h2><a class="a-link-normal"
                href="/msi-MAG-321UP-QD-OLED-X24-Renewed/dp/B0H616RSZQ">msi</a></h2>
          <span class="a-price"><span class="a-offscreen">$540.00</span></span>
        </div>"""
        monkeypatch.setattr(amazon, "fetch", lambda *a, **k: html)
        out = amazon.scrape(CFG, PRODUCT, HTTP)
        assert out[0].condition == "refurbished"

    def test_cheapest_wins_per_condition(self, monkeypatch):
        html = "".join(
            '<div data-component-type="s-search-result" data-asin="A%d">'
            '<h2><a class="a-link-normal" '
            'href="/msi-MAG-321UP-QD-OLED-X24/dp/A%d">msi</a></h2>'
            '<span class="a-price"><span class="a-offscreen">$%s</span></span></div>'
            % (i, i, p)
            for i, p in enumerate(["1,072.05", "739.00", "988.00"])
        )
        monkeypatch.setattr(amazon, "fetch", lambda *a, **k: html)
        out = amazon.scrape(CFG, PRODUCT, HTTP)
        assert [l.price for l in out] == [739.00]


class TestBotWall:
    @pytest.mark.parametrize("marker", [
        "Enter the characters you see below",
        "/errors/validateCaptcha",
        "api-services-support@amazon.com",
    ])
    def test_challenge_page_raises_instead_of_reporting_no_data(self, monkeypatch, marker):
        """A CAPTCHA must surface as a source failure, not as 'no listings',
        so the health alert can see it."""
        monkeypatch.setattr(amazon, "fetch", lambda *a, **k: f"<html>{marker}</html>")
        with pytest.raises(ValueError, match="bot-challenge"):
            amazon.scrape(CFG, PRODUCT, HTTP)

    def test_fetch_error_propagates(self, monkeypatch):
        def boom(*a, **k):
            raise FetchError("HTTP 503")

        monkeypatch.setattr(amazon, "fetch", boom)
        with pytest.raises(FetchError):
            amazon.scrape(CFG, PRODUCT, HTTP)
