"""
Telegram notification sender.

Pure string formatting only — NO LLM involvement, per project constraints.
Messages are sent sequentially with a small delay between sends to respect
Telegram's roughly 1 message/second per-chat rate limit.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import CONFIG

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Exact template specified in the project spec. `original_price` /
# `sale_price` are pre-formatted strings before substitution so that
# `None` renders as a friendly placeholder rather than the literal "None".
MESSAGE_TEMPLATE = (
    "<b>🏬 {store}</b> | {category}\n"
    "🔥 <b>{title}</b>\n"
    "💰 <s>{original_price}</s> ➔ <b>{sale_price} THB</b>\n"
    "⏰ Valid until: {valid_until}\n"
    "🔗 <a href='{source_url}'>View Deal</a>"
)


def _format_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def format_deal_message(deal: dict[str, Any]) -> str:
    """Render a single deal dict into the fixed HTML Telegram template."""
    return MESSAGE_TEMPLATE.format(
        store=_escape_html(deal["store"]),
        category=_escape_html(deal["category"]),
        title=_escape_html(deal["title"]),
        original_price=_format_price(deal.get("original_price")),
        sale_price=_format_price(deal.get("sale_price")),
        valid_until=deal.get("valid_until") or "See source for details",
        source_url=deal["source_url"],
    )


def _escape_html(text: str) -> str:
    """Minimal HTML escaping for Telegram's HTML parse mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class TelegramNotifier:
    """
    Thin wrapper around the Telegram Bot `sendMessage` API.

    If Telegram credentials aren't configured, `send()` logs and returns
    False rather than raising — the pipeline should still run (e.g. for
    local dry runs against fixtures) without valid secrets.
    """

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TelegramNotifier":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def send_html(self, text: str) -> bool:
        """Send one HTML-formatted message. Returns True on success."""
        if not CONFIG.telegram.is_configured:
            logger.info("Telegram not configured; skipping send. Message would have been:\n%s", text)
            return False

        url = f"{TELEGRAM_API_BASE}/bot{CONFIG.telegram.bot_token}/sendMessage"
        payload = {
            "chat_id": CONFIG.telegram.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    def send_deals(self, deals: list[dict[str, Any]]) -> int:
        """
        Send one message per deal, rate-limited. Returns count of successful sends.
        """
        sent = 0
        for i, deal in enumerate(deals):
            if i > 0:
                time.sleep(CONFIG.telegram.send_delay_seconds)
            message = format_deal_message(deal)
            if self.send_html(message):
                sent += 1
        return sent

    def send_admin_alert(self, text: str) -> bool:
        """Send a plain low-noise admin/ops message (e.g. scraper health)."""
        return self.send_html(text)
