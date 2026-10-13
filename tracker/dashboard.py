"""Render the static status dashboard published to GitHub Pages.

Reads `data/history.jsonl` plus `data/state.json` and writes `docs/index.html`.
Everything is generated server-side into one self-contained file: inline SVG for
the chart, a small inline script only for the hover crosshair. No CDN, no build
step, no runtime data fetching -- so the page cannot break because a dependency
moved, and it renders identically offline.

Colours come from the validated categorical palette (slots 1-4). Both the light
and dark steps were checked with the palette validator: every gate passes, with
a light-mode contrast warning on aqua and yellow, which obliges visible direct
labels and a table view. Both are present below.
"""

from __future__ import annotations

import html
import json
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# Categorical slots 1-4: blue, orange, aqua, yellow. Fixed order, never cycled.
SERIES_COLORS = [
    {"light": "#2a78d6", "dark": "#3987e5"},
    {"light": "#eb6834", "dark": "#d95926"},
    {"light": "#1baf7a", "dark": "#199e70"},
    {"light": "#eda100", "dark": "#c98500"},
]

# When two sources report the same retailer+condition, trust the direct
# retailer scraper over the aggregator.
SOURCE_PRIORITY = {"newegg": 0, "walmart": 1, "jsonld": 2, "smartprix": 3}

