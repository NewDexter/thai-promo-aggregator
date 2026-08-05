"""
Lotus's (formerly Tesco Lotus) scraper.

Coverage notes (read before modifying):
  * HTML path:  The "Weekend Shock Price" (ล็อตเต้ ช็อคราคา / ราคาช็อกโลก
    เฉพาะวันเสาร์-อาทิตย์) page renders each item as a structured HTML
    product tile with title and price text — scraped directly, no OCR.
  * OCR path:   The broader weekly e-leaflet ("ใบปลิวโปรโมชั่น") is
    published as a multi-page image gallery / PDF with no per-item HTML
    text. Each leaflet page image is routed through
    `ocr_fallback.OcrFallback`.
  * OUT OF SCOPE (app-only): "Lotus's Reward" personalized member coupons
    require app login and per-user session tokens; not reachable from the
    public web and therefore not collected here.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

WEEKEND_SHOCK_URL = "https://www.lotuss.com/en/weekend-shock-price"
LEAFLET_URL = "https://www.lotuss.com/en/promotions/leaflet"


class LotussScraper(BaseScraper):
    store_name = "Lotus's"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        deals.extend(self._scrape_weekend_shock_price())
        deals.extend(self._scrape_leaflet())
        return deals

    # -- Weekend Shock Price: structured HTML -----------------------------

    def _scrape_weekend_shock_price(self) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        response = self.fetch(WEEKEND_SHOCK_URL)
        soup = BeautifulSoup(response.text, "html.parser")

        for tile in soup.select(".product-tile"):
            title_el = tile.select_one(".product-title")
            if title_el is None:
                continue
            original_el = tile.select_one(".price-was")
            sale_el = tile.select_one(".price-now")
            valid_el = tile.select_one(".valid-until")

            deals.append(
                self.build_deal(
                    title=title_el.get_text(strip=True),
                    category="FlashSale",
                    original_price=original_el.get_text(strip=True) if original_el else None,
                    sale_price=sale_el.get_text(strip=True) if sale_el else None,
                    valid_until=valid_el.get("data-iso-date") if valid_el else None,
                    image_url=None,
                    source_url=WEEKEND_SHOCK_URL,
                    extraction_method="html",
                )
            )
        return deals

    # -- Weekly e-leaflet: OCR path -----------------------------------

    def _scrape_leaflet(self) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        response = self.fetch(LEAFLET_URL)
        soup = BeautifulSoup(response.text, "html.parser")

        for page_img in soup.select("img.leaflet-page"):
            image_url = page_img.get("src") or page_img.get("data-src")
            if not image_url:
                continue
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = "https://www.lotuss.com" + image_url

            try:
                image_bytes = self.fetch_bytes(image_url)
            except Exception as exc:  # noqa: BLE001 - one bad page shouldn't stop the run
                logger.warning("[%s] failed to download leaflet page %s: %s", self.store_name, image_url, exc)
                deals.append(
                    self.build_deal(
                        title="Lotus's Weekly Leaflet Page",
                        category="Discount",
                        original_price=None,
                        sale_price=None,
                        valid_until=None,
                        image_url=image_url,
                        source_url=LEAFLET_URL,
                        extraction_method="ocr",
                    )
                )
                continue

            ocr_results = self._ocr.extract_deals_from_image(
                image_bytes, source_description=f"Lotus's leaflet {image_url}"
            )

            if not ocr_results:
                deals.append(
                    self.build_deal(
                        title="Lotus's Weekly Leaflet Page",
                        category="Discount",
                        original_price=None,
                        sale_price=None,
                        valid_until=None,
                        image_url=image_url,
                        source_url=LEAFLET_URL,
                        extraction_method="ocr",
                    )
                )
                continue

            for item in ocr_results:
                deals.append(
                    self.build_deal(
                        title=item.get("title") or "Lotus's Weekly Leaflet Item",
                        category="Discount",
                        original_price=item.get("original_price"),
                        sale_price=item.get("sale_price"),
                        valid_until=item.get("valid_until"),
                        image_url=image_url,
                        source_url=LEAFLET_URL,
                        extraction_method="ocr",
                    )
                )
        return deals
