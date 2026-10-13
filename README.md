# MSI MAG 321UP QD-OLED X24 price tracker

A small scheduled service that checks what the MSI MAG 321UP QD-OLED X24
(31.5" 4K QD-OLED, 240 Hz) costs across US retailers and emails you when a
**new** unit drops to **$650 or less**.

It runs free on GitHub Actions cron. There is no server, no database and no
paid tier: price history is committed back into `data/` in the repo, which
doubles as the keep-alive that stops GitHub disabling the schedule.

## Prices when this was built (2026-10-11)

| Retailer | Price | Condition | Stock |
| --- | --- | --- | --- |
| Newegg | $680.06 | open box | in stock |
| Newegg | $781.99 | new | **out of stock** |
| Amazon | $799.49 | new | in stock |
| Walmart | $804.59 | new | in stock |

So $650 is a real sale-watch target, not a price you'll see tomorrow — but the
cheapest new unit has already been seen at $781.99, and the open-box SKU is
within $30 of your target.

## The trap this is built to avoid

Searching any retailer for "MAG 321UP" returns four different things, and
three of them sit within ~$20 of your $650 threshold:

| What | Price seen | Should it alert? |
| --- | --- | --- |
| MAG 321UP QD-OLED **X24**, new | $781.99 | yes, when it falls |
| MAG 321UP QD-OLED **X24**, open box | $680.06 | your call (off by default) |
| MAG 321UP QD-OLED, **165 Hz, no X24** | $671.14 | **no — different monitor** |
| the same 165 Hz panel, used | $403.99 | **no** |

A tracker that just takes the cheapest "321UP" result would have emailed you a
$404 used unit of the wrong monitor on its very first run. So:

- A listing only counts if its title **or URL slug** contains both `321UP` and
  `X24` after punctuation is stripped (this also catches the `MAG321UPDEX24`
  spelling B&H and the EU stores use).
- Condition is parsed from the title and badge text, and only `new` can
  trigger an alert unless you opt in to more.

Newegg's visible titles are spec blurbs that often omit the model name
entirely, which is why the URL slug is part of the match.

## Where the prices come from

| Source | What it gives | Status |
| --- | --- | --- |
| `smartprix` | Amazon + Newegg in one request | on, most reliable |
| `newegg` | Newegg per-SKU live price, stock, open-box | on |
| `walmart` | Walmart | on, best effort |
| `jsonld` | generic schema.org scraper for any product URL | off by default |

The Newegg source queries its per-item realtime endpoint for the two exact
SKUs (`N82E16824475582` new, `...582R` open box). That returns live price,
a real stock flag, and the full product title — far better than scraping the
search page, which Newegg A/B-serves as a React shell containing no prices
about a quarter of the time. The search scrape is kept as a fallback.

Amazon, B&H, Micro Center, Adorama, eBay and PCPartPicker all answer **HTTP
403** to GitHub's datacenter IP ranges, so they can't be scraped directly from
Actions — Amazon pricing arrives via the aggregator instead. If you ever run
this from a home IP, flip `sources.jsonld.enabled` to `true` in `config.yml`
to pick up B&H and Micro Center as well.

If *every* source fails 3 runs in a row you get a "tracker is blind" email, so
silence never gets mistaken for "the price never dropped".

## Setup

### 1. Create the repo

```bash
cd msi-price-tracker
git init -b main
git add .
git commit -m "Add MSI MAG 321UP QD-OLED X24 price tracker"
git remote add origin https://github.com/<you>/msi-price-tracker.git
git push -u origin main
```

A **public** repo gets unlimited free Actions minutes. A private one uses your
2,000 min/month free quota; this job takes well under a minute per run, so
8 runs/day is roughly 4 hours a month either way.

### 2. Get a Gmail app password

Normal Gmail passwords won't work over SMTP. At
<https://myaccount.google.com/apppasswords> (requires 2-Step Verification),
create an app password and copy the 16-character value.

Any other SMTP provider works too — just set `SMTP_HOST`/`SMTP_PORT`.

### 3. Add repository secrets

**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASS` | the 16-character app password |
| `ALERT_TO` | where alerts go |
| `SMTP_HOST` | optional, defaults to `smtp.gmail.com` |
| `SMTP_PORT` | optional, defaults to `587` |
| `ALERT_FROM` | optional, defaults to `SMTP_USER` |

### 4. Turn the schedule on

Open the **Actions** tab and enable workflows. Then run **Track price →
Run workflow** once by hand to confirm it works — the run summary shows a
table of every price found.

To prove the email path end to end before a real drop happens, run locally:

```bash
pip install -r requirements.txt
SMTP_USER=you@gmail.com SMTP_PASS=xxxx ALERT_TO=you@gmail.com \
  python -m tracker.main --test-email
```

## Local use

```bash
python -m tracker.main --dry-run          # scrape and print, change nothing
python -m tracker.main --dry-run -v       # with HTTP debug logging
python -m tracker.main --threshold 850    # see what an alert would look like
python -m pytest                          # 64 offline tests
```

On Windows PowerShell, set env vars with `$env:SMTP_USER = "..."` first.

## Configuration

Everything lives in [`config.yml`](config.yml). The things you're most likely
to change:

```yaml
alert:
  threshold: 650.00        # your target price
  conditions: ["new"]      # add "open_box" to also alert on the ~$665 unit
  cooldown_hours: 24       # don't repeat the same alert this often
  renotify_drop: 10.00     # ...unless it falls another $10
```

Environment variables override the file: `PRICE_THRESHOLD`,
`ALERT_CONDITIONS` (comma-separated), `ALERT_COOLDOWN_HOURS`.

The **Run workflow** button also takes a one-off threshold.

## How the alert logic behaves

- Alerts fire on listings that are `<=` threshold, an allowed condition, and
  not known to be out of stock.
- The same listing won't re-alert for 24 hours unless the price falls another
  $10 — otherwise a 3-hourly cron would send you eight identical emails a day.
- Condition is part of the de-duplication key, so an open-box alert can never
  suppress a later new-unit alert.
- Every observation is appended to `data/history.jsonl`, so you can chart the
  price over time later.

## Limitations, honestly

- **This is scraping.** Retailers change their markup without warning. The
  health alert tells you when that happens instead of failing silently, and
  `tests/test_newegg.py` makes the fix cheap to verify.
- **GitHub cron is best-effort** and can run late under load. It is also
  disabled automatically after 60 days of repo inactivity — the history commit
  on each run is what prevents that.
- **Stock status is approximate.** Smartprix doesn't expose it, so those
  listings assume in-stock.
- **Prices exclude tax and shipping**, and ignore limited-time coupon codes
  that only appear in the cart.
- Nothing here solves CAPTCHAs or uses proxies; it makes one polite request
  per source per run.

## Layout

```
config.yml                  threshold, sources, matching rules
tracker/
  main.py                   orchestration + CLI
  match.py                  model/condition/price parsing  <- the safety rail
  state.py                  history + alert de-duplication
  notify.py                 SMTP email rendering
  http.py                   browser-like session with retries
  sources/                  one module per retailer, self-registering
tests/                      64 offline tests
.github/workflows/
  track.yml                 the 3-hourly cron job
  tests.yml                 CI
data/                       price history + alert state, committed each run
```

Adding a retailer is one file in `tracker/sources/` with an `@register("name")`
decorator, plus an entry in `config.yml`.
