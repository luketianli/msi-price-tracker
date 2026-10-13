"""Entry point: scrape every enabled source, then alert if the price qualifies.

    python -m tracker.main --dry-run      # scrape and print, send nothing
    python -m tracker.main                # the real run (what cron calls)
    python -m tracker.main --test-email   # prove the SMTP secrets work
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import config as config_mod
from . import dashboard, notify, sources, state as state_mod
from .models import Listing, SourceResult

log = logging.getLogger("tracker")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def collect(cfg: dict) -> list[SourceResult]:
    """Run every enabled source."""
    results: list[SourceResult] = []
    product = cfg["product"]
    http_cfg = cfg["http"]

    for name, src_cfg in cfg["sources"].items():
        if not isinstance(src_cfg, dict) or not src_cfg.get("enabled", False):
            log.info("source %s: disabled, skipping", name)
            continue
        log.info("source %s: fetching", name)
        result = sources.run_source(name, src_cfg, product, http_cfg)
        if result.ok:
            for item in result.listings:
                log.info(
                    "  %-22s %8.2f %-11s %s",
                    item.retailer, item.price, item.condition,
                    "in stock" if item.in_stock is not False else "OUT OF STOCK",
                )
        else:
            log.warning("  failed: %s", result.error)
        results.append(result)

    return results


def qualifying(listings: list[Listing], alert_cfg: dict) -> list[Listing]:
    """Listings that are under the threshold, the right condition, and in stock."""
    threshold = float(alert_cfg["threshold"])
    allowed = {c.lower() for c in alert_cfg["conditions"]}
    return [
        item for item in listings
        if item.price <= threshold
        and item.condition.lower() in allowed
        and item.in_stock is not False
    ]


def _write_run_summary(lines: list[str]) -> None:
    """Surface the result on the Actions run page, not just in the log."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        log.warning("could not write step summary: %s", exc)


def _render_dashboard(cfg: dict) -> None:
    """Regenerate the static status page from whatever history we have."""
    dash_cfg = cfg.get("dashboard") or {}
    if not dash_cfg.get("enabled", True):
        log.info("dashboard disabled in config")
        return
    out = dashboard.DOCS_DIR / "index.html"
    try:
        dashboard.render(
            history_path=state_mod.HISTORY_PATH,
            state_path=state_mod.STATE_PATH,
            out_path=out,
            product_name=cfg["product"].get("name", "Tracked product"),
            threshold=float(cfg["alert"]["threshold"]),
            conditions=list(cfg["alert"]["conditions"]),
            repo_url=dash_cfg.get("repo_url"),
        )
    except OSError as exc:
        # A dashboard failure must never mask a successful price check.
        log.error("could not write dashboard: %s", exc)


