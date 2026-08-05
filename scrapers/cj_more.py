"""
CJ More (ซีเจ มอร์) scraper.

Coverage notes (read before modifying):
  * HTML path:  The public "promotions" landing page lists each weekly
    catalog as a card with a title and a link to a PDF/JPEG leaflet — the
    card title, validity dates, and leaflet URL are plain HTML and are
    scraped directly.
  * OCR path:   CJ More does not publish individual item prices as HTML —
    the actual per-product discounts only exist inside the catalog leaflet
    image/PDF pages themselves. Each leaflet page image is downloaded and
    routed through `ocr_fallback.OcrFallback` to extract per-item deals.
    This is the PRIMARY extraction path for this store, not a fallback.
  * OUT OF SCOPE: none currently identified — CJ More does not appear to
    gate any promotional content behind a mobile app login at the time of
    writing. Revisit this note if that changes.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PROMOTION_URL = "https://www.cjmore.com/promotion"


class CjMoreScraper(BaseScraper):
    store_name = "CJ More"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(PROMOTION_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        deals: list[dict[str, Any]] = []

        for catalog in soup.select(".catalog-card"):
            deals.extend(self._process_catalog_card(catalog))

        return deals

    def _process_catalog_card(self, catalog: Any) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []

        title_el = catalog.select_one(".catalog-title")
        leaflet_img = catalog.select_one("img.catalog-leaflet")
        valid_el = catalog.select_one(".catalog-valid-until")

        catalog_title = title_el.get_text(strip=True) if title_el else "CJ More Weekly Catalog"
        valid_until = valid_el.get("data-iso-date") if valid_el else None

        if leaflet_img is None:
            return deals

        image_url = leaflet_img.get("src") or leaflet_img.get("data-src")
        if not image_url:
            return deals
        if image_url.startswith("//"):
            image_url = "https:" + image_url
        elif image_url.startswith("/"):
            image_url = "https://www.cjmore.com" + image_url

        try:
            image_bytes = self.fetch_bytes(image_url)
        except Exception as exc:  # noqa: BLE001 - one bad leaflet shouldn't stop the run
            logger.warning("[%s] failed to download leaflet %s: %s", self.store_name, image_url, exc)
            deals.append(
                self.build_deal(
                    title=catalog_title,
                    category="Discount",
                    original_price=None,
                    sale_price=None,
                    valid_until=valid_until,
                    image_url=image_url,
                    source_url=PROMOTION_URL,
                    extraction_method="ocr",
                )
            )
            return deals

        ocr_results = self._ocr.extract_deals_from_image(
            image_bytes, source_description=f"CJ More leaflet {image_url}"
        )

        if not ocr_results:
            deals.append(
                self.build_deal(
                    title=catalog_title,
                    category="Discount",
                    original_price=None,
                    sale_price=None,
                    valid_until=valid_until,
                    image_url=image_url,
                    source_url=PROMOTION_URL,
                    extraction_method="ocr",
                )
            )
            return deals

        for item in ocr_results:
            deals.append(
                self.build_deal(
                    title=item.get("title") or catalog_title,
                    category="Discount",
                    original_price=item.get("original_price"),
                    sale_price=item.get("sale_price"),
                    valid_until=item.get("valid_until") or valid_until,
                    image_url=image_url,
                    source_url=PROMOTION_URL,
                    extraction_method="ocr",
                )
            )
        return deals
