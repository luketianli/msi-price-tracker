"""Shared HTTP session.

Retailers reject obviously-automated clients, so we present a complete,
consistent set of browser headers and retry with backoff on the transient
status codes. One request per source per run, and no CAPTCHA solving.

By default every request goes out directly. Sources that opt in with
`via_proxy` are routed through whatever fetch service `FETCH_PROXY_TEMPLATE`
names, which is the only way to reach the retailers that refuse datacenter and
residential IPs alike; with that variable unset those sources simply stay off.
"""

from __future__ import annotations

import logging
import os
import random
import time
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="140", "Not=A?Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Some retailers (Micro Center, B&H, Best Buy, Adorama) refuse datacenter and
# ordinary residential IPs alike, so no parser can reach them unaided. Setting
# FETCH_PROXY_TEMPLATE to a fetch-service URL containing "{url}" lets the
# sources that opt in route through it, e.g.
#   https://api.scraperapi.com/?api_key=KEY&url={url}
# Left unset, nothing changes and those sources stay off.
PROXY_TEMPLATE_ENV = "FETCH_PROXY_TEMPLATE"


class FetchError(RuntimeError):
    """A source could not be read. Carries no price information."""


def proxy_configured() -> bool:
    return bool(os.environ.get(PROXY_TEMPLATE_ENV, "").strip())


def _apply_proxy(url: str) -> str:
    template = os.environ.get(PROXY_TEMPLATE_ENV, "").strip()
    if not template:
        raise FetchError(
            f"source requires a proxy but {PROXY_TEMPLATE_ENV} is not set"
        )
    if "{url}" not in template:
        raise FetchError(f"{PROXY_TEMPLATE_ENV} must contain the '{{url}}' placeholder")
    return template.replace("{url}", quote(url, safe=""))


def fetch(
    url: str,
    *,
    timeout: float = 25.0,
    retries: int = 3,
    backoff: float = 2.0,
    extra_headers: dict | None = None,
    via_proxy: bool = False,
) -> str:
    """GET `url` and return the body text, or raise FetchError."""
    if via_proxy:
        url = _apply_proxy(url)

    headers = dict(BASE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    last = "no attempt made"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            log.warning("fetch %s attempt %d/%d failed: %s", url, attempt, retries, last)
        else:
            if resp.status_code == 200:
                return resp.text
            last = f"HTTP {resp.status_code}"
            # 403/404 are verdicts, not hiccups -- don't burn retries on them.
            if resp.status_code not in RETRY_STATUS:
                raise FetchError(f"{url} -> {last}")
            log.warning("fetch %s attempt %d/%d: %s", url, attempt, retries, last)

        if attempt < retries:
            time.sleep(backoff * attempt + random.uniform(0, 0.75))

    raise FetchError(f"{url} -> {last} (after {retries} attempts)")
