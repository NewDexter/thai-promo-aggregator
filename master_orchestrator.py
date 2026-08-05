"""
Master orchestrator: runs every store scraper in isolation, deduplicates
against the SQLite store, sends Telegram alerts for genuinely new deals,
tracks per-store scraper health, and (when run as a script) commits the
updated database + OCR cache back to the repo.

Run modes:
  * `python master_orchestrator.py`            - normal run (used by cron)
  * `DRY_RUN=1 python master_orchestrator.py`   - runs the full pipeline but
    never calls the Telegram API (still writes to the DB) — useful for
    local testing without spamming a real chat.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from config import CONFIG
from database import (
    get_connection,
    mark_alert_sent,
    mark_failure_alert_sent,
    record_scraper_failure,
    record_scraper_success,
    should_send_failure_alert,
    upsert_deal,
)
from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper
from scrapers.cj_more import CjMoreScraper
from scrapers.lotuss import LotussScraper
from scrapers.makro import MakroScraper
from scrapers.seven_eleven import SevenElevenScraper
from scrapers.tops_gourmet import TopsGourmetScraper
from telegram_notifier import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("master_orchestrator")


def build_scrapers(ocr: OcrFallback) -> list[BaseScraper]:
    """
    Instantiate every store scraper. OCR-capable scrapers share a single
    OcrFallback instance so per-run/per-day quota limits apply across the
    whole pipeline, not per-store.
    """
    return [
        SevenElevenScraper(ocr=ocr),
        CjMoreScraper(ocr=ocr),
        LotussScraper(ocr=ocr),
        MakroScraper(),
        TopsGourmetScraper(),
    ]


def run_scraper_isolated(scraper: BaseScraper, conn: Any) -> list[dict[str, Any]]:
    """
    Run a single scraper, catching ANY exception so one store's failure
    (site redesign, block, timeout, CAPTCHA) never stops the others.

    Updates scraper_health bookkeeping and returns an empty list on failure.
    """
    store = scraper.store_name
    try:
        deals = scraper.scrape()
        record_scraper_success(conn, store)
        logger.info("[%s] scrape succeeded: %d deals found", store, len(deals))
        return deals
    except Exception as exc:  # noqa: BLE001 - isolation boundary is intentional
        failure_count = record_scraper_failure(conn, store, str(exc))
        logger.error("[%s] scrape FAILED (%d consecutive failures): %s", store, failure_count, exc)
        return []
    finally:
        scraper.close()


def collect_all_deals(conn: Any) -> list[dict[str, Any]]:
    ocr = OcrFallback()
    all_deals: list[dict[str, Any]] = []
    for scraper in build_scrapers(ocr):
        all_deals.extend(run_scraper_isolated(scraper, conn))
    return all_deals


def dedupe_and_persist(conn: Any, deals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Upsert every scraped deal into SQLite. Returns only the subset that are
    genuinely new (first time this hash_id has been seen) — these are the
    ones that should trigger a Telegram alert. Everything else is a
    duplicate: logged, not alerted.
    """
    new_deals: list[dict[str, Any]] = []
    for deal in deals:
        is_new = upsert_deal(conn, deal)
        if is_new:
            new_deals.append(deal)
            logger.info("[%s] NEW deal: %s (hash=%s)", deal["store"], deal["title"], deal["hash_id"][:12])
        else:
            logger.info(
                "[%s] duplicate deal, skipping alert: %s (hash=%s)",
                deal["store"],
                deal["title"],
                deal["hash_id"][:12],
            )
    return new_deals


def send_failure_alerts(conn: Any, notifier: TelegramNotifier) -> None:
    """
    Fire a single low-noise admin alert for any store that has just crossed
    the consecutive-failure threshold. Uses should_send_failure_alert /
    mark_failure_alert_sent so this fires exactly once per failure streak,
    not on every run while the store stays broken.
    """
    threshold = CONFIG.scraper.consecutive_failure_alert_threshold
    rows = conn.execute("SELECT store FROM scraper_health").fetchall()
    for row in rows:
        store = row["store"]
        if should_send_failure_alert(conn, store, threshold):
            message = (
                f"⚠️ <b>{store}</b> scraper has failed {threshold}+ runs in a row — "
                "page structure may have changed and needs investigation."
            )
            if CONFIG.dry_run:
                logger.info("[DRY RUN] Would send admin alert: %s", message)
            else:
                notifier.send_admin_alert(message)
            mark_failure_alert_sent(conn, store)


def send_deal_alerts(conn: Any, notifier: TelegramNotifier, new_deals: list[dict[str, Any]]) -> int:
    if not new_deals:
        return 0
    if CONFIG.dry_run:
        logger.info("[DRY RUN] Would send %d Telegram alerts", len(new_deals))
        for deal in new_deals:
            mark_alert_sent(conn, deal["hash_id"])
        return len(new_deals)

    sent = 0
    for i, deal in enumerate(new_deals):
        if i > 0:
            import time

            time.sleep(CONFIG.telegram.send_delay_seconds)
        from telegram_notifier import format_deal_message

        if notifier.send_html(format_deal_message(deal)):
            mark_alert_sent(conn, deal["hash_id"])
            sent += 1
    return sent


def main() -> int:
    logger.info("=== Thai Promo Aggregator run starting (dry_run=%s) ===", CONFIG.dry_run)

    with get_connection() as conn:
        all_deals = collect_all_deals(conn)
        logger.info("Total deals scraped across all stores: %d", len(all_deals))

        new_deals = dedupe_and_persist(conn, all_deals)
        logger.info("New deals requiring alerts: %d", len(new_deals))

        with TelegramNotifier() as notifier:
            sent = send_deal_alerts(conn, notifier, new_deals)
            logger.info("Telegram alerts sent: %d/%d", sent, len(new_deals))

            send_failure_alerts(conn, notifier)

    logger.info("=== Run complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
