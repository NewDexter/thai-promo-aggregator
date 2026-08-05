"""
Lotus's (formerly Tesco Lotus) scraper.

Coverage notes (read before modifying — confirmed by direct inspection of
the live site, not assumed):
  * A dedicated "Weekend Shock Price" URL does NOT exist on the current
    site (an earlier version of this scraper guessed
    `/en/weekend-shock-price`; the site's own Next.js payload reports that
    path as a 404). Lotus's is a fully client-rendered Next.js storefront
    — most inner pages return only `{"page": {"status": {"code": 404}}}`
    when requested with a plain HTTP GET, i.e. there is no static HTML/JSON
    to scrape for them without executing JavaScript, which this project
    intentionally does not do (no headless browser — stays zero-cost/
    low-resource).
  * HTML path (primary, verified working): the homepage
    (www.lotuss.com/en) IS server-rendered with a real CMS payload in
    `__NEXT_DATA__` → `props.pageProps.page.data.content`, a list of
    content blocks. Blocks of type `marketingBanner` are the real weekly
    promotional campaign banners (e.g. "Big Bang" coupon campaigns, "My
    Lotus's Fest") with real CDN image URLs and links — this is the
    genuine public equivalent of a "weekly e-leaflet" for this store.
  * OCR path: marketing banners carry no text field, only an image — every
    banner is routed through `ocr_fallback.OcrFallback` to recover a
    title/price. A slug-derived fallback title (from the banner's link)
    is used if OCR is unavailable/exhausted, per the project's
    graceful-degradation rule.
  * OUT OF SCOPE (app-only): "Lotus's Reward" personalized member coupons
    require app login and per-user session tokens; not reachable from the
    public web and therefore not collected here. Category/product-listing
    pages (e.g. `/th/category/weekly-promotion`) exist but require walking
    a large per-product catalog API — out of scope for a promotions
    aggregator; revisit only if `marketingBanner` coverage proves
    insufficient.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

HOMEPAGE_URL = "https://www.lotuss.com/en"

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _slug_to_title(url: str | None) -> str:
    """Derive a readable fallback title from a promo URL slug, e.g.
    '.../promotion/big-bang-21may-12aug' -> 'Big Bang 21May 12aug'."""
    if not url:
        return "Lotus's Promotion"
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    words = [w for w in slug.replace("-", " ").replace("_", " ").split() if w]
    return " ".join(w.capitalize() for w in words) or "Lotus's Promotion"


class LotussScraper(BaseScraper):
    store_name = "Lotus's"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        response = self.fetch(HOMEPAGE_URL)
        content_blocks = self._extract_content_blocks(response.text)
        if not content_blocks:
            logger.warning(
                "[%s] no CMS content blocks found on homepage — page structure "
                "may have changed, or this run hit the CSR-only 404 shell.",
                self.store_name,
            )
            return []

        deals: list[dict[str, Any]] = []
        for block in content_blocks:
            marketing_banner = block.get("marketingBanner")
            if not isinstance(marketing_banner, dict):
                continue
            for banner in marketing_banner.get("banners", []) or []:
                deal = self._process_banner(banner)
                if deal is not None:
                    deals.append(deal)
        return deals

    # -- JSON extraction -----------------------------------------------

    @staticmethod
    def _extract_content_blocks(html: str) -> list[dict[str, Any]]:
        match = _NEXT_DATA_RE.search(html)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning("[Lotus's] failed to parse __NEXT_DATA__ JSON: %s", exc)
            return []
        page = data.get("props", {}).get("pageProps", {}).get("page", {})
        if page.get("status", {}).get("code") != 200:
            return []
        content = page.get("data", {}).get("content")
        return content if isinstance(content, list) else []

    # -- per-banner deal construction ----------------------------------

    def _process_banner(self, banner: dict[str, Any]) -> dict[str, Any] | None:
        banner_image = banner.get("bannerImage") or {}
        image_url = banner_image.get("url")
        if not image_url:
            return None
        # Lotus's CDN URLs sometimes contain literal spaces; encode for a valid request.
        image_url = image_url.replace(" ", "%20")

        source_url = banner.get("bannerLink") or HOMEPAGE_URL
        fallback_title = _slug_to_title(banner.get("bannerLink"))
        schedule = banner.get("schedule") or {}
        valid_until = self._date_only(schedule.get("endDateTime"))

        try:
            image_bytes = self.fetch_bytes(image_url)
        except Exception as exc:  # noqa: BLE001 - one bad banner shouldn't stop the run
            logger.warning("[%s] failed to download banner %s: %s", self.store_name, image_url, exc)
            return self.build_deal(
                title=fallback_title,
                category="Discount",
                original_price=None,
                sale_price=None,
                valid_until=valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="ocr",
            )

        ocr_results = self._ocr.extract_deals_from_image(
            image_bytes, source_description=f"Lotus's banner {image_url}"
        )

        if not ocr_results:
            return self.build_deal(
                title=fallback_title,
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
            title=first.get("title") or fallback_title,
            category="Discount",
            original_price=first.get("original_price"),
            sale_price=first.get("sale_price"),
            valid_until=first.get("valid_until") or valid_until,
            image_url=image_url,
            source_url=source_url,
            extraction_method="ocr",
        )

    @staticmethod
    def _date_only(iso_datetime: str | None) -> str | None:
        if not iso_datetime:
            return None
        return iso_datetime.split("T", 1)[0]
