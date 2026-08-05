"""
SQLite persistence layer.

The database file lives under `data/deals.db` and is committed back to the
git repo by the GitHub Actions workflow after every successful run — this is
the project's ONLY durable state store (no external database, no paid
hosting). Two tables:

  * deals           - every distinct promotion ever seen, keyed by hash_id,
                       with first_seen / last_seen timestamps for dedup.
  * scraper_health   - per-store consecutive-failure counter, used to decide
                        when to fire the "scraper has been failing" admin alert.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    hash_id         TEXT PRIMARY KEY,
    store           TEXT NOT NULL,
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    original_price  REAL,
    sale_price      REAL,
    valid_until     TEXT,
    image_url       TEXT,
    source_url      TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    alert_sent      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scraper_health (
    store               TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_run_at         TEXT,
    last_error          TEXT,
    failure_alert_sent  INTEGER NOT NULL DEFAULT 0
);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with schema ensured, committing on success."""
    from config import DATABASE_PATH

    path = db_path or DATABASE_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_new_deal(conn: sqlite3.Connection, hash_id: str) -> bool:
    """True if this hash_id has never been recorded before."""
    row = conn.execute("SELECT 1 FROM deals WHERE hash_id = ?", (hash_id,)).fetchone()
    return row is None


def upsert_deal(conn: sqlite3.Connection, deal: dict) -> bool:
    """
    Insert a new deal or refresh last_seen on an existing one.

    Returns True if the deal was newly inserted (i.e. a Telegram alert should
    be considered), False if it already existed (dedup — log only).
    """
    now = _utcnow_iso()
    existing = conn.execute(
        "SELECT hash_id FROM deals WHERE hash_id = ?", (deal["hash_id"],)
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO deals (
                hash_id, store, title, category, original_price, sale_price,
                valid_until, image_url, source_url, extraction_method,
                first_seen, last_seen, alert_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                deal["hash_id"],
                deal["store"],
                deal["title"],
                deal["category"],
                deal.get("original_price"),
                deal.get("sale_price"),
                deal.get("valid_until"),
                deal.get("image_url"),
                deal["source_url"],
                deal["extraction_method"],
                now,
                now,
            ),
        )
        return True

    conn.execute("UPDATE deals SET last_seen = ? WHERE hash_id = ?", (now, deal["hash_id"]))
    return False


def mark_alert_sent(conn: sqlite3.Connection, hash_id: str) -> None:
    conn.execute("UPDATE deals SET alert_sent = 1 WHERE hash_id = ?", (hash_id,))


def record_scraper_success(conn: sqlite3.Connection, store: str) -> None:
    """Reset a store's consecutive-failure counter after a clean run."""
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO scraper_health (store, consecutive_failures, last_run_at, last_error, failure_alert_sent)
        VALUES (?, 0, ?, NULL, 0)
        ON CONFLICT(store) DO UPDATE SET
            consecutive_failures = 0,
            last_run_at = excluded.last_run_at,
            last_error = NULL,
            failure_alert_sent = 0
        """,
        (store, now),
    )


def record_scraper_failure(conn: sqlite3.Connection, store: str, error: str) -> int:
    """
    Increment a store's consecutive-failure counter and return the new count.
    """
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO scraper_health (store, consecutive_failures, last_run_at, last_error, failure_alert_sent)
        VALUES (?, 1, ?, ?, 0)
        ON CONFLICT(store) DO UPDATE SET
            consecutive_failures = consecutive_failures + 1,
            last_run_at = excluded.last_run_at,
            last_error = excluded.last_error
        """,
        (store, now, error),
    )
    row = conn.execute(
        "SELECT consecutive_failures FROM scraper_health WHERE store = ?", (store,)
    ).fetchone()
    return int(row["consecutive_failures"]) if row else 0


def should_send_failure_alert(conn: sqlite3.Connection, store: str, threshold: int) -> bool:
    """
    True exactly once when a store crosses the failure threshold — avoids
    re-alerting on every subsequent failed run.
    """
    row = conn.execute(
        "SELECT consecutive_failures, failure_alert_sent FROM scraper_health WHERE store = ?",
        (store,),
    ).fetchone()
    if row is None:
        return False
    return row["consecutive_failures"] >= threshold and not row["failure_alert_sent"]


def mark_failure_alert_sent(conn: sqlite3.Connection, store: str) -> None:
    conn.execute("UPDATE scraper_health SET failure_alert_sent = 1 WHERE store = ?", (store,))
