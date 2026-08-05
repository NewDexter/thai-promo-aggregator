"""
Tests run entirely against local fixtures — no live network calls — so CI
is deterministic. HTTP-fetching methods on BaseScraper are monkeypatched to
return fixture content instead of hitting the network.

Fixtures reflect the REAL structure of each store's live site as verified
by direct inspection (see each scraper's module docstring for what was
found and why), not placeholder guesses.
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


def _clear_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure OCR is unconfigured so tests exercise graceful degradation
    deterministically, regardless of the local/CI environment."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import importlib

    import config as config_module

    importlib.reload(config_module)


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
# 7-Eleven: real __NEXT_DATA__ JSON structure
# ---------------------------------------------------------------------------


def test_seven_eleven_parses_structured_price_from_next_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Items with real price/normal_price in the JSON need no OCR at all."""
    scraper = SevenElevenScraper()
    html = (FIXTURES / "seven_eleven_promotion.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.7eleven.co.th/promotion": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    html_deals = [d for d in deals if d["extraction_method"] == "html"]
    assert len(html_deals) == 1
    deal = html_deals[0]
    assert deal["title"] == "ลดอย่างแรง 7 วันเท่านั้น"
    assert deal["sale_price"] == 49.0
    assert deal["original_price"] == 59.0
    assert deal["valid_until"] == "2026-08-31"
    assert deal["source_url"] == "https://www.7eleven.co.th/promotion/sale/269"
    assert deal["category"] == "Discount"


def test_seven_eleven_ocr_fallback_for_priceless_items_with_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Items with no price but a banner_image go through OCR; without an
    API key configured this degrades to null prices, not a crash."""
    _clear_gemini_key(monkeypatch)

    scraper = SevenElevenScraper()
    html = (FIXTURES / "seven_eleven_promotion.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.7eleven.co.th/promotion": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    ocr_deals = [d for d in deals if d["extraction_method"] == "ocr"]
    assert len(ocr_deals) == 1
    assert ocr_deals[0]["title"] == "Shop and Get Cashback Mission"
    assert ocr_deals[0]["category"] == "MemberExclusive"
    assert ocr_deals[0]["sale_price"] is None
    assert ocr_deals[0]["original_price"] is None


def test_seven_eleven_missing_next_data_returns_empty_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = SevenElevenScraper()
    _patch_fetch(monkeypatch, scraper, {"https://www.7eleven.co.th/promotion": "<html><body>no data here</body></html>"})

    deals = scraper.scrape()

    assert deals == []


# ---------------------------------------------------------------------------
# CJ More: real cjmore.co.th AJAX JSON endpoint
# ---------------------------------------------------------------------------


def test_cj_more_ocr_from_leaflet_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gemini_key(monkeypatch)

    scraper = CjMoreScraper()
    tab1 = (FIXTURES / "cj_more_type1.json").read_text()
    tab2 = (FIXTURES / "cj_more_type2_empty.json").read_text()
    _patch_fetch(
        monkeypatch,
        scraper,
        {
            "https://www.cjmore.co.th/promotion/type/1": tab1,
            "https://www.cjmore.co.th/promotion/type/2": tab2,
        },
    )
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    # Tab 1 has 2 leaflet images, tab 2 has none.
    assert len(deals) == 2
    assert all(d["extraction_method"] == "ocr" for d in deals)
    assert all(d["sale_price"] is None for d in deals)  # graceful degradation, no API key
    assert deals[0]["image_url"] == "https://www.cjmore.co.th/upload/promotion/195.jpg"


def test_cj_more_handles_backend_500_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """CJ More's own tab-2 endpoint is known to sometimes 500 — this must
    not crash the scraper or the orchestrator."""
    scraper = CjMoreScraper()

    def fake_fetch(url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith("/type/1"):
            return FakeResponse((FIXTURES / "cj_more_type1.json").read_text())
        raise RuntimeError("simulated 500 from CJ More backend")

    monkeypatch.setattr(scraper, "fetch", fake_fetch)
    _patch_fetch_bytes(monkeypatch, scraper)
    _clear_gemini_key(monkeypatch)

    deals = scraper.scrape()

    assert len(deals) == 2  # tab 1 still succeeds despite tab 2 failing


def test_cj_more_non_json_response_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = CjMoreScraper()
    _patch_fetch(
        monkeypatch,
        scraper,
        {
            "https://www.cjmore.co.th/promotion/type/1": "<html>Whoops</html>",
            "https://www.cjmore.co.th/promotion/type/2": "<html>Whoops</html>",
        },
    )

    deals = scraper.scrape()

    assert deals == []


# ---------------------------------------------------------------------------
# Makro: real image-carousel + OCR
# ---------------------------------------------------------------------------


def test_makro_parses_banner_title_and_period_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gemini_key(monkeypatch)

    scraper = MakroScraper()
    html = (FIXTURES / "makro_discount.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.makro.co.th/en/discount": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    assert len(deals) == 2
    assert deals[0]["title"] == "FF Weekly17 Period : 5Aug - 11Aug'26"
    assert deals[0]["valid_until"] == "2026-08-11"
    assert deals[0]["extraction_method"] == "ocr"
    assert deals[0]["sale_price"] is None  # graceful degradation, no API key
    assert deals[1]["valid_until"] == "2026-08-18"


# ---------------------------------------------------------------------------
# Tops & Gourmet Market (pure HTML, no OCR) — unaffected by the real-site
# Cloudflare block, which is an operational/runtime issue, not a parsing bug.
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
# Lotus's: real homepage marketingBanner blocks + CSR-only graceful degradation
# ---------------------------------------------------------------------------


def test_lotuss_extracts_marketing_banners(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gemini_key(monkeypatch)

    scraper = LotussScraper()
    html = (FIXTURES / "lotuss_homepage.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.lotuss.com/en": html})
    _patch_fetch_bytes(monkeypatch, scraper)

    deals = scraper.scrape()

    assert len(deals) == 1
    deal = deals[0]
    assert deal["title"] == "Big Bang 21may 12aug"  # slug-derived fallback (no OCR key)
    assert deal["source_url"] == "https://www.lotuss.com/th/promotion/big-bang-21may-12aug"
    assert deal["valid_until"] == "2026-08-12"
    assert deal["extraction_method"] == "ocr"
    assert deal["sale_price"] is None  # graceful degradation, no API key


def test_lotuss_csr_only_page_degrades_to_empty_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirms the documented reality: many Lotus's routes are CSR-only
    and report 404 inside their own JSON payload. The scraper must return
    an empty list quietly, not raise."""
    scraper = LotussScraper()
    html = (FIXTURES / "lotuss_csr_404.html").read_text()
    _patch_fetch(monkeypatch, scraper, {"https://www.lotuss.com/en": html})

    deals = scraper.scrape()

    assert deals == []


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
