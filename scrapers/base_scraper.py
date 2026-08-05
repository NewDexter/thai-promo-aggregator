"""
Shared scraper utilities: polite HTTP fetching with retry/backoff, and
normalization helpers (Thai-formatted prices, hash_id computation) used by
every store-specific scraper.

No scraper should talk to the network directly with a bare `httpx.get` —
route all requests through `BaseScraper.fetch()` / `fetch_bytes()` so
politeness delays, retries, and a consistent User-Agent are applied
uniformly.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from config import CONFIG

logger = logging.getLogger(__name__)

# Matches Thai/English currency strings such as "1,290.00", "฿99", "99.-",
# "1290", "1,290 บาท". Captures the numeric portion only.
_PRICE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def normalize_price(raw: str | float | int | None) -> float | None:
    """
    Convert a Thai-formatted price string (e.g. "1,290.00", "฿99", "99.-")
    into a float. Returns None for missing/unparseable input rather than
    raising, since price extraction (especially via OCR) is inherently lossy.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    if not text:
        return None

    match = _PRICE_RE.search(text)
    if not match:
        return None

    numeric = match.group(1).replace(",", "")
    try:
        return round(float(numeric), 2)
    except ValueError:
        return None


def compute_hash_id(store: str, title: str, sale_price: float | None, valid_until: str | None) -> str:
    """
    MD5 of (store + title + sale_price + valid_until).

    Including valid_until means a recurring monthly promo at the same price
    is treated as a *new* alert once its validity window rolls over, instead
    of being permanently suppressed by dedup.
    """
    parts = [
        store.strip().lower(),
        title.strip().lower(),
        "" if sale_price is None else f"{sale_price:.2f}",
        (valid_until or "").strip(),
    ]
    digest_input = "|".join(parts).encode("utf-8")
    return hashlib.md5(digest_input).hexdigest()


class BaseScraper(ABC):
    """
    Common scaffolding for all store scrapers.

    Subclasses implement `scrape()` and return a list of normalized deal
    dicts matching the project-wide schema. They should use `self.fetch()`
    for all HTML requests and `self.fetch_bytes()` for images/PDFs destined
    for the OCR fallback.
    """

    store_name: str = "unknown"

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": CONFIG.scraper.user_agent},
            timeout=CONFIG.scraper.request_timeout_seconds,
            follow_redirects=True,
        )
        self._last_request_time: float = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _respect_politeness_delay(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        remaining = CONFIG.scraper.request_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_time = time.monotonic()

    def fetch(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET a URL as text, with politeness delay + retry/backoff."""
        return self._request_with_retry(url, **kwargs)

    def fetch_bytes(self, url: str, **kwargs: Any) -> bytes:
        """GET a URL and return raw bytes (for images / PDFs)."""
        response = self._request_with_retry(url, **kwargs)
        return response.content

    def _request_with_retry(self, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, CONFIG.scraper.max_retries + 1):
            self._respect_politeness_delay()
            try:
                response = self._client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning(
                    "[%s] request to %s failed (attempt %d/%d): %s",
                    self.store_name,
                    url,
                    attempt,
                    CONFIG.scraper.max_retries,
                    exc,
                )
                if attempt < CONFIG.scraper.max_retries:
                    backoff = CONFIG.scraper.backoff_base_seconds * (2 ** (attempt - 1))
                    time.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    @abstractmethod
    def scrape(self) -> list[dict[str, Any]]:
        """Return a list of normalized deal dicts for this store."""
        raise NotImplementedError

    def build_deal(
        self,
        *,
        title: str,
        category: str,
        original_price: float | str | None,
        sale_price: float | str | None,
        valid_until: str | None,
        image_url: str | None,
        source_url: str,
        extraction_method: str,
    ) -> dict[str, Any]:
        """Assemble a schema-conformant deal dict with normalized prices and hash_id."""
        norm_original = normalize_price(original_price)
        norm_sale = normalize_price(sale_price)
        return {
            "store": self.store_name,
            "title": title.strip(),
            "category": category,
            "original_price": norm_original,
            "sale_price": norm_sale,
            "valid_until": valid_until,
            "image_url": image_url,
            "source_url": source_url,
            "extraction_method": extraction_method,
            "hash_id": compute_hash_id(self.store_name, title, norm_sale, valid_until),
        }
