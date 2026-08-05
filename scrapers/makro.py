"""
Makro scraper.

Coverage notes (read before modifying):
  * HTML path:  The public "discounts" listing on makro.co.th renders each
    promotion as a structured HTML product card (title, price, validity) —
    scraped directly with BeautifulSoup.
  * OCR path:   Not currently used for Makro. The public discounts page is
    text-based; if Makro switches to image-only banners in the future,
    follow the OCR pattern used in `lotuss.py` / `seven_eleven.py`.
  * OUT OF SCOPE (app-only): "Makro PRO" app-exclusive wholesale pricing
    and member-tier discounts require the Makro PRO mobile app with
    device-signed sessions and are NOT reachable from any public web page.
    Per project constraints we do not reverse-engineer that app's API —
    this scraper only reflects what a logged-out browser can see on the
    public makro.co.th site.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from scrapers.base_scraper import BaseScraper

DISCOUNTS_URL = "https://www.makro.co.th/en/discount"


class MakroScraper(BaseScraper):
    store_name = "Makro"

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(DISCOUNTS_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        deals: list[dict[str, Any]] = []

        for card in soup.select(".product-card"):
            title_el = card.select_one(".product-name")
            if title_el is None:
                continue

            original_el = card.select_one(".price-original")
            sale_el = card.select_one(".price-discounted")
            valid_el = card.select_one(".discount-valid-until")
            badge_el = card.select_one(".discount-badge")

            deals.append(
                self.build_deal(
                    title=title_el.get_text(strip=True),
                    category=self._normalize_category(
                        badge_el.get_text(strip=True) if badge_el else ""
                    ),
                    original_price=original_el.get_text(strip=True) if original_el else None,
                    sale_price=sale_el.get_text(strip=True) if sale_el else None,
                    valid_until=valid_el.get("data-iso-date") if valid_el else None,
                    image_url=None,
                    source_url=DISCOUNTS_URL,
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
