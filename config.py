"""
Central configuration for the Thai Convenience Store & Grocery Promotion Aggregator.

All values are sourced from environment variables so the same code runs
identically on a developer's laptop (via a local `.env` loaded by the caller,
or plain exported shell vars) and inside GitHub Actions (via `secrets` /
`vars` mapped to `env:` in the workflow). Nothing in this file requires a
paid service — every default is chosen to keep the project at zero cost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Repo root = directory containing this file. Kept relative so the project
# works the same whether run locally or from the GitHub Actions checkout.
BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"
OCR_CACHE_DIR = CACHE_DIR / "ocr"

DATABASE_PATH = DATA_DIR / "deals.db"

# Ensure directories that get committed back to the repo always exist,
# even on a completely fresh checkout.
DATA_DIR.mkdir(parents=True, exist_ok=True)
OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back safely on bad input."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str | None = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN"))
    chat_id: str | None = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID"))
    # Telegram allows roughly 1 message/sec to a given chat before it starts
    # throttling (HTTP 429). We stay comfortably under that.
    send_delay_seconds: float = field(
        default_factory=lambda: float(os.environ.get("TELEGRAM_SEND_DELAY_SECONDS", "1.1"))
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token) and bool(self.chat_id)


@dataclass(frozen=True)
class OcrConfig:
    """
    Configuration for the free-tier Gemini Flash OCR fallback.

    OCR is used ONLY for stores that publish deals as banner images / PDF
    leaflets instead of structured HTML (Lotus's, 7-Eleven, CJ More — see
    each scraper's docstring for exactly which parts). If `api_key` is not
    set, OCR is skipped entirely and affected items are stored with null
    prices rather than crashing the run.
    """

    api_key: str | None = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY"))
    model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))

    # Conservative defaults to stay inside the Gemini free tier and to avoid
    # hammering retailer image CDNs. These are intentionally low — bump via
    # env vars if your free-tier quota allows more.
    max_ocr_calls_per_run: int = field(default_factory=lambda: _env_int("OCR_MAX_CALLS_PER_RUN", 15))
    max_ocr_calls_per_day: int = field(default_factory=lambda: _env_int("OCR_MAX_CALLS_PER_DAY", 40))

    request_timeout_seconds: int = field(default_factory=lambda: _env_int("OCR_TIMEOUT_SECONDS", 30))

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class ScraperConfig:
    # Politeness delay between HTTP requests to the same host, in seconds.
    request_delay_seconds: float = field(
        default_factory=lambda: float(os.environ.get("SCRAPER_REQUEST_DELAY_SECONDS", "1.5"))
    )
    request_timeout_seconds: int = field(default_factory=lambda: _env_int("SCRAPER_TIMEOUT_SECONDS", 20))
    max_retries: int = field(default_factory=lambda: _env_int("SCRAPER_MAX_RETRIES", 3))
    backoff_base_seconds: float = field(
        default_factory=lambda: float(os.environ.get("SCRAPER_BACKOFF_BASE_SECONDS", "2.0"))
    )
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "SCRAPER_USER_AGENT",
            "Mozilla/5.0 (compatible; ThaiPromoAggregator/1.0; "
            "+https://github.com/) Python-httpx",
        )
    )
    # After this many consecutive failed runs, a store fires a single
    # low-noise admin alert instead of failing silently or spamming.
    consecutive_failure_alert_threshold: int = field(
        default_factory=lambda: _env_int("SCRAPER_FAILURE_ALERT_THRESHOLD", 3)
    )


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", False))


CONFIG = AppConfig()
