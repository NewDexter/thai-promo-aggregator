# Thai Convenience Store & Grocery Promotion Aggregator

Scrapes public promotion pages from 7-Eleven Thailand, CJ More, Lotus's,
Makro, and Tops & Gourmet Market every 6 hours, deduplicates against a
committed SQLite database, and pushes new deals to a Telegram chat.

Built to run at **near-zero cost**: no paid LLM calls for routine scraping,
formatting, or notifications (pure Python: httpx, BeautifulSoup4, regex,
string templates), and no paid hosting (runs entirely on GitHub Actions'
free tier, with state persisted by committing back to the repo).

## Data coverage — what's actually live vs. best-effort

| Store | Fully live-scraped (HTML) | OCR-derived (image/PDF leaflets) | App-only / not collected |
|---|---|---|---|
| 7-Eleven | Promotion cards with text prices | Weekly banner images (`.promo-banner`) | "7-Delivery" coupons & All Member app-exclusive flash deals |
| CJ More | Catalog title/date metadata | **Primary path** — per-item prices only exist inside catalog leaflet images | none identified |
| Lotus's | "Weekend Shock Price" page | Weekly e-leaflet pages | "Lotus's Reward" personalized member coupons |
| Makro | Public discount listing | not used currently | Makro PRO app wholesale/member pricing (device-signed session, not reachable from the web) |
| Tops & Gourmet Market | Weekly promotions listing | not used currently | none identified |

Every scraper module's docstring restates this so it stays next to the code
it describes — check there first if a store's coverage changes.

OCR extraction is skipped entirely (item stored with `null` prices, logged,
no crash) whenever `GEMINI_API_KEY` isn't set or the daily/per-run quota is
exhausted. This is the expected steady state for anyone who doesn't want to
create a Gemini API key — everything else in the pipeline still works.

## Repository layout

```
config.py                 # env vars, rate limits, paths
database.py                # SQLite schema + dedup helpers
master_orchestrator.py     # runs scrapers, dedupes, sends alerts
telegram_notifier.py       # HTML message formatting + rate-limited sending
ocr_fallback.py            # Gemini Flash free-tier OCR, disk-cached, quota-aware
scrapers/
  base_scraper.py          # retry/backoff, price normalization, hash_id
  seven_eleven.py, cj_more.py, lotuss.py, makro.py, tops_gourmet.py
tests/
  fixtures/                 # saved sample HTML per store
  test_scrapers.py          # runs against fixtures only, no live network
.github/workflows/scraper_cron.yml
```

## Local setup

1. Python 3.11+ recommended (3.9+ works; the codebase uses modern type hints
   evaluated lazily via `from __future__ import annotations`).
2. Create a virtualenv and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Run the test suite (no network required, no secrets required):
   ```bash
   pytest tests/ -v
   ```
4. Run the real pipeline locally:
   ```bash
   export TELEGRAM_BOT_TOKEN=xxxx     # from @BotFather
   export TELEGRAM_CHAT_ID=xxxx       # your chat/channel ID
   export GEMINI_API_KEY=xxxx         # optional — omit to skip OCR entirely
   python master_orchestrator.py
   ```
   Set `DRY_RUN=1` to run the full scrape → dedup → format pipeline without
   actually calling the Telegram API (still writes to `data/deals.db`).

## Required / optional secrets

Configure these as **repository secrets** (Settings → Secrets and variables
→ Actions) so the scheduled workflow can use them:

| Secret | Required? | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Yes | Destination chat/channel ID for alerts |
| `GEMINI_API_KEY` | No | Enables OCR for image/PDF leaflets (Lotus's, 7-Eleven, CJ More). Without it, those items are stored with `null` prices instead of being OCR'd. |

No secret is required for the scraper to run — without Telegram credentials
it logs what it *would* have sent instead of raising.

## GitHub Actions: schedule, concurrency, and state persistence

`.github/workflows/scraper_cron.yml`:

- Runs on a `cron: "0 */6 * * *"` schedule (every 6 hours, UTC) and supports
  `workflow_dispatch` for manual runs.
- Uses `concurrency: { group: thai-promo-aggregator-run, cancel-in-progress: false }`
  so overlapping runs queue instead of racing on the SQLite file.
- After a successful run, commits `data/deals.db` and `cache/ocr/` back to
  the repository using the automatically-provided `GITHUB_TOKEN` — this is
  the project's **only** durable state store. GitHub Actions artifacts are
  intentionally *not* used for this, since they expire (default 90 days)
  and aren't designed for long-lived state.

### GitHub Actions cost

- **Public repositories**: Actions minutes are unlimited and free for
  standard runners. This project is designed to run comfortably within that
  free tier — a full run typically takes under a minute per store.
- **Private repositories**: Free accounts get 2,000 Actions minutes/month;
  a run every 6 hours (4/day) at roughly 1–2 minutes each stays well under
  that budget (~150–250 minutes/month), but a private repo is the one place
  this project could theoretically accrue cost if minutes are exhausted
  elsewhere in your account. Use a public repo if you want a hard
  cost-free guarantee.

## Rate limits & politeness

- **Scraper HTTP requests**: `SCRAPER_REQUEST_DELAY_SECONDS` (default 1.5s)
  between requests to be polite to retailer sites; retried with exponential
  backoff (`SCRAPER_MAX_RETRIES`, default 3 attempts).
- **OCR (Gemini free tier)**: `OCR_MAX_CALLS_PER_RUN` (default 15) and
  `OCR_MAX_CALLS_PER_DAY` (default 40), plus a disk cache keyed by image
  SHA-256 so the same banner is never OCR'd twice across runs.
- **Telegram**: messages are sent one at a time with a
  `TELEGRAM_SEND_DELAY_SECONDS` (default 1.1s) gap to stay under Telegram's
  roughly 1 message/second per-chat limit.

## Deduplication

Each deal gets an MD5 `hash_id` of `(store, title, sale_price, valid_until)`.
An alert is sent only the first time a `hash_id` is seen (checked against
SQLite before formatting/sending); repeat sightings update `last_seen` and
are logged, not alerted. Including `valid_until` in the hash means a
recurring monthly promotion at the same price becomes a fresh alert once its
validity window rolls over, rather than being suppressed forever.

## Scraper resilience

Every store scraper runs inside its own try/except in
`master_orchestrator.run_scraper_isolated` — one store's failure (site
redesign, block, timeout, CAPTCHA) never stops the others. Consecutive
failures are tracked per store in the `scraper_health` table; after
`SCRAPER_FAILURE_ALERT_THRESHOLD` (default 3) consecutive failed runs, a
single low-noise Telegram admin alert fires (not repeated every run while
the store stays broken).
