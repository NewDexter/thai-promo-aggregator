"""
Tops & Gourmet Market scraper.

Coverage notes (read before modifying — confirmed by direct inspection of
the live site, not assumed):
  * BLOCKED (not app-only, not a selector problem): the entire
    www.tops.co.th domain — including the homepage, not just the
    promotions path — returns HTTP 403 from Cloudflare bot management
    (`server: cloudflare`, a `__cf_bm` challenge cookie) for a plain HTTP
    client, before any HTML is ever served. There is no page structure to
    parse because no page is returned at all. This is distinct from the
    "app-only" exclusions on other stores: the promotions ARE public web
    content, they're just behind anti-bot protection this project
    deliberately does not attempt to bypass (no headless browser/stealth
    fingerprinting — that crosses into detection evasion, which is out of
    scope by design, not just by cost).
  * HTML path / OCR path: N/A while the block above holds. If Tops relaxes
    or changes their bot-protection config in the future, re-probe with a
    plain `curl -A "<realistic UA>" https://www.tops.co.th/` — a 200
    response there would mean it's worth re-attempting the original
    structured HTML plan (product card scraping) or the OCR pattern used
    in `lotuss.py` / `seven_eleven.py`, whichever the real page turns out
    to need.
  * Current behavior: `scrape()` still attempts the request every run (via
    `BaseScraper.fetch`'s normal retry/backoff) and raises on the 403,
    which `master_orchestrator.run_scraper_isolated` catches — this store
    fails cleanly without affecting others, and the existing
    consecutive-failure admin-alert mechanism fires once the block has
    persisted for `SCRAPER_FAILURE_ALERT_THRESHOLD` consecutive runs.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper

PROMOTIONS_URL = "https://www.tops.co.th/en/promotions"


class TopsGourmetScraper(BaseScraper):
    store_name = "Tops & Gourmet Market"

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(PROMOTIONS_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        deals: list[dict[str, Any]] = []

        for card in soup.select(".promo-item"):
            title_el = card.select_one(".promo-item-title")
            if title_el is None:
                continue

            original_el = card.select_one(".promo-item-original-price")
            sale_el = card.select_one(".promo-item-sale-price")
            valid_el = card.select_one(".promo-item-valid-until")
            category_el = card.select_one(".promo-item-tag")

            deals.append(
                self.build_deal(
                    title=title_el.get_text(strip=True),
                    category=self._normalize_category(
                        category_el.get_text(strip=True) if category_el else ""
                    ),
                    original_price=original_el.get_text(strip=True) if original_el else None,
                    sale_price=sale_el.get_text(strip=True) if sale_el else None,
                    valid_until=valid_el.get("data-iso-date") if valid_el else None,
                    image_url=None,
                    source_url=PROMOTIONS_URL,
                    extraction_method="html",
                )
            )
        return deals

    @staticmethod
    def _normalize_category(raw: str) -> str:
        raw_lower = raw.lower()
        if "1 แถม 1" in raw or "buy 1 get 1" in raw_lower or "b1g1" in raw_lower:
            return "Buy1Get1"
        if "flash" in raw_lower:
            return "FlashSale"
        if "member" in raw_lower or "สมาชิก" in raw:
            return "MemberExclusive"
        return "Discount"