CONDITION_LABEL = {
    "new": "new",
    "open_box": "open box",
    "refurbished": "refurb",
    "used": "used",
    "unknown": "unknown",
}


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _json_for_script(payload: object) -> str:
    """JSON safe to embed inside a <script> element.

    Escaping the angle brackets and ampersand means no scraped string can close
    the tag early or smuggle markup into the page.
    """
    return (
        json.dumps(payload)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _parse_ts(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def load_history(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(row.get("ts", ""))
            price = row.get("price")
            if ts is None or not isinstance(price, (int, float)):
                continue
            row["_ts"] = ts
            rows.append(row)
    rows.sort(key=lambda r: r["_ts"])
    return rows


def build_series(rows: list[dict]) -> "OrderedDict[tuple[str, str], list[dict]]":
    """Group observations into one series per retailer+condition.

    Within a single run two sources can report the same retailer and condition
    at different prices (the aggregator lags the retailer's own API), so collapse
    each run to the highest-priority source.
    """
    # (retailer, condition, run timestamp) -> best row for that run
    picked: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (row.get("retailer", "?"), row.get("condition", "unknown"), row["ts"])
        current = picked.get(key)
        if current is None or SOURCE_PRIORITY.get(
            row.get("source", ""), 99
        ) < SOURCE_PRIORITY.get(current.get("source", ""), 99):
            picked[key] = row

    series: "OrderedDict[tuple[str, str], list[dict]]" = OrderedDict()
    for row in sorted(picked.values(), key=lambda r: r["_ts"]):
        series.setdefault((row["retailer"], row["condition"]), []).append(row)

    # Cheapest-current first, so the most relevant series takes palette slot 1.
    return OrderedDict(
        sorted(series.items(), key=lambda kv: kv[1][-1]["price"])
    )


def _nice_domain(lo: float, hi: float) -> tuple[float, float, float]:
    """A padded price domain plus a sensible tick step."""
    if hi <= lo:
        lo, hi = lo - 25, hi + 25
    span = hi - lo
    pad = max(span * 0.12, 8.0)
    lo, hi = lo - pad, hi + pad
    raw_step = (hi - lo) / 4
    for step in (10, 20, 25, 50, 100, 150, 200, 250, 500):
        if raw_step <= step:
            break
    lo = (lo // step) * step
    hi = -(-hi // step) * step
    return lo, hi, step


def _chart(series, threshold: float) -> str:
    """Inline SVG multi-series line chart with a target reference rule."""
    stamps = sorted({r["ts"] for rows in series.values() for r in rows})
    if len(stamps) < 2:
        return (
            '<p class="empty">Only one observation so far &mdash; the chart appears '
            "once the tracker has run at least twice. It runs every 3 hours.</p>"
        )

    W, H = 820, 340
    ML, MR, MT, MB = 58, 104, 18, 40
    plot_w, plot_h = W - ML - MR, H - MT - MB

    prices = [r["price"] for rows in series.values() for r in rows]
    lo, hi, step = _nice_domain(min(prices + [threshold]), max(prices + [threshold]))

    t0 = _parse_ts(stamps[0]).timestamp()
    t1 = _parse_ts(stamps[-1]).timestamp()
    span = max(t1 - t0, 1.0)

    def x_of(ts: str) -> float:
        return ML + (_parse_ts(ts).timestamp() - t0) / span * plot_w

    def y_of(price: float) -> float:
        return MT + (hi - price) / (hi - lo) * plot_h

    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" role="img" class="chart" '
        f'aria-label="Price history per retailer, with the {_money(threshold)} target marked">'
    ]

    # Recessive gridlines + y ticks (tabular figures: a column of numbers).
    tick = lo
    while tick <= hi + 0.01:
        y = y_of(tick)
        parts.append(
            f'<line x1="{ML}" y1="{y:.1f}" x2="{ML + plot_w}" y2="{y:.1f}" class="grid"/>'
        )
        parts.append(
            f'<text x="{ML - 10}" y="{y + 4:.1f}" class="tick tick-y">${tick:,.0f}</text>'
        )
        tick += step

    # Baseline + x ticks (first, middle, last to avoid collisions).
    parts.append(
        f'<line x1="{ML}" y1="{MT + plot_h}" x2="{ML + plot_w}" y2="{MT + plot_h}" class="axis"/>'
    )
    idxs = sorted({0, len(stamps) // 2, len(stamps) - 1})
    for i in idxs:
        ts = stamps[i]
        dt = _parse_ts(ts)
        anchor = "start" if i == 0 else ("end" if i == len(stamps) - 1 else "middle")
        parts.append(
            f'<text x="{x_of(ts):.1f}" y="{MT + plot_h + 22}" class="tick" '
            f'text-anchor="{anchor}">{dt.strftime("%b %d %H:%M")}</text>'
        )

    # The target: a reference rule, labelled, not a series.
    ty = y_of(threshold)
    parts.append(
        f'<line x1="{ML}" y1="{ty:.1f}" x2="{ML + plot_w}" y2="{ty:.1f}" class="target"/>'
    )
    parts.append(
        f'<text x="{ML + plot_w + 8}" y="{ty + 4:.1f}" class="target-label">'
        f"target {_money(threshold)}</text>"
    )

    # One 2px line per series, with an end marker ringed in the surface colour
    # so coincident dots stay legible.
    ends: list[tuple[float, float, float]] = []  # (y, x, price)
    for i, ((retailer, condition), rows) in enumerate(series.items()):
        slot = i % len(SERIES_COLORS)
        pts = " ".join(f"{x_of(r['ts']):.1f},{y_of(r['price']):.1f}" for r in rows)
        parts.append(f'<polyline points="{pts}" class="line s{slot}"/>')
        last = rows[-1]
        cx, cy = x_of(last["ts"]), y_of(last["price"])
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" class="dot s{slot}"/>')
        ends.append((cy, cx, last["price"]))

    # Direct end labels, collision-filtered. Two retailers often sit at exactly
    # the same price, which would stack two labels on the same baseline. Nudging
    # them apart detaches a label from its line and reads as noise, so instead
    # one label is drawn per occupied band and the legend, tooltip and table
    # carry the rest. The table view is also the required relief for the
    # light-mode contrast warning on the aqua and yellow slots.
    MIN_GAP = 13.0
    last_y: float | None = None
    for y, x, price in sorted(ends):
        if last_y is not None and y - last_y < MIN_GAP:
            continue
        parts.append(
            f'<text x="{x + 11:.1f}" y="{y + 4:.1f}" class="end-label">'
            f"{_money(price)}</text>"
        )
        last_y = y

    # Hover layer: crosshair + tooltip, driven by the inline script.
    parts.append(
        f'<line id="crosshair" x1="0" y1="{MT}" x2="0" y2="{MT + plot_h}" '
        f'class="crosshair" style="opacity:0"/>'
    )
    parts.append(
        f'<rect id="hit" x="{ML}" y="{MT}" width="{plot_w}" height="{plot_h}" '
        f'fill="transparent" style="cursor:crosshair"/>'
    )
    parts.append("</svg>")

    # Geometry + values the hover script needs. Names are stored raw: the
    # payload is JSON data, `_json_for_script` neutralises the angle brackets
    # that could close this tag early, and the tooltip builds DOM nodes with
    # textContent rather than innerHTML -- so nothing here is ever parsed as
    # markup, and a name containing "&" still displays correctly.
    hover = {
        "ml": ML, "plotW": plot_w,
        "stamps": stamps,
        "xs": [round(x_of(s), 1) for s in stamps],
        "labels": [_parse_ts(s).strftime("%b %d, %H:%M UTC") for s in stamps],
        "series": [
            {
                "name": f"{retailer} ({CONDITION_LABEL.get(cond, cond)})",
                "slot": i % len(SERIES_COLORS),
                "byTs": {r["ts"]: r["price"] for r in rows},
            }
            for i, ((retailer, cond), rows) in enumerate(series.items())
        ],
    }

    legend = "".join(
        f'<span class="key"><span class="swatch s{i % len(SERIES_COLORS)}"></span>'
        f"{html.escape(retailer)} "
        f'<span class="muted">({html.escape(CONDITION_LABEL.get(cond, cond))})</span></span>'
        for i, ((retailer, cond), _) in enumerate(series.items())
    )

    return (
        f'<div class="legend">{legend}</div>'
        f'<div class="chart-wrap">{"".join(parts)}'
        f'<div id="tip" class="tip" hidden></div></div>'
        f'<script id="hover-data" type="application/json">{_json_for_script(hover)}</script>'
    )


def _css() -> str:
    """Palette as role tokens; dark mode is selected steps, not an auto-flip."""
    light = {
        "surface": "#fcfcfb", "plane": "#f9f9f7", "ink": "#0b0b0b",
        "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
        "axis": "#c3c2b7", "good": "#006300", "border": "rgba(11,11,11,0.10)",
    }
    dark = {
        "surface": "#1a1a19", "plane": "#0d0d0d", "ink": "#f0efec",
        "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
        "axis": "#383835", "good": "#0ca30c", "border": "rgba(255,255,255,0.10)",
    }

    def tokens(mode: dict, key: str) -> str:
        rows = "".join(f"--{k}:{v};" for k, v in mode.items())
        series = "".join(
            f"--s{i}:{c[key]};" for i, c in enumerate(SERIES_COLORS)
        )
        return rows + series

    return f"""
:root {{ color-scheme: light; {tokens(light, 'light')} }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{ color-scheme: dark; {tokens(dark, 'dark')} }}
}}
:root[data-theme="dark"] {{ color-scheme: dark; {tokens(dark, 'dark')} }}

* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 32px 16px 64px;
  background: var(--plane); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.5;
}}
.wrap {{ max-width: 920px; margin: 0 auto; }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 24px; margin-bottom: 20px;
}}
h1 {{ font-size: 20px; margin: 0 0 4px; letter-spacing: -0.01em; }}
.sub {{ color: var(--ink2); font-size: 14px; margin: 0; }}
.muted {{ color: var(--muted); }}
h2 {{ font-size: 14px; font-weight: 600; margin: 0 0 16px; color: var(--ink2); }}

/* Hero figure: exactly one per view, >=48px, proportional figures. */
.hero-label {{ font-size: 13px; color: var(--ink2); margin: 0 0 2px; }}
.hero {{ font-size: 54px; font-weight: 600; line-height: 1.05; margin: 0; }}
.hero-note {{ font-size: 14px; color: var(--ink2); margin: 6px 0 0; }}
.at-target {{ color: var(--good); font-weight: 600; }}

.tiles {{ display: flex; flex-wrap: wrap; gap: 28px; margin-top: 24px;
  padding-top: 20px; border-top: 1px solid var(--grid); }}
.tile-label {{ font-size: 12px; color: var(--muted); margin: 0 0 2px; }}
.tile-value {{ font-size: 19px; font-weight: 600; margin: 0; }}

.legend {{ display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 12px;
  font-size: 13px; color: var(--ink2); }}
.key {{ display: inline-flex; align-items: center; gap: 7px; }}
.swatch {{ width: 11px; height: 11px; border-radius: 3px; display: inline-block; }}

.chart-wrap {{ position: relative; }}
.chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }}
.axis {{ stroke: var(--axis); stroke-width: 1; }}
.tick {{ fill: var(--muted); font-size: 11px; }}
.tick-y {{ text-anchor: end; font-variant-numeric: tabular-nums; }}
.line {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
/* 2px surface ring keeps overlapping end-dots legible. */
.dot {{ stroke: var(--surface); stroke-width: 2; }}
.end-label {{ fill: var(--ink); font-size: 12px; font-weight: 600; }}
.target {{ stroke: var(--muted); stroke-width: 1.5; stroke-dasharray: 5 4; }}
.target-label {{ fill: var(--muted); font-size: 11px; }}
.crosshair {{ stroke: var(--axis); stroke-width: 1; pointer-events: none; }}

.tip {{ position: absolute; pointer-events: none; z-index: 5;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 9px; padding: 9px 11px; font-size: 12px;
  box-shadow: 0 4px 14px rgba(0,0,0,.14); min-width: 170px; }}
.tip-ts {{ color: var(--muted); margin-bottom: 6px; }}
.tip-row {{ display: flex; align-items: center; gap: 7px;
  justify-content: space-between; }}
.tip-row b {{ font-variant-numeric: tabular-nums; }}

table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ text-align: left; font-weight: 600; font-size: 12px; color: var(--muted);
  padding: 0 12px 8px 0; border-bottom: 1px solid var(--grid); }}
td {{ padding: 10px 12px 10px 0; border-bottom: 1px solid var(--grid);
  vertical-align: middle; }}
tr:last-child td {{ border-bottom: 0; }}
.price {{ font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.pill {{ font-size: 11px; padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--ink2); white-space: nowrap; }}
.oos {{ color: var(--muted); }}
a {{ color: var(--s0); }}
.empty {{ color: var(--ink2); font-size: 14px; margin: 0; }}
footer {{ color: var(--muted); font-size: 12px; text-align: center; }}
footer a {{ color: var(--muted); }}
@media (max-width: 560px) {{
  .hero {{ font-size: 42px; }}
  .tiles {{ gap: 18px; }}
  .hide-sm {{ display: none; }}
}}
""" + "".join(
        f".s{i} {{ stroke: var(--s{i}); }} circle.s{i}, .swatch.s{i} "
        f"{{ fill: var(--s{i}); background: var(--s{i}); }}\n"
        for i in range(len(SERIES_COLORS))
    )


def _hover_script() -> str:
    return """
(function () {
  var el = document.getElementById('hover-data');
  if (!el) return;
  var d = JSON.parse(el.textContent);
  var svg = document.querySelector('svg.chart');
  var hit = document.getElementById('hit');
  var cross = document.getElementById('crosshair');
  var tip = document.getElementById('tip');
  var wrap = document.querySelector('.chart-wrap');
  if (!svg || !hit || !tip) return;

  function money(v) {
    return '$' + v.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/, ',');
  }

  function show(ev) {
    var box = svg.getBoundingClientRect();
    // Map pointer position into the SVG's own viewBox units.
    var vx = (ev.clientX - box.left) / box.width * 820;
    var best = 0;
    for (var i = 1; i < d.xs.length; i++) {
      if (Math.abs(d.xs[i] - vx) < Math.abs(d.xs[best] - vx)) best = i;
    }
    var ts = d.stamps[best];
    cross.setAttribute('x1', d.xs[best]);
    cross.setAttribute('x2', d.xs[best]);
    cross.style.opacity = 1;

    // Built as nodes, never innerHTML: retailer names are scraped strings.
    var frag = document.createDocumentFragment();
    var head = document.createElement('div');
    head.className = 'tip-ts';
    head.textContent = d.labels[best];
    frag.appendChild(head);

    var any = false;
    for (var j = 0; j < d.series.length; j++) {
      var s = d.series[j];
      if (!(ts in s.byTs)) continue;
      any = true;
      var row = document.createElement('div');
      row.className = 'tip-row';
      var left = document.createElement('span');
      var sw = document.createElement('span');
      sw.className = 'swatch s' + s.slot;
      left.appendChild(sw);
      left.appendChild(document.createTextNode(' ' + s.name));
      var val = document.createElement('b');
      val.textContent = money(s.byTs[ts]);
      row.appendChild(left);
      row.appendChild(val);
      frag.appendChild(row);
    }
    if (!any) { hide(); return; }
    tip.textContent = '';
    tip.appendChild(frag);
    tip.hidden = false;

    // Keep the tooltip inside the card.
    var px = d.xs[best] / 820 * box.width;
    var tw = tip.offsetWidth;
    var left = px + 14;
    if (left + tw > wrap.clientWidth) left = px - tw - 14;
    tip.style.left = Math.max(0, left) + 'px';
    tip.style.top = '8px';
  }

  function hide() {
    cross.style.opacity = 0;
    tip.hidden = true;
  }

  hit.addEventListener('mousemove', show);
  hit.addEventListener('mouseleave', hide);
  hit.addEventListener('touchmove', function (e) {
    if (e.touches[0]) { show(e.touches[0]); e.preventDefault(); }
  }, { passive: false });
  hit.addEventListener('touchend', hide);
})();
"""


def _current_rows(series) -> list[dict]:
    """Latest observation per series, cheapest first."""
    out = [rows[-1] for rows in series.values()]
    return sorted(out, key=lambda r: r["price"])


def render(
    history_path: Path,
    state_path: Path,
    out_path: Path,
    product_name: str,
    threshold: float,
    conditions: list[str],
    repo_url: str | None = None,
) -> Path:
    rows = load_history(history_path)
    series = build_series(rows)
    current = _current_rows(series)

    state: dict = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    allowed = {c.lower() for c in conditions}
    alertable = [
        r for r in current
        if r.get("condition", "").lower() in allowed and r.get("in_stock") is not False
    ]
    # The hero number is the thing you're actually waiting on.
    headline = min(alertable, key=lambda r: r["price"]) if alertable else None

    last_run = state.get("last_run") or (rows[-1]["ts"] if rows else None)
    last_dt = _parse_ts(last_run) if last_run else None
    updated = last_dt.strftime("%b %d, %Y at %H:%M UTC") if last_dt else "never"

    lowest = state.get("lowest_seen") or {}
    lowest_new = [v for k, v in lowest.items() if k.endswith(":new") and isinstance(v, (int, float))]

    # --- hero -------------------------------------------------------------
    if headline:
        gap = headline["price"] - threshold
        if gap <= 0:
            note = (
                f'<span class="at-target">At or below your '
                f"{_money(threshold)} target.</span>"
            )
        else:
            note = (
                f"{_money(gap)} above your {_money(threshold)} target &middot; "
                f'cheapest {html.escape(CONDITION_LABEL.get(headline["condition"], ""))} '
                f'unit, at {html.escape(headline["retailer"])}'
            )
        hero = (
            f'<p class="hero-label">Cheapest alertable price now</p>'
            f'<p class="hero">{_money(headline["price"])}</p>'
            f'<p class="hero-note">{note}</p>'
        )
    else:
        hero = (
            '<p class="hero-label">Cheapest alertable price now</p>'
            '<p class="hero">&mdash;</p>'
            '<p class="hero-note">No in-stock listing matched the tracked '
            "conditions on the last run.</p>"
        )

    tiles = [("Target", _money(threshold))]
    if lowest_new:
        tiles.append(("Lowest new price seen", _money(min(lowest_new))))
    tiles.append(("Listings tracked", str(len(current))))
    tiles.append(("Observations", f"{len(rows):,}"))
    tiles.append(("Alerting on", ", ".join(CONDITION_LABEL.get(c, c) for c in conditions)))
    tiles_html = "".join(
        f'<div><p class="tile-label">{html.escape(l)}</p>'
        f'<p class="tile-value">{v}</p></div>'
        for l, v in tiles
    )

    # --- table view (the relief for the light-mode contrast warning) ------
    if current:
        body = "".join(
            f"<tr>"
            f'<td class="price">{_money(r["price"])}</td>'
            f'<td>{html.escape(r["retailer"])}</td>'
            f'<td><span class="pill">'
            f'{html.escape(CONDITION_LABEL.get(r.get("condition", ""), "?"))}</span></td>'
            f'<td>{"Out of stock" if r.get("in_stock") is False else "In stock"}</td>'
            f'<td class="hide-sm"><a href="{html.escape(r.get("url") or "#")}" '
            f'rel="nofollow noopener">View</a></td>'
            f"</tr>"
            for r in current
        )
        table = (
            "<table><thead><tr><th>Price</th><th>Retailer</th><th>Condition</th>"
            '<th>Stock</th><th class="hide-sm">Link</th></tr></thead>'
            f"<tbody>{body}</tbody></table>"
        )
    else:
        table = '<p class="empty">No observations recorded yet.</p>'

    repo_link = (
        f' &middot; <a href="{html.escape(repo_url)}">source</a>' if repo_url else ""
    )

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(product_name)} price</title>
<meta name="description" content="Live price tracker for the {html.escape(product_name)}.">
<style>{_css()}</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>{html.escape(product_name)}</h1>
    <p class="sub">Updated {updated} &middot; checked every 3 hours</p>
  </div>

  <div class="card">
    {hero}
    <div class="tiles">{tiles_html}</div>
  </div>

  <div class="card">
    <h2>Price history</h2>
    {_chart(series, threshold)}
  </div>

  <div class="card">
    <h2>Current listings</h2>
    {table}
  </div>

  <footer>
    Generated by a GitHub Actions cron job{repo_link}
  </footer>
</div>
<script>{_hover_script()}</script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    log.info("dashboard written to %s (%d observations)", out_path, len(rows))
    return out_path
