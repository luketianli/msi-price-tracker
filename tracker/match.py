"""Model identification, condition classification, and price parsing.

This module is the safety rail of the whole service. The Newegg search page
for "MAG 321UP" simultaneously returns:

  * MSI MAG 321UP QD-OLED X24, new            ~$838   <- what we want
  * MSI MAG 321UP QD-OLED X24, open box       ~$665
  * MSI MAG 321UP QD-OLED (165 Hz, no X24)    ~$671   <- different monitor
  * the same 165 Hz panel, used               ~$404

Three of those four sit at or near a $650 threshold, so a tracker that just
takes the cheapest "321UP" result would fire a useless alert on its first run.
Everything here exists to tell those four apart.
"""

from __future__ import annotations

import re

# Matches "$838.26", "$ 1,179 .99", "838.26", "1,179.99"
_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\s*\.\s*\d{1,2})?|\d+(?:\s*\.\s*\d{1,2})?)")

_CONDITION_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    # Order matters: the first match wins, so the most specific phrases lead.
    ("open_box", ("OPEN BOX", "OPENBOX", "OPEN-BOX")),
    ("refurbished", ("REFURB", "RECERTIFIED", "RENEWED", "MANUFACTURER RECERT")),
    ("used", ("USED", "PRE-OWNED", "PREOWNED", "VERY GOOD", "LIKE NEW",
              "ACCEPTABLE", "SCRATCH & DENT", "SCRATCH AND DENT")),
]


def normalize(text: str) -> str:
    """Upper-case and strip every non-alphanumeric character.

    Collapses the many spellings of the model into one comparable form, so
    "MAG 321UP QD-OLED X24", "MAG321UPDEX24" and "321up-qd-oled-x24" all
    reduce to something containing both "321UP" and "X24".
    """
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def parse_price(raw: str) -> float | None:
    """Pull a price out of messy retailer markup.

    Newegg splits the price across elements, so the scraped text arrives as
    "$ 838 .26 -". Returns None when there is no usable number.
    """
    if not raw:
        return None
    # Keep digits, separators and the decimal point; drop currency and dashes.
    cleaned = re.sub(r"[^\d,.\s]", " ", raw)
    match = _PRICE_RE.search(cleaned)
    if not match:
        return None
    candidate = re.sub(r"[\s,]", "", match.group(1))
    try:
        value = float(candidate)
    except ValueError:
        return None
    # Guard against picking up a review count or a "15W" spec as a price.
    return value if 1.0 <= value <= 100_000.0 else None


def classify_condition(title: str, extra: str = "") -> str:
    """Infer item condition from a listing title and any badge text."""
    haystack = f"{title} {extra}".upper()
    for condition, needles in _CONDITION_PATTERNS:
        if any(n in haystack for n in needles):
            return condition
    return "new"


def is_target_model(title: str, require_all: list[str], reject_any: list[str]) -> bool:
    """True only if `title` identifies the exact monitor we are tracking."""
    norm = normalize(title)
    if not norm:
        return False
    if any(normalize(tok) in norm for tok in reject_any if tok.strip()):
        return False
    return all(normalize(tok) in norm for tok in require_all if tok.strip())
