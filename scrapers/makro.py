"""
Makro scraper.

Coverage notes (read before modifying — confirmed by direct inspection of
the live page, not assumed):
  * HTML path: the public "discount" listing (www.makro.co.th/en/discount)
    is server-rendered HTML (not a JS SPA), but its actual weekly deals are
    presented as an image carousel (`.pro-week-item img.lazyload`) with a
    `data-src` banner image and an `alt` attribute holding the title and a
    "Period : DDMon - DDMon'YY" validity string — there is no separate
    text-based price markup on this page. Title and (best-effort) validity
    period are parsed directly from the `alt` text via plain HTML/regex,
    no OCR needed for those two fields.
  * OCR path: actual prices are only visible inside each banner image
    itself, so every banner is routed through `ocr_fallback.OcrFallback`
    to recover price data. If OCR is unavailable/exhausted, the item is
    stored with null prices per the project's graceful-degradation rule
    (title/validity are still useful without OCR).
  * OUT OF SCOPE (app-only): "Makro PRO" app-exclusive wholesale pricing
    and member-tier discounts require the Makro PRO mobile app with
    device-signed sessions and are NOT reachable from any public web page.
    Per project constraints we do not reverse-engineer that app's API —
    this scraper only reflects what a logged-out browser can see on the
    public makro.co.th site.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

DISCOUNTS_URL = "https://www.makro.co.th/en/discount"

# e.g. "FF Weekly17 Period : 5Aug - 11Aug'26" -> end date pieces ("11Aug", "26")
_PERIOD_RE = re.compile(r"Period\s*:\s*[\dA-Za-z]+\s*-\s*(\d{1,2})([A-Za-z]{3})'(\d{2})")

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_period_end_date(alt_text: str) -> str | None:
    match = _PERIOD_RE.search(alt_text)
    if not match:
        return None
    day, month_abbr, year_2digit = match.groups()
    month = _MONTHS.get(month_abbr.lower())
    if month is None:
        return None
    year = 2000 + int(year_2digit)
    return f"{year}-{month:02d}-{int(day):02d}"


class MakroScraper(BaseScraper):
    store_name = "Makro"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(DISCOUNTS_URL)
        soup = BeautifulSoup(response.text, "html.parser")
        deals: list[dict[str, Any]] = []

        for item_el in soup.select(".pro-week-item"):
            img = item_el.select_one("img.lazyload")
            if img is None:
                continue
            image_url = img.get("data-src") or img.get("src")
            if not image_url:
                continue
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = "https://www.makro.co.th" + image_url

            alt_text = img.get("alt", "").strip() or "Makro Weekly Promotion"
            valid_until = _parse_period_end_date(alt_text)

            link_el = item_el.select_one("a")
            source_url = link_el.get("href") if link_el and link_el.get("href") else DISCOUNTS_URL

            deals.append(self._build_deal_with_ocr(alt_text, image_url, valid_until, source_url))

        return deals

    def _build_deal_with_ocr(
        self, alt_text: str, image_url: str, valid_until: str | None, source_url: str
    ) -> dict[str, Any]:
        try:
            image_bytes = self.fetch_bytes(image_url)
        except Exception as exc:  # noqa: BLE001 - one bad banner shouldn't stop the run
            logger.warning("[%s] failed to download banner %s: %s", self.store_name, image_url, exc)
            return self.build_deal(
                title=alt_text,
                category="Discount",
                original_price=None,
                sale_price=None,
                valid_until=valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="ocr",
            )

        ocr_results = self._ocr.extract_deals_from_image(
            image_bytes, source_description=f"Makro banner {image_url}"
        )

        if not ocr_results:
            return self.build_deal(
                title=alt_text,
                category="Discount",
                original_price=None,
                sale_price=None,
                valid_until=valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="ocr",
            )

        first = ocr_results[0]
        return self.build_deal(
            title=first.get("title") or alt_text,
            category="Discount",
            original_price=first.get("original_price"),
            sale_price=first.get("sale_price"),
            valid_until=first.get("valid_until") or valid_until,
            image_url=image_url,
            source_url=source_url,
            extraction_method="ocr",
        )
