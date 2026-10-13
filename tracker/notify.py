"""Email notification over SMTP.

Uses a Gmail app password by default, but any SMTP provider works -- set
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS accordingly.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from .models import Listing, SourceResult

log = logging.getLogger(__name__)


class NotifyError(RuntimeError):
    pass


def _money(value: float, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, f"{currency} ")
    return f"{symbol}{value:,.2f}"


def send_email(cfg: dict, subject: str, text_body: str, html_body: str) -> None:
    email_cfg = cfg["email"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_cfg["from"]
    msg["To"] = email_cfg["to"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        if email_cfg["port"] == 465:
            with smtplib.SMTP_SSL(
                email_cfg["host"], email_cfg["port"],
                context=ssl.create_default_context(), timeout=30,
            ) as server:
                server.login(email_cfg["user"], email_cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(email_cfg["host"], email_cfg["port"], timeout=30) as server:
                server.ehlo()
                if email_cfg.get("use_tls", True):
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(email_cfg["user"], email_cfg["password"])
                server.send_message(msg)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise NotifyError(f"could not send email: {type(exc).__name__}: {exc}") from exc

    log.info("alert email sent to %s", email_cfg["to"])


def build_price_alert(
    product_name: str,
    threshold: float,
    currency: str,
    hits: list[Listing],
    all_listings: list[Listing],
    lowest_seen: dict[str, float],
) -> tuple[str, str, str]:
    """Render the 'price dropped' email. Returns (subject, text, html)."""
    cheapest = min(hits, key=lambda l: l.price)
    subject = (
        f"\U0001f4b0 {product_name} is {_money(cheapest.price, currency)} "
        f"at {cheapest.retailer} (under {_money(threshold, currency)})"
    )

    lines = [
        f"{product_name} dropped below your {_money(threshold, currency)} target.",
        "",
        "Matching listings:",
    ]
    for item in sorted(hits, key=lambda l: l.price):
        low = lowest_seen.get(item.key)
        extra = f"  (lowest ever seen: {_money(low, currency)})" if low else ""
        lines.append(
            f"  * {_money(item.price, currency)} - {item.retailer} "
            f"[{item.condition}]{extra}\n    {item.url}"
        )

    others = [l for l in all_listings if l not in hits]
    if others:
        lines += ["", "Everything else being tracked right now:"]
        for item in sorted(others, key=lambda l: l.price):
            lines.append(
                f"  - {_money(item.price, item.currency)} - {item.retailer} [{item.condition}]"
            )

    lines += ["", "-- MSI price tracker (GitHub Actions)"]
    text = "\n".join(lines)

    rows = "".join(
        f"<tr>"
        f"<td style='padding:8px 14px;font-weight:700;color:#0a7d33;white-space:nowrap'>"
        f"{html.escape(_money(i.price, currency))}</td>"
        f"<td style='padding:8px 14px'>{html.escape(i.retailer)}</td>"
        f"<td style='padding:8px 14px;color:#555'>{html.escape(i.condition)}</td>"
        f"<td style='padding:8px 14px'><a href='{html.escape(i.url)}'>View</a></td>"
        f"</tr>"
        for i in sorted(hits, key=lambda l: l.price)
    )
    other_rows = "".join(
        f"<tr>"
        f"<td style='padding:6px 14px;color:#444;white-space:nowrap'>"
        f"{html.escape(_money(i.price, i.currency))}</td>"
        f"<td style='padding:6px 14px;color:#444'>{html.escape(i.retailer)}</td>"
        f"<td style='padding:6px 14px;color:#777'>{html.escape(i.condition)}</td>"
        f"<td style='padding:6px 14px'><a href='{html.escape(i.url)}'>View</a></td>"
        f"</tr>"
        for i in sorted(others, key=lambda l: l.price)
    )

    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f6f8;
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a">
  <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:12px;
  padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
    <p style="margin:0 0 6px;font-size:13px;letter-spacing:.08em;
    text-transform:uppercase;color:#0a7d33;font-weight:700">Price drop</p>
    <h1 style="margin:0 0 4px;font-size:21px;line-height:1.3">
      {html.escape(product_name)}</h1>
    <p style="margin:0 0 20px;color:#555;font-size:14px">
      Now {html.escape(_money(cheapest.price, currency))} at
      {html.escape(cheapest.retailer)} &mdash; your target was
      {html.escape(_money(threshold, currency))}.</p>
    <table style="border-collapse:collapse;width:100%;font-size:14px;
    border:1px solid #e3e5e8;border-radius:8px;overflow:hidden">
      <thead><tr style="background:#f0f7f2">
        <th style="padding:8px 14px;text-align:left">Price</th>
        <th style="padding:8px 14px;text-align:left">Retailer</th>
        <th style="padding:8px 14px;text-align:left">Condition</th>
        <th style="padding:8px 14px;text-align:left">Link</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    {"<p style='margin:22px 0 8px;font-size:13px;color:#666;font-weight:600'>"
     "Other tracked listings</p><table style='border-collapse:collapse;width:100%;"
     "font-size:13px'><tbody>" + other_rows + "</tbody></table>" if other_rows else ""}
    <p style="margin:24px 0 0;font-size:12px;color:#999">
      Sent by your MSI price tracker running on GitHub Actions.</p>
  </div>
</body></html>"""

    return subject, text, html_body


def build_health_alert(
    product_name: str, results: list[SourceResult], streak: int
) -> tuple[str, str, str]:
    """Render the 'tracker is blind' email, so silence is never mistaken for
    'the price never dropped'."""
    subject = f"⚠️ Price tracker can't read any retailer ({streak} runs)"
    detail = "\n".join(f"  - {r.source}: {r.error}" for r in results)
    text = (
        f"The tracker for {product_name} has failed to read every configured "
        f"source for {streak} consecutive runs.\n\n"
        f"Source errors:\n{detail}\n\n"
        "Retailers change their markup and block datacenter IPs from time to "
        "time. Check the latest Actions run log, and update the selectors or "
        "the configured URLs in config.yml.\n\n"
        "-- MSI price tracker (GitHub Actions)"
    )
    rows = "".join(
        f"<li style='margin-bottom:6px'><strong>{html.escape(r.source)}</strong>: "
        f"{html.escape(str(r.error))}</li>"
        for r in results
    )
    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f6f8;
font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a">
  <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:12px;
  padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
    <p style="margin:0 0 6px;font-size:13px;letter-spacing:.08em;
    text-transform:uppercase;color:#b4690e;font-weight:700">Tracker unhealthy</p>
    <h1 style="margin:0 0 10px;font-size:20px">No source could be read</h1>
    <p style="margin:0 0 16px;color:#555;font-size:14px">
      {html.escape(product_name)} &mdash; {streak} consecutive failed runs.
      Treat recent silence as "unknown", not "no price drop".</p>
    <ul style="font-size:13px;color:#444;padding-left:20px">{rows}</ul>
    <p style="margin:20px 0 0;font-size:12px;color:#999">
      Check the Actions log and the URLs in config.yml.</p>
  </div>
</body></html>"""
    return subject, text, html_body
