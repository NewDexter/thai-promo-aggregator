"""
7-Eleven Thailand scraper.

Coverage notes (read before modifying):
  * HTML path:  The public promotions listing page (www.7eleven.co.th /
    promotion) renders some deals as plain HTML cards (title + price text
    in the DOM) — these are scraped directly with BeautifulSoup, no OCR
    needed.
  * OCR path:   Many weekly deals are published purely as banner images
    (JPEG/PNG) inside the same promotion page, with no text in the DOM.
    Any `<img class="promo-banner">` with no matching HTML price text is
    routed through `ocr_fallback.OcrFallback` to extract title/price.
  * OUT OF SCOPE (app-only): "7-Delivery" exclusive coupons and All Member
    app-only flash deals live inside the 7-Eleven mobile app behind a
    signed session and are NOT reachable from any public web page. Per
    project constraints we do not reverse-engineer the app API — these
    deals are simply not collected. This scraper only ever reflects what a
    logged-out browser can see on the public site.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PROMOTION_URL = "https://www.7eleven.co.th/promotion"


class SevenElevenScraper(BaseScraper):
    store_name = "7-Eleven"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        # Allow injection of a shared OcrFallback instance so per-run/per-day
        # quota counters are shared across all stores in one orchestrator run.
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(PROMOTION_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        deals: list[dict[str, Any]] = []

        deals.extend(self._parse_html_cards(soup))
        deals.extend(self._parse_banner_images(soup))
        return deals

    # -- HTML-rendered deal cards -----------------------------------------

    def _parse_html_cards(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        for card in soup.select(".promotion-card"):
            title_el = card.select_one(".promo-title")
            if title_el is None:
                continue
            title = title_el.get_text(strip=True)

            original_el = card.select_one(".promo-price-original")
            sale_el = card.select_one(".promo-price-sale")
            valid_el = card.select_one(".promo-valid-until")
            category_el = card.select_one(".promo-category")

            deals.append(
                self.build_deal(
                    title=title,
                    category=self._normalize_category(
                        category_el.get_text(strip=True) if category_el else ""
                    ),
                    original_price=original_el.get_text(strip=True) if original_el else None,
                    sale_price=sale_el.get_text(strip=True) if sale_el else None,
                    valid_until=self._extract_iso_date(
                        valid_el.get_text(strip=True) if valid_el else None
                    ),
                    image_url=None,
                    source_url=PROMOTION_URL,
                    extraction_method="html",
                )
            )
        return deals

    # -- Image-only banners (OCR path) ------------------------------------

    def _parse_banner_images(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        for img in soup.select("img.promo-banner"):
            image_url = img.get("src") or img.get("data-src")
            if not image_url:
                continue
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = "https://www.7eleven.co.th" + image_url

            alt_title = img.get("alt", "").strip() or "7-Eleven Promotion Banner"

            try:
                image_bytes = self.fetch_bytes(image_url)
            except Exception as exc:  # noqa: BLE001 - one bad banner shouldn't stop the run
                logger.warning("[%s] failed to download banner %s: %s", self.store_name, image_url, exc)
                deals.append(
                    self.build_deal(
                        title=alt_title,
                        category="Discount",
                        original_price=None,
                        sale_price=None,
                        valid_until=None,
                        image_url=image_url,
                        source_url=PROMOTION_URL,
                        extraction_method="ocr",
                    )
                )
                continue

            ocr_results = self._ocr.extract_deals_from_image(
                image_bytes, source_description=f"7-Eleven banner {image_url}"
            )

            if not ocr_results:
                # No API key / quota exhausted / OCR failed: store the item
                # with null prices and move on, per graceful-degradation rule.
                deals.append(
                    self.build_deal(
                        title=alt_title,
                        category="Discount",
                        original_price=None,
                        sale_price=None,
                        valid_until=None,
                        image_url=image_url,
                        source_url=PROMOTION_URL,
                        extraction_method="ocr",
                    )
                )
                continue

            for item in ocr_results:
                deals.append(
                    self.build_deal(
                        title=item.get("title") or alt_title,
                        category="Discount",
                        original_price=item.get("original_price"),
                        sale_price=item.get("sale_price"),
                        valid_until=item.get("valid_until"),
                        image_url=image_url,
                        source_url=PROMOTION_URL,
                        extraction_method="ocr",
                    )
                )
        return deals

    # -- helpers -------------------------------------------------------

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

    @staticmethod
    def _extract_iso_date(raw: str | None) -> str | None:
        """
        7-Eleven publishes dates like "ถึง 31/12/2026" (DD/MM/YYYY). Convert
        to ISO 8601 when a date is present; otherwise return None.
        """
        if not raw:
            return None
        import re

        match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
        if not match:
            return None
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
