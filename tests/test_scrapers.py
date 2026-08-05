"""
Tests run entirely against local fixtures — no live network calls — so CI
is deterministic. HTTP-fetching methods on BaseScraper are monkeypatched to
return fixture content instead of hitting the network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.base_scraper import compute_hash_id, normalize_price
from scrapers.cj_more import CjMoreScraper
from scrapers.lotuss import LotussScraper
from scrapers.makro import MakroScraper
from scrapers.seven_eleven import SevenElevenScraper
from scrapers.tops_gourmet import TopsGourmetScraper

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, scraper: Any, html_by_url: dict[str, str]) -> None:
    def fake_fetch(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(html_by_url[url])

    monkeypatch.setattr(scraper, "fetch", fake_fetch)


def _patch_fetch_bytes(monkeypatch: pytest.MonkeyPatch, scraper: Any) -> None:
    dummy_bytes = (FIXTURES / "dummy_banner.jpg").read_bytes()

    def fake_fetch_bytes(url: str, **kwargs: Any) -> bytes:
        return dummy_bytes

    monkeypatch.setattr(scraper, "fetch_bytes", fake_fetch_bytes)


# ---------------------------------------------------------------------------
# base_scraper helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,290.00", 1290.00),
        ("฿99", 99.0),
        ("99.-", 99.0),
        ("1290", 1290.0),
        ("1,290 บาท", 1290.0),
        (None, None),
        ("no digits here", None),
        (59, 59.0),
        (59.5, 59.5),
    ],
)
def test_normalize_price(raw: Any, expected: float | None) -> None:
    assert normalize_price(raw) == expected


def test_hash_id_changes_when_valid_until_rolls_over() -> None:
    """A recurring promo at the same price should get a new hash once its
    validity window changes, so it triggers a fresh alert."""
    h1 = compute_hash_id("Lotus's", "Jasmine Rice 5kg", 199.0, "2026-08-09")
    h2 = compute_hash_id("Lotus's", "Jasmine Rice 5kg", 199.0, "2026-09-09")
    assert h1 != h2


def test_hash_id_stable_for_identical_input() -> None:
    h1 = compute_hash_id("Makro", "Cooking Oil 5L", 399.0, "2026-08-15")
    h2 = compute_hash_id("Makro", "Cooking Oil 5L", 399.0, "2026-08-15")
    assert h1 == h2


# ---------------------------------------------------------------------------
# Makro (pure HTML, no OCR)
# ---------------------------------------------------------------------------


def test_makro_scraper_parses_html_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = MakroScraper()
    html = (FIXTURES / "makro_discount.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.makro.co.th/en/discount": html})

    deals = scraper.scrape()

    assert len(deals) == 2
    shrimp = deals[0]
    assert shrimp["title"] == "Frozen Shrimp 1kg (Box of 12)"
    assert shrimp["category"] == "MemberExclusive"
    assert shrimp["original_price"] == 1290.00
    assert shrimp["sale_price"] == 990.00
    assert shrimp["valid_until"] == "2026-08-15"
    assert shrimp["extraction_method"] == "html"
    assert shrimp["store"] == "Makro"
    assert len(shrimp["hash_id"]) == 32


# ---------------------------------------------------------------------------
# Tops & Gourmet Market (pure HTML, no OCR)
# ---------------------------------------------------------------------------


def test_tops_scraper_parses_html_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = TopsGourmetScraper()
    html = (FIXTURES / "tops_promotions.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.tops.co.th/en/promotions": html})

    deals = scraper.scrape()

    assert len(deals) == 1
    deal = deals[0]
    assert deal["title"] == "Imported Cheddar Cheese 200g"
    assert deal["category"] == "Buy1Get1"
    assert deal["original_price"] == 159.0
    assert deal["sale_price"] == 99.0
    assert deal["extraction_method"] == "html"


# ---------------------------------------------------------------------------
# 7-Eleven (HTML cards + OCR banner, graceful degradation without API key)
# ---------------------------------------------------------------------------


def test_seven_eleven_html_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = SevenElevenScraper()
    html = (FIXTURES / "seven_eleven_promotion.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.7eleven.co.th/promotion": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    html_deals = [d for d in deals if d["extraction_method"] == "html"]
    assert len(html_deals) == 2
    assert html_deals[0]["title"] == "Sandwich Ham & Cheese"
    assert html_deals[0]["category"] == "Buy1Get1"
    assert html_deals[0]["original_price"] == 45.0
    assert html_deals[0]["sale_price"] == 25.0
    assert html_deals[0]["valid_until"] == "2026-12-31"


def test_seven_eleven_ocr_graceful_degradation_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without GEMINI_API_KEY set, the banner should still be recorded, with
    null prices, and the run must not crash."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Reload config so CONFIG.ocr.is_configured reflects the cleared env var.
    import importlib

    import config as config_module

    importlib.reload(config_module)

    scraper = SevenElevenScraper()
    html = (FIXTURES / "seven_eleven_promotion.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.7eleven.co.th/promotion": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    ocr_deals = [d for d in deals if d["extraction_method"] == "ocr"]
    assert len(ocr_deals) == 1
    assert ocr_deals[0]["sale_price"] is None
    assert ocr_deals[0]["original_price"] is None
    assert ocr_deals[0]["title"] == "Weekly Snack Deals Banner"


# ---------------------------------------------------------------------------
# CJ More (OCR-primary, graceful degradation)
# ---------------------------------------------------------------------------


def test_cj_more_ocr_graceful_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import importlib

    import config as config_module

    importlib.reload(config_module)

    scraper = CjMoreScraper()
    html = (FIXTURES / "cj_more_promotion.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.cjmore.com/promotion": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    assert len(deals) == 1
    assert deals[0]["extraction_method"] == "ocr"
    assert deals[0]["sale_price"] is None
    assert deals[0]["valid_until"] == "2026-08-07"


# ---------------------------------------------------------------------------
# Lotus's (HTML Weekend Shock Price + OCR leaflet)
# ---------------------------------------------------------------------------


def test_lotuss_weekend_shock_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import importlib

    import config as config_module

    importlib.reload(config_module)

    scraper = LotussScraper()
    shock_html = (FIXTURES / "lotuss_weekend_shock.html").read_text()
    leaflet_html = (FIXTURES / "lotuss_leaflet.html").read_text()
    _patch_fetch(
        monkeypatch,
        scraper,
        {
            "https://www.lotuss.com/en/weekend-shock-price": shock_html,
            "https://www.lotuss.com/en/promotions/leaflet": leaflet_html,
        },
    )
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    html_deals = [d for d in deals if d["extraction_method"] == "html"]
    ocr_deals = [d for d in deals if d["extraction_method"] == "ocr"]

    assert len(html_deals) == 2
    assert html_deals[0]["title"] == "Fresh Chicken Breast 500g"
    assert html_deals[0]["category"] == "FlashSale"
    assert html_deals[0]["sale_price"] == 59.0

    assert len(ocr_deals) == 1
    assert ocr_deals[0]["sale_price"] is None  # graceful degradation, no API key


# ---------------------------------------------------------------------------
# Telegram message formatting (pure string formatting, no LLM)
# ---------------------------------------------------------------------------


def test_format_deal_message_full_data() -> None:
    from telegram_notifier import format_deal_message

    deal = {
        "store": "Makro",
        "category": "Discount",
        "title": "Cooking Oil 5L",
        "original_price": 450.0,
        "sale_price": 399.0,
        "valid_until": "2026-08-15",
        "source_url": "https://www.makro.co.th/en/discount",
    }
    msg = format_deal_message(deal)
    assert "<b>🏬 Makro</b> | Discount" in msg
    assert "<b>Cooking Oil 5L</b>" in msg
    assert "450.00" in msg
    assert "399.00 THB" in msg
    assert "2026-08-15" in msg
    assert "https://www.makro.co.th/en/discount" in msg


def test_format_deal_message_null_prices_and_dates() -> None:
    from telegram_notifier import format_deal_message

    deal = {
        "store": "7-Eleven",
        "category": "Discount",
        "title": "Weekly Snack Banner",
        "original_price": None,
        "sale_price": None,
        "valid_until": None,
        "source_url": "https://www.7eleven.co.th/promotion",
    }
    msg = format_deal_message(deal)
    assert "N/A" in msg
    assert "See source for details" in msg


# ---------------------------------------------------------------------------
# Database dedup logic
# ---------------------------------------------------------------------------


def test_database_dedup_roundtrip(tmp_path: Path) -> None:
    from database import get_connection, is_new_deal, upsert_deal

    db_path = tmp_path / "test.db"
    deal = {
        "hash_id": "abc123",
        "store": "Makro",
        "title": "Test Item",
        "category": "Discount",
        "original_price": 100.0,
        "sale_price": 80.0,
        "valid_until": "2026-08-15",
        "image_url": None,
        "source_url": "https://example.com",
        "extraction_method": "html",
    }

    with get_connection(db_path) as conn:
        assert is_new_deal(conn, "abc123") is True
        first_insert = upsert_deal(conn, deal)
        assert first_insert is True

    # Re-open connection to simulate a fresh run picking up committed state.
    with get_connection(db_path) as conn:
        assert is_new_deal(conn, "abc123") is False
        second_insert = upsert_deal(conn, deal)
        assert second_insert is False


# ---------------------------------------------------------------------------
# Scraper isolation: one store failing must not affect others
# ---------------------------------------------------------------------------


def test_run_scraper_isolated_survives_exception(tmp_path: Path) -> None:
    from database import get_connection
    from master_orchestrator import run_scraper_isolated
    from scrapers.base_scraper import BaseScraper

    class ExplodingScraper(BaseScraper):
        store_name = "ExplodingStore"

        def scrape(self) -> list[dict[str, Any]]:
            raise RuntimeError("site redesign broke the selectors")

    db_path = tmp_path / "test.db"
    with get_connection(db_path) as conn:
        result = run_scraper_isolated(ExplodingScraper(), conn)
        assert result == []

        row = conn.execute(
            "SELECT consecutive_failures FROM scraper_health WHERE store = ?",
            ("ExplodingStore",),
        ).fetchone()
        assert row["consecutive_failures"] == 1
