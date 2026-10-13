"""Scraper registry.

Importing this package registers every scraper, so `base.available()` and
`base.run_source()` can find them by the names used in config.yml.
"""

from . import amazon, jsonld, newegg, smartprix, walmart  # noqa: F401  (side-effect imports)
from .base import available, register, run_source

__all__ = ["available", "register", "run_source"]
