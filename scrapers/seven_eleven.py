"""
7-Eleven Thailand scraper.

Coverage notes (read before modifying — confirmed by direct inspection of
the live page, not assumed):
  * HTML path (primary):  www.7eleven.co.th/promotion is a Next.js page
    that embeds its full promotion payload as JSON inside
    `<script id="__NEXT_DATA__">`. `props.pageProps` contains one dict per
    promotion section (`sale`, `trade`, `matching`, `allmember`,
    `savemore`, `bigpack`, `only`, `category`, ...), each with an `items`
    list. Individual items carry real structured fields — `price`,
    `normal_price`, `title_th`/`title_en`, `start_date`/`end_date` — so
    most deals are extracted as plain "html" (the JSON is part of the
    served HTML document, no extra request needed), with NO OCR required.
  * OCR path (fallback only):  Some items have `price`/`normal_price`
    both null but do carry a `banner_image`/`mobile_image`/`thumb_image`.
    For those (and only those), the banner is downloaded and routed
    through `ocr_fallback.OcrFallback` to recover a price. If OCR is
    unavailable/exhausted, the item is stored with null prices per the
    project's graceful-degradation rule.
  * OUT OF SCOPE (app-only): "7-Delivery" exclusive coupons and certain
    All Member in-app-only flash deals live inside the 7-Eleven mobile app
    behind a signed session and are NOT reachable from any public web
    page. We do not reverse-engineer the app API — these are simply not
    collected. This scraper only reflects what a logged-out browser sees
    on the public site.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from scrapers.base_scraper import BaseScraper
from ocr_fallback import OcrFallback

logger = logging.getLogger(__name__)

PROMOTION_URL = "https://www.7eleven.co.th/promotion"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)

# pageProps keys that hold promotion sections (as opposed to metadata keys
# like `dataPages` or `heroBanner`).
_SECTION_KEYS = (
    "category",
    "sale",
    "savemore",
    "bigpack",
    "only",
    "trade",
    "matching",
    "allmember",
    "redeem",
    "stamp",
)

_IMAGE_FIELDS = ("banner_image", "mobile_image", "thumb_image", "rectangle_image")


def _first_image_url(item: dict[str, Any]) -> str | None:
    for field in _IMAGE_FIELDS:
        images = item.get(field)
        if isinstance(images, list) and images:
            url = images[0].get("url")
            if url:
                return url
    return None


def _date_only(iso_datetime: str | None) -> str | None:
    """"2025-04-21T19:00:00.000Z" -> "2025-04-21". None-safe."""
    if not iso_datetime:
        return None
    return iso_datetime.split("T", 1)[0]


class SevenElevenScraper(BaseScraper):
    store_name = "7-Eleven"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        # Shared across stores in one orchestrator run so OCR quota counters
        # apply to the whole pipeline, not per-store.
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(PROMOTION_URL)
        page_props = self._extract_page_props(response.text)
        deals: list[dict[str, Any]] = []

        for section_key in _SECTION_KEYS:
            section = page_props.get(section_key)
            if not isinstance(section, dict):
                continue
            items = section.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                deal = self._build_deal_from_item(section_key, item)
                if deal is not None:
                    deals.append(deal)
        return deals

    # -- JSON extraction -----------------------------------------------

    @staticmethod
    def _extract_page_props(html: str) -> dict[str, Any]:
        match = _NEXT_DATA_RE.search(html)
        if not match:
            logger.warning(
                "7-Eleven: __NEXT_DATA__ script tag not found — page structure "
                "may have changed."
            )
            return {}
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning("7-Eleven: failed to parse __NEXT_DATA__ JSON: %s", exc)
            return {}
        return data.get("props", {}).get("pageProps", {})

    # -- per-item deal construction ----------------------------------

    def _build_deal_from_item(self, section_key: str, item: dict[str, Any]) -> dict[str, Any] | None:
        title = item.get("title_th") or item.get("title_en")
        if not title:
            return None

        price = item.get("price")
        normal_price = item.get("normal_price")
        image_url = _first_image_url(item)
        valid_until = _date_only(item.get("end_date"))
        item_url = item.get("item_url") or item.get("url")
        source_url = (
            f"https://www.7eleven.co.th{item_url}" if item_url and item_url.startswith("/") else (item_url or PROMOTION_URL)
        )
        category = self._normalize_category(section_key, title)

        if price is not None or normal_price is not None or not image_url:
            # Real structured price data already present (or no image to OCR
            # at all) — no need to touch the network again.
            return self.build_deal(
                title=title,
                category=category,
                original_price=normal_price,
                sale_price=price,
                valid_until=valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="html",
            )

        # No price in the JSON but a banner image exists: try OCR.
        try:
            image_bytes = self.fetch_bytes(image_url)
        except Exception as exc:  # noqa: BLE001 - one bad banner shouldn't stop the run
            logger.warning("[%s] failed to download banner %s: %s", self.store_name, image_url, exc)
            return self.build_deal(
                title=title,
                category=category,
                original_price=None,
                sale_price=None,
                valid_until=valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="ocr",
            )

        ocr_results = self._ocr.extract_deals_from_image(
            image_bytes, source_description=f"7-Eleven banner {image_url}"
        )
        if not ocr_results:
            # No API key / quota exhausted / OCR failed: null prices, no crash.
            return self.build_deal(
                title=title,
                category=category,
                original_price=None,
                sale_price=None,
                valid_until=valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="ocr",
            )

        first = ocr_results[0]
        return self.build_deal(
            title=first.get("title") or title,
            category=category,
            original_price=first.get("original_price"),
            sale_price=first.get("sale_price"),
            valid_until=first.get("valid_until") or valid_until,
            image_url=image_url,
            source_url=source_url,
            extraction_method="ocr",
        )

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _normalize_category(section_key: str, title: str) -> str:
        title_lower = title.lower()
        if section_key == "allmember":
            return "MemberExclusive"
        if "1 แถม 1" in title or "buy 1 get 1" in title_lower or "b1g1" in title_lower:
            return "Buy1Get1"
        if "flash" in title_lower:
            return "FlashSale"
        if section_key == "sale":
            return "Discount"
        return "Discount"
