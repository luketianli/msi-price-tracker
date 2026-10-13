"""Report which retailer endpoints are reachable from wherever this runs.

Big retailers block datacenter IP ranges inconsistently, and a laptop's result
tells you nothing about what a GitHub Actions runner can reach. So this is
meant to be run *in CI* (the "Probe sources" workflow) before adding a source,
and again later when one goes dark.

    python tools/probe_sources.py

For each candidate it prints the status, whether the model name and a price
appear in the body, and whether the response looks like a bot wall. One
request per URL.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from tracker.http import BASE_HEADERS  # noqa: E402

PRICE_RE = re.compile(r"\$\s?([0-9]{3,4}(?:\.[0-9]{2})?)")
BLOCK_HINTS = (
    "captcha", "are you a human", "robot check", "access denied",
    "unusual traffic", "request unsuccessful", "pardon our interruption",
    "enable javascript and cookies", "bot detection", "cf-browser-verification",
    "attention required", "just a moment",
)

JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}

# (label, url, extra headers or None)
CANDIDATES: list[tuple[str, str, dict | None]] = [
    # --- controls: these already work, so they prove the probe itself is sane
    ("CONTROL smartprix",
     "https://us.smartprix.com/monitors/msi-mag-321up-qd-oled-x24-32-inch-ultra-hd-ppd19i2tvun8",
     None),
    ("CONTROL newegg-api",
     "https://www.newegg.com/product/api/ProductRealtime?ItemGroup=Single&ItemNumber=N82E16824475582",
     JSON_HEADERS),
    ("CONTROL walmart-search",
     "https://www.walmart.com/search?q=MSI+MAG+321UP+QD-OLED+X24", None),

    # --- Micro Center (explicitly requested)
    ("microcenter-search",
     "https://www.microcenter.com/search/search_results.aspx?Ntt=MAG+321UP+QD-OLED+X24", None),
    ("microcenter-search-short",
     "https://www.microcenter.com/search/search_results.aspx?Ntt=321UP", None),
    ("microcenter-sitemap-ish",
     "https://www.microcenter.com/product/696479/msi-mag-321up-qd-oled-x24-315-uhd-240-hz-oled-gaming-monitor", None),

    # --- Best Buy
    ("bestbuy-search",
     "https://www.bestbuy.com/site/searchpage.jsp?st=MAG+321UP+QD-OLED+X24", None),
    ("bestbuy-api-v1",
     "https://api.bestbuy.com/v1/products(search=321UP)?format=json", JSON_HEADERS),

    # --- B&H
    ("bhphoto-search",
     "https://www.bhphotovideo.com/c/search?q=MAG+321UP+QD-OLED+X24", None),
    ("bhphoto-product",
     "https://www.bhphotovideo.com/c/product/1884447-REG/msi_mag321updex24_mag_321up_qd_oled.html", None),

    # --- other majors
    ("adorama-search",
     "https://www.adorama.com/l/?searchinfo=MAG+321UP+QD-OLED", None),
    ("antonline-search",
     "https://www.antonline.com/search?search_query=MAG+321UP+QD-OLED+X24", None),
    ("target-search",
     "https://www.target.com/s?searchTerm=MSI+MAG+321UP+QD-OLED+X24", None),
    ("ebay-search",
     "https://www.ebay.com/sch/i.html?_nkw=MSI+MAG+321UP+QD-OLED+X24", None),
    ("amazon-product-search",
     "https://www.amazon.com/s?k=MSI+MAG+321UP+QD-OLED+X24", None),
    ("provantage",
     "https://www.provantage.com/service/searchsvcs/MAG+321UP", None),
    ("connection",
     "https://www.connection.com/search?searchTerm=MAG%20321UP%20QD-OLED%20X24", None),
    ("shi-texas",
     "https://texas.gs.shi.com/product/52020597/MAG-321UP-QD-OLED-X24-MONITOR-FLAT-32IN-3840X2160-UHD-240HZ", None),

    # --- aggregators that might cover several at once
    ("pcpartpicker",
     "https://pcpartpicker.com/search/?q=MAG+321UP+QD-OLED+X24", None),
    ("price-rank",
     "https://price-rank.com/p/240843", None),
    ("msi-us-store",
     "https://us-store.msi.com/search?q=321UP+QD-OLED+X24", None),
]


def probe(label: str, url: str, extra: dict | None) -> dict:
    headers = dict(BASE_HEADERS)
    if extra:
        headers.update(extra)
    row: dict = {"label": label, "url": url}
    try:
        r = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
    except requests.RequestException as exc:
        row.update(status="EXC", note=f"{type(exc).__name__}", model=False,
                   prices=[], blocked=False, jsonld=False, length=0)
        return row

    body = r.text
    low = body.lower()
    row.update(
        status=r.status_code,
        length=len(body),
        model="321up" in low,
        blocked=any(h in low for h in BLOCK_HINTS),
        jsonld="application/ld+json" in low,
        prices=sorted(set(PRICE_RE.findall(body)))[:5],
        note="",
        body=body,
    )
    return row


def main() -> int:
    # --dump a,b  saves those candidates' raw bodies next to the results, so a
    # parser can be developed and tested offline against what CI actually sees.
    dump: set[str] = set()
    for i, arg in enumerate(sys.argv):
        if arg == "--dump" and i + 1 < len(sys.argv):
            dump = {x.strip() for x in sys.argv[i + 1].split(",") if x.strip()}

    rows = []
    for label, url, extra in CANDIDATES:
        row = probe(label, url, extra)
        rows.append(row)
        if label in dump and row.get("body"):
            out = Path("dumps") / (label.replace(" ", "_") + ".html")
            out.parent.mkdir(exist_ok=True)
            out.write_text(row["body"], encoding="utf-8")
            print(f"  -> dumped {out} ({len(row['body'])} bytes)", flush=True)
        row.pop("body", None)
        verdict = (
            "USABLE" if row["model"] and row["prices"] and not row["blocked"]
            else "blocked" if row["blocked"]
            else "no-data"
        )
        print(
            f"{label:26} {str(row['status']):>4}  len={row['length']:>7}  "
            f"model={str(row['model']):5}  jsonld={str(row['jsonld']):5}  "
            f"blocked={str(row['blocked']):5}  {verdict:8}  prices={row['prices']}",
            flush=True,
        )
        time.sleep(1.5)  # be polite

    Path("probe-results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print("\n=== candidates worth implementing ===")
    good = [r for r in rows if r["model"] and r["prices"] and not r["blocked"]
            and not r["label"].startswith("CONTROL")]
    for r in good:
        print(f"  {r['label']}  prices={r['prices']}  jsonld={r['jsonld']}")
    if not good:
        print("  none - every non-control candidate was blocked or had no data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
