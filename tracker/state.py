"""Persistent state: price history and alert de-duplication.

Both files live in `data/` and are committed back to the repo by the GitHub
Actions workflow, which is what gives this stateless cron job a memory --
no database, no paid add-on.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Listing

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_PATH = DATA_DIR / "state.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"

_EMPTY_STATE: dict = {
    "alerts": {},              # listing key -> {last_alert, last_price}
    "lowest_seen": {},         # listing key -> lowest price ever observed
    "consecutive_failures": 0,  # runs where every source failed
    "last_health_alert": None,
    "last_run": None,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    if not path.is_file():
        return json.loads(json.dumps(_EMPTY_STATE))
    try:
        with path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("state file unreadable (%s); starting fresh", exc)
        return json.loads(json.dumps(_EMPTY_STATE))
    for key, default in _EMPTY_STATE.items():
        state.setdefault(key, default)
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = utcnow().isoformat()
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)


def append_history(listings: list[Listing], path: Path | None = None) -> None:
    """Append one JSON line per observation, for charting price over time."""
    path = path or HISTORY_PATH
    if not listings:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().isoformat()
    with path.open("a", encoding="utf-8") as fh:
        for item in listings:
            fh.write(json.dumps({
                "ts": stamp,
                "source": item.source,
                "retailer": item.retailer,
                "condition": item.condition,
                "price": item.price,
                "currency": item.currency,
                "in_stock": item.in_stock,
                "url": item.url,
            }, sort_keys=True) + "\n")


def record_lowest(state: dict, listings: list[Listing]) -> None:
    lowest = state.setdefault("lowest_seen", {})
    for item in listings:
        prev = lowest.get(item.key)
        if prev is None or item.price < prev:
            lowest[item.key] = item.price


def should_alert(
    state: dict,
    listing: Listing,
    *,
    cooldown_hours: float,
    renotify_drop: float,
) -> bool:
    """Has this listing already been alerted on recently at this price?

    Re-alerts when the cooldown has elapsed, or immediately when the price has
    fallen a meaningful amount further since the last email. This is what stops
    a 3-hourly cron from sending eight identical emails a day.
    """
    record = state.get("alerts", {}).get(listing.key)
    if not record:
        return True

    last_price = record.get("last_price")
    if isinstance(last_price, (int, float)) and listing.price <= last_price - renotify_drop:
        return True

    last_alert = record.get("last_alert")
    if not last_alert:
        return True
    try:
        when = datetime.fromisoformat(last_alert)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return utcnow() - when >= timedelta(hours=cooldown_hours)


def mark_alerted(state: dict, listings: list[Listing]) -> None:
    alerts = state.setdefault("alerts", {})
    stamp = utcnow().isoformat()
    for item in listings:
        alerts[item.key] = {"last_alert": stamp, "last_price": item.price}


def note_run_outcome(state: dict, any_source_ok: bool) -> int:
    """Track consecutive total failures; returns the new streak length."""
    if any_source_ok:
        state["consecutive_failures"] = 0
    else:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    return state["consecutive_failures"]


def health_alert_due(state: dict, streak: int, threshold: int, cooldown_hours: float = 24.0) -> bool:
    """Only warn once per cooldown window, however long the outage lasts."""
    if streak < threshold:
        return False
    last = state.get("last_health_alert")
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return utcnow() - when >= timedelta(hours=cooldown_hours)
