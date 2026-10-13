"""Shared HTTP session.

Retailers reject obviously-automated clients, so we present a complete,
consistent set of browser headers and retry with backoff on the transient
status codes. This is plain polite scraping: no proxies, no CAPTCHA solving,
one request per source per run.
"""

from __future__ import annotations

import logging
import random
import time

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


class FetchError(RuntimeError):
    """A source could not be read. Carries no price information."""


def fetch(
    url: str,
    *,
    timeout: float = 25.0,
    retries: int = 3,
    backoff: float = 2.0,
    extra_headers: dict | None = None,
) -> str:
    """GET `url` and return the body text, or raise FetchError."""
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
