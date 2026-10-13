"""Configuration loading.

config.yml holds everything that isn't a secret; environment variables
override it so the GitHub Actions workflow can inject credentials without
them ever touching the repo.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"


class ConfigError(RuntimeError):
    pass


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str) -> int | None:
    value = _env_float(name)
    return int(value) if value is not None else None


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Read config.yml and apply environment overrides."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise ConfigError(f"config file not found: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    cfg.setdefault("product", {})
    cfg.setdefault("alert", {})
    cfg.setdefault("sources", {})
    cfg.setdefault("http", {})

    alert = cfg["alert"]
    alert.setdefault("threshold", 650.0)
    alert.setdefault("currency", "USD")
    alert.setdefault("conditions", ["new"])
    alert.setdefault("cooldown_hours", 24)
    alert.setdefault("renotify_drop", 10.0)
    alert.setdefault("health_alert_after_failures", 3)

    if (threshold := _env_float("PRICE_THRESHOLD")) is not None:
        alert["threshold"] = threshold
    if (cooldown := _env_int("ALERT_COOLDOWN_HOURS")) is not None:
        alert["cooldown_hours"] = cooldown
    if conditions := os.environ.get("ALERT_CONDITIONS", "").strip():
        alert["conditions"] = [c.strip().lower() for c in conditions.split(",") if c.strip()]

    # Email settings live only in the environment.
    cfg["email"] = {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587").strip() or 587),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASS", ""),
        "to": os.environ.get("ALERT_TO", "").strip(),
        "from": os.environ.get("ALERT_FROM", "").strip()
        or os.environ.get("SMTP_USER", "").strip(),
        "use_tls": os.environ.get("SMTP_TLS", "true").strip().lower() != "false",
    }

    if not isinstance(alert["threshold"], (int, float)) or alert["threshold"] <= 0:
        raise ConfigError(f"alert.threshold must be positive, got {alert['threshold']!r}")

    return cfg


def email_configured(cfg: dict) -> bool:
    email = cfg.get("email", {})
    return bool(email.get("host") and email.get("user") and email.get("password") and email.get("to"))
