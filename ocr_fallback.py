"""
Free-tier OCR fallback for stores that publish weekly deals as banner images
or PDF e-leaflets rather than structured HTML (Lotus's, 7-Eleven, CJ More).

Design goals:
  * Zero cost by default: uses the Gemini Flash free tier, and does nothing
    at all (returns None) if GEMINI_API_KEY isn't set.
  * Never OCR the same image twice: results are cached on disk keyed by the
    SHA-256 hash of the image bytes.
  * Quota-aware: enforces a conservative per-run AND per-day call ceiling so
    a burst of new banners in one run can't blow through the free tier.
  * Never crashes the run: any failure (network, quota, malformed response)
    is logged and results in a `None` return so the caller can fall back to
    storing the item with null prices.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from config import CONFIG, OCR_CACHE_DIR

logger = logging.getLogger(__name__)

# Prompt kept intentionally narrow and structured so the free-tier model
# returns something we can parse deterministically with json.loads — no
# downstream LLM calls needed to interpret the result.
_OCR_PROMPT = """You are reading a Thai retail promotion banner or leaflet page.
Extract every distinct product promotion visible in the image.
Respond with ONLY a JSON array (no markdown fences, no commentary) where each
element has this exact shape:
{"title": "<product name, Thai or English as shown>",
 "original_price": <number or null>,
 "sale_price": <number or null>,
 "valid_until": "<ISO date YYYY-MM-DD or null if not shown>"}
If you cannot read any promotions, respond with an empty JSON array: []
"""

_DAILY_COUNTER_FILE = OCR_CACHE_DIR / "daily_call_count.json"


class OcrQuotaExceeded(Exception):
    """Raised internally when a call would exceed the configured quota."""


class OcrFallback:
    """
    Stateful per-run helper: tracks how many OCR calls have been made this
    run (and today, persisted to disk) and enforces the configured ceilings.
    """

    def __init__(self) -> None:
        self._calls_this_run = 0
        self._daily_count, self._daily_date = self._load_daily_counter()

    # -- public API ---------------------------------------------------

    def extract_deals_from_image(
        self, image_bytes: bytes, *, source_description: str
    ) -> list[dict[str, Any]] | None:
        """
        Return a list of {"title", "original_price", "sale_price",
        "valid_until"} dicts extracted from the image, or None if OCR could
        not be performed (no API key, quota exhausted, or a request error).

        Never raises — all failure modes degrade to None so the caller can
        store the item with null prices instead of crashing the run.
        """
        if not CONFIG.ocr.is_configured:
            logger.info(
                "OCR skipped for %s: GEMINI_API_KEY not configured", source_description
            )
            return None

        image_hash = hashlib.sha256(image_bytes).hexdigest()
        cached = self._read_cache(image_hash)
        if cached is not None:
            logger.info("OCR cache hit for %s (hash=%s)", source_description, image_hash[:12])
            return cached

        try:
            self._enforce_quota()
        except OcrQuotaExceeded as exc:
            logger.warning("OCR quota exceeded, skipping %s: %s", source_description, exc)
            return None

        try:
            deals = self._call_gemini(image_bytes)
        except Exception as exc:  # noqa: BLE001 - OCR must never crash the run
            logger.warning("OCR call failed for %s: %s", source_description, exc)
            return None

        self._write_cache(image_hash, deals)
        self._calls_this_run += 1
        self._increment_daily_counter()
        return deals

    # -- quota bookkeeping ---------------------------------------------

    def _enforce_quota(self) -> None:
        if self._calls_this_run >= CONFIG.ocr.max_ocr_calls_per_run:
            raise OcrQuotaExceeded(
                f"per-run limit of {CONFIG.ocr.max_ocr_calls_per_run} reached"
            )
        if self._daily_count >= CONFIG.ocr.max_ocr_calls_per_day:
            raise OcrQuotaExceeded(
                f"per-day limit of {CONFIG.ocr.max_ocr_calls_per_day} reached"
            )

    def _load_daily_counter(self) -> tuple[int, str]:
        today = date.today().isoformat()
        if not _DAILY_COUNTER_FILE.exists():
            return 0, today
        try:
            payload = json.loads(_DAILY_COUNTER_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return 0, today
        if payload.get("date") != today:
            return 0, today
        return int(payload.get("count", 0)), today

    def _increment_daily_counter(self) -> None:
        self._daily_count += 1
        _DAILY_COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DAILY_COUNTER_FILE.write_text(
            json.dumps({"date": self._daily_date, "count": self._daily_count})
        )

    # -- disk cache -------------------------------------------------------

    def _cache_path(self, image_hash: str) -> Path:
        return OCR_CACHE_DIR / f"{image_hash}.json"

    def _read_cache(self, image_hash: str) -> list[dict[str, Any]] | None:
        path = self._cache_path(image_hash)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, image_hash: str, deals: list[dict[str, Any]]) -> None:
        path = self._cache_path(image_hash)
        try:
            path.write_text(json.dumps(deals, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("Failed to write OCR cache for %s: %s", image_hash[:12], exc)

    # -- Gemini call --------------------------------------------------

    def _call_gemini(self, image_bytes: bytes) -> list[dict[str, Any]]:
        import base64

        b64_image = base64.b64encode(image_bytes).decode("ascii")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{CONFIG.ocr.model}:generateContent?key={CONFIG.ocr.api_key}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _OCR_PROMPT},
                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        with httpx.Client(timeout=CONFIG.ocr.request_timeout_seconds) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError(f"expected JSON array from OCR, got: {type(parsed)}")
        return parsed
