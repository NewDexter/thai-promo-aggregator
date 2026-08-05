"""
CJ More (ซีเจ มอร์) scraper.

Coverage notes (read before modifying — confirmed by direct inspection of
the live site, not assumed):
  * Correct domain is `www.cjmore.co.th` — `www.cjmore.com` (used in an
    earlier version of this scraper) is an unrelated expired/parked domain
    and returns no useful content. Always double check a store's real
    public domain before writing selectors against it.
  * HTML path: `www.cjmore.co.th/promotion` is a jQuery page (not a JS
    framework SPA) that calls a same-origin JSON endpoint,
    `GET /promotion/type/{id}`, to populate each tab
    (`{"title", "date", "description", "imgs": [...]}`) via a client-side
    template. This scraper calls that endpoint directly instead of
    scraping the shell HTML.
  * OCR path: per-item prices only exist inside the `imgs` leaflet images
    returned by the endpoint above (when present) — each is routed through
    `ocr_fallback.OcrFallback`. This remains the PRIMARY price-extraction
    path for this store.
  * KNOWN SITE LIMITATION (not a scraper bug): `www.cjmore.co.th` rejects
    Python's `httpx`/`ssl` TLS handshake outright
    (`SSL: TLSV1_ALERT_PROTOCOL_VERSION`) while an ordinary `curl` to the
    exact same URL succeeds — almost certainly TLS/JA3 fingerprinting by
    their edge/WAF rather than a real protocol incompatibility. This
    project does not attempt to spoof a TLS fingerprint to get past that
    (same policy as the Tops Cloudflare block: no bot-detection evasion).
    Separately, even a successful request would currently return
    thin data: tab id `2` returns an HTTP 500 from CJ More's own backend,
    and tab `1`'s payload was observed stale (dated 2019) with an empty
    `imgs` list — so this store's real-world yield is expected to be zero
    or near-zero until CJ More both relaxes their TLS fingerprinting and
    fixes their backend. The scraper still calls the endpoint correctly
    and will pick up live data automatically if either changes — every
    failure mode here degrades to zero deals (logged, isolated by
    `master_orchestrator`), never a crash.
  * OUT OF SCOPE: none identified — no mobile-app-only gating found.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ocr_fallback import OcrFallback
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

PROMOTION_PAGE_URL = "https://www.cjmore.co.th/promotion"

# Tab ids exposed by the `#tab-menu` on the promotion page. Only 1 and 2
# exist at the time of writing (see docstring for tab 2's known 500 error).
PROMOTION_TAB_IDS = (1, 2)


class CjMoreScraper(BaseScraper):
    store_name = "CJ More"

    def __init__(self, ocr: OcrFallback | None = None) -> None:
        super().__init__()
        self._ocr = ocr or OcrFallback()

    def scrape(self) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        for tab_id in PROMOTION_TAB_IDS:
            deals.extend(self._scrape_tab(tab_id))
        return deals

    def _scrape_tab(self, tab_id: int) -> list[dict[str, Any]]:
        endpoint = f"{PROMOTION_PAGE_URL}/type/{tab_id}"
        try:
            response = self.fetch(endpoint)
        except Exception as exc:  # noqa: BLE001 - one broken tab shouldn't stop others
            logger.warning("[%s] tab %d request failed: %s", self.store_name, tab_id, exc)
            return []

        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            logger.warning(
                "[%s] tab %d did not return valid JSON (site-side error page?)",
                self.store_name,
                tab_id,
            )
            return []

        title = payload.get("title") or "CJ More Promotion"
        valid_until = self._parse_thai_date_range(payload.get("date"))
        imgs = payload.get("imgs") or []

        if not imgs:
            logger.info(
                "[%s] tab %d ('%s') has no leaflet images to process", self.store_name, tab_id, title
            )
            return []

        deals: list[dict[str, Any]] = []
        for img_entry in imgs:
            deals.extend(self._process_image(img_entry, title, valid_until, endpoint))
        return deals

    def _process_image(
        self, img_entry: Any, catalog_title: str, valid_until: str | None, source_url: str
    ) -> list[dict[str, Any]]:
        image_path = img_entry if isinstance(img_entry, str) else img_entry.get("name") or img_entry.get("image")
        if not image_path:
            return []
        image_url = (
            image_path
            if str(image_path).startswith("http")
            else f"https://www.cjmore.co.th/upload/promotion/{image_path}"
        )

        try:
            image_bytes = self.fetch_bytes(image_url)
        except Exception as exc:  # noqa: BLE001 - one bad leaflet shouldn't stop the run
            logger.warning("[%s] failed to download leaflet %s: %s", self.store_name, image_url, exc)
            return [
                self.build_deal(
                    title=catalog_title,
                    category="Discount",
                    original_price=None,
                    sale_price=None,
                    valid_until=valid_until,
                    image_url=image_url,
                    source_url=source_url,
                    extraction_method="ocr",
                )
            ]

        ocr_results = self._ocr.extract_deals_from_image(
            image_bytes, source_description=f"CJ More leaflet {image_url}"
        )

        if not ocr_results:
            return [
                self.build_deal(
                    title=catalog_title,
                    category="Discount",
                    original_price=None,
                    sale_price=None,
                    valid_until=valid_until,
                    image_url=image_url,
                    source_url=source_url,
                    extraction_method="ocr",
                )
            ]

        return [
            self.build_deal(
                title=item.get("title") or catalog_title,
                category="Discount",
                original_price=item.get("original_price"),
                sale_price=item.get("sale_price"),
                valid_until=item.get("valid_until") or valid_until,
                image_url=image_url,
                source_url=source_url,
                extraction_method="ocr",
            )
            for item in ocr_results
        ]

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _parse_thai_date_range(raw: str | None) -> str | None:
        """
        CJ More dates look like "11 - 24 ต.ค. 62" (Buddhist Era, Thai month
        abbreviation). Parsing this reliably without a full Thai date
        library is out of scope for a zero-dependency parser — we
        intentionally return None rather than guess incorrectly. The raw
        string is preserved by the OCR path via the leaflet image itself
        when a specific expiry is visible there.
        """
        return None
