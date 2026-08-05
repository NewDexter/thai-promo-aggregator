"""
Tops & Gourmet Market scraper.

Coverage notes (read before modifying):
  * HTML path:  Both Tops and Gourmet Market share the same Central Food
    Retail storefront platform. The weekly "promotions" listing page
    renders each deal as a structured HTML product card — scraped directly.
  * OCR path:   Not currently used. If Tops switches a given campaign to a
    banner-image-only leaflet (as they occasionally do for seasonal
    campaigns), extend this scraper following the pattern in
    `lotuss.py` / `seven_eleven.py` rather than assuming full HTML coverage.
  * OUT OF SCOPE: none currently identified — Tops' public promotions page
    does not require app login at the time of writing.
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
