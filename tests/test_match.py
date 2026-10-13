"""Tests for model identification, condition and price parsing.

The cases here are taken from the actual Newegg search results for
"MAG 321UP", which is where the dangerous near-misses live.
"""

import pytest

from tracker.match import classify_condition, is_target_model, normalize, parse_price

REQUIRE = ["321UP", "X24"]
REJECT = ["BUNDLE", "STAND ONLY", "REPLACEMENT PANEL"]


def target(title: str) -> bool:
    return is_target_model(title, REQUIRE, REJECT)


class TestIsTargetModel:
    @pytest.mark.parametrize("title", [
        "MSI MAG 321UP QD-OLED X24 32in UHD 240Hz",
        "msi-321up-qd-oled-x24-32-qd-oled-240hz-mag",          # Newegg URL slug
        "MSI MAG321UPDEX24 Gaming Monitor",                     # EU/B&H SKU spelling
        "MAG 321UP QD-OLED X24",
        "MSI 32 inch UHD 4K 240Hz OLED https://newegg.com/msi-321up-qd-oled-x24/p/N82E1",
    ])
    def test_accepts_the_x24(self, title):
        assert target(title)

    @pytest.mark.parametrize("title", [
        # The 165 Hz sibling. Sells near $650 -- must never alert.
        "MSI MAG 321UP QD-OLED 32in UHD 4K 165Hz Metallic Black",
        "msi-mag-321up-qd-oled-32-uhd-165-hz-metallic-black",
        # Different monitors entirely.
        "MSI MAG 321UPX QD-OLED 32in",
        "MSI MPG 271QRX QD-OLED X24",
        "LG UltraGear 32GS95UE 32in 4K OLED",
        "",
    ])
    def test_rejects_everything_else(self, title):
        assert not target(title)

    def test_rejects_non_monitor_skus(self):
        assert not target("MSI MAG 321UP QD-OLED X24 Stand Only")
        assert not target("MSI MAG 321UP QD-OLED X24 + Keyboard Bundle")

    def test_normalize_collapses_spelling(self):
        assert normalize("MAG 321UP QD-OLED X24") == "MAG321UPQDOLEDX24"
        assert normalize("mag-321up_qd.oled x24") == "MAG321UPQDOLEDX24"


class TestParsePrice:
    @pytest.mark.parametrize("raw,expected", [
        ("$ 838 .26 –", 838.26),      # Newegg splits price across elements
        ("$ 1,179 .99 –", 1179.99),
        ("$799.49", 799.49),
        ("799.49", 799.49),
        ("$664.99 –", 664.99),
        ("1,049.00", 1049.00),
        ("$650", 650.0),
    ])
    def test_reads_real_markup(self, raw, expected):
        assert parse_price(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", "Out of stock", "$", "N/A", None])
    def test_returns_none_for_junk(self, raw):
        assert parse_price(raw) is None

    def test_rejects_implausible_values(self):
        # A "15W" USB-C spec or a 0 must not become a price.
        assert parse_price("0") is None
        assert parse_price("0.00") is None


class TestClassifyCondition:
    @pytest.mark.parametrize("title,expected", [
        # Newegg concatenates the badge into the title text.
        ("Open BoxMSI 32 inch UHD 4K 240Hz", "open_box"),
        ("Used - AcceptableMSI 32in UHD 4K 165Hz", "used"),
        ("Used - Very GoodMSI 32in", "used"),
        ("MSI Certified Refurbished MAG 321UP X24", "refurbished"),
        ("MSI MAG 321UP QD-OLED X24 32in UHD 240Hz", "new"),
    ])
    def test_classifies(self, title, expected):
        assert classify_condition(title) == expected

    def test_badge_text_is_considered(self):
        assert classify_condition("MSI MAG 321UP X24", "Open Box") == "open_box"
