"""Source plumbing: every scraper is a callable returning a SourceResult."""

from __future__ import annotations

import logging
from typing import Callable

from ..http import FetchError
from ..models import SourceResult

log = logging.getLogger(__name__)

# A source takes (source_config, product_config, http_config) -> list[Listing]
SourceFn = Callable[[dict, dict, dict], list]

_REGISTRY: dict[str, SourceFn] = {}


def register(name: str):
    """Decorator registering a scraper under `name` (matches config.yml keys)."""

    def wrap(fn: SourceFn) -> SourceFn:
        _REGISTRY[name] = fn
        return fn

    return wrap


def available() -> dict[str, SourceFn]:
    return dict(_REGISTRY)


def run_source(name: str, src_cfg: dict, product: dict, http_cfg: dict) -> SourceResult:
    """Run one scraper, converting any failure into a recorded error.

    A source that raises must never take the run down with it -- a single
    retailer blocking us is normal, and the other sources still have
    something useful to say.
    """
    fn = _REGISTRY.get(name)
    if fn is None:
        return SourceResult(source=name, error=f"no scraper registered for '{name}'")
    try:
        listings = fn(src_cfg, product, http_cfg)
    except FetchError as exc:
        log.warning("source %s unreachable: %s", name, exc)
        return SourceResult(source=name, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - scrapers face arbitrary markup
        log.exception("source %s raised", name)
        return SourceResult(source=name, error=f"{type(exc).__name__}: {exc}")

    if not listings:
        return SourceResult(
            source=name,
            error="reachable but no listing matched the target model",
        )
    return SourceResult(source=name, listings=listings)