def run(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    if args.threshold is not None:
        cfg["alert"]["threshold"] = args.threshold

    alert_cfg = cfg["alert"]
    product_name = cfg["product"].get("name", "Tracked product")
    currency = alert_cfg.get("currency", "USD")
    threshold = float(alert_cfg["threshold"])

    if args.render_only:
        _render_dashboard(cfg)
        return 0

    state = state_mod.load_state()

    if args.test_email:
        if not config_mod.email_configured(cfg):
            log.error("email is not configured; set SMTP_HOST/SMTP_USER/SMTP_PASS/ALERT_TO")
            return 2
        sample = Listing(
            source="test", retailer="Example Store", title=product_name,
            price=threshold - 25.0, currency=currency,
            url="https://example.com/listing", condition="new", in_stock=True,
        )
        subject, text, html_body = notify.build_price_alert(
            product_name, threshold, currency, [sample], [sample], {}
        )
        notify.send_email(cfg, f"[TEST] {subject}", text, html_body)
        log.info("test email sent to %s", cfg["email"]["to"])
        return 0

    results = collect(cfg)
    if not results:
        log.error("no sources are enabled in %s", args.config or "config.yml")
        return 2

    listings = [item for r in results if r.ok for item in r.listings]
    failures = [r for r in results if not r.ok]
    any_ok = any(r.ok for r in results)

    summary = [f"### {product_name}", ""]
    if listings:
        summary.append(f"Target: **under {notify._money(threshold, currency)}** "
                       f"(conditions: {', '.join(alert_cfg['conditions'])})")
        summary += ["", "| Price | Retailer | Condition | Source | Stock |",
                    "| --- | --- | --- | --- | --- |"]
        for item in sorted(listings, key=lambda l: l.price):
            stock = {True: "yes", False: "**no**", None: "?"}[item.in_stock]
            summary.append(
                f"| {notify._money(item.price, item.currency)} | {item.retailer} "
                f"| {item.condition} | {item.source} | {stock} |"
            )
    else:
        summary.append("No listings could be read this run.")
    if failures:
        summary += ["", "**Source failures**", ""]
        summary += [f"- `{r.source}`: {r.error}" for r in failures]

    # Record history before any alerting, so we keep data even if email fails.
    if not args.dry_run:
        state_mod.append_history(listings)
        state_mod.record_lowest(state, listings)

    hits = qualifying(listings, alert_cfg)
    cheapest_new = min(
        (l for l in listings if l.condition == "new"), key=lambda l: l.price, default=None
    )
    if cheapest_new:
        log.info(
            "cheapest NEW listing: %.2f at %s (target %.2f)",
            cheapest_new.price, cheapest_new.retailer, threshold,
        )

    to_send = [
        item for item in hits
        if state_mod.should_alert(
            state, item,
            cooldown_hours=float(alert_cfg["cooldown_hours"]),
            renotify_drop=float(alert_cfg["renotify_drop"]),
        )
    ]
    suppressed = len(hits) - len(to_send)
    if suppressed:
        log.info("%d qualifying listing(s) suppressed by cooldown", suppressed)

    streak = state_mod.note_run_outcome(state, any_ok)
    exit_code = 0

    if args.dry_run:
        log.info("dry run: would alert on %d listing(s)", len(to_send))
        for item in to_send:
            log.info("  ALERT %.2f %s [%s]", item.price, item.retailer, item.condition)
        summary += ["", f"_Dry run - {len(to_send)} alert(s) withheld._"]
        _write_run_summary(summary)
        return 0

    if to_send and not args.no_email:
        if not config_mod.email_configured(cfg):
            log.error("price qualified but email is not configured - no alert sent!")
            exit_code = 1
        else:
            subject, text, html_body = notify.build_price_alert(
                product_name, threshold, currency, to_send, listings,
                state.get("lowest_seen", {}),
            )
            try:
                notify.send_email(cfg, subject, text, html_body)
                state_mod.mark_alerted(state, to_send)
                summary += ["", f"✅ **Alert emailed** for {len(to_send)} listing(s)."]
            except notify.NotifyError as exc:
                log.error("%s", exc)
                summary += ["", f"❌ Email failed: {exc}"]
                exit_code = 1
    elif to_send:
        log.info("--no-email set; %d alert(s) not sent", len(to_send))
    else:
        log.info("nothing at or below %.2f %s", threshold, currency)

    # Tell the user when the tracker has gone blind, rather than letting
    # silence look like good news.
    if state_mod.health_alert_due(
        state, streak, int(alert_cfg["health_alert_after_failures"])
    ):
        if config_mod.email_configured(cfg) and not args.no_email:
            subject, text, html_body = notify.build_health_alert(
                product_name, failures, streak
            )
            try:
                notify.send_email(cfg, subject, text, html_body)
                state["last_health_alert"] = state_mod.utcnow().isoformat()
            except notify.NotifyError as exc:
                log.error("health alert failed: %s", exc)
        log.warning("all sources have failed for %d consecutive runs", streak)
        exit_code = exit_code or 1

    state_mod.save_state(state)
    _render_dashboard(cfg)
    _write_run_summary(summary)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tracker",
        description="Track the price of a monitor and email when it drops below a threshold.",
    )
    p.add_argument("--config", help="path to config.yml")
    p.add_argument("--dry-run", action="store_true",
                   help="scrape and report, but send no email and persist no state")
    p.add_argument("--no-email", action="store_true", help="skip sending email")
    p.add_argument("--test-email", action="store_true",
                   help="send one sample alert to verify SMTP credentials")
    p.add_argument("--threshold", type=float, help="override the alert threshold")
    p.add_argument("--render-only", action="store_true",
                   help="rebuild docs/index.html from stored history without scraping")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return run(args)
    except config_mod.ConfigError as exc:
        log.error("configuration error: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
