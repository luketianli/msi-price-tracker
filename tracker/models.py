"""Core data types."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# Conditions, cheapest-is-most-suspicious first. Used for display ordering too.
CONDITIONS = ("new", "open_box", "refurbished", "used", "unknown")


@dataclass(frozen=True)
class Listing:
    """A single priced offer for the product at one retailer."""

    source: str           # which scraper produced this ("newegg", "smartprix", ...)
    retailer: str         # who is actually selling ("Newegg", "Amazon", ...)
    title: str
    price: float
    currency: str
    url: str
    condition: str = "unknown"
    in_stock: Optional[bool] = None

    @property
    def key(self) -> str:
        """Stable identity for de-duplicating alerts across runs.

        Deliberately excludes the price so that a price change maps to the
        same key, and includes the condition so a used unit can never
        suppress an alert for a new one.
        """
        return f"{self.source}:{self.retailer}:{self.condition}".lower()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


@dataclass
class SourceResult:
    """Outcome of running one source, successful or not."""

    source: str
    listings: list[Listing] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None
