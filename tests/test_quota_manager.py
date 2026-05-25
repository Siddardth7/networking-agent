"""
tests/test_quota_manager.py
Unit tests for QuotaManager (Step 3.3).

Each test receives a hermetic temporary SQLite database via the ``qm``
fixture.  Migrations are run before each test so the ``quota`` table exists.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.core.migrations import run_migrations
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Return a path to a temporary SQLite DB with migrations applied."""
    db_path = tmp_path / "test_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    run_migrations(conn)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def qm(tmp_db: Path) -> QuotaManager:
    """Return a QuotaManager wired to the temporary DB."""
    return QuotaManager(db_path=str(tmp_db))


# ---------------------------------------------------------------------------
# Test 1 — Increment to limit; next increment raises QuotaExhausted
# ---------------------------------------------------------------------------


def test_serper_increment_to_limit_then_exhausted(qm: QuotaManager) -> None:
    """Increment serper 100 times; can_query must be False; next call raises."""
    for _ in range(100):
        qm.increment("serper")

    assert qm.can_query("serper") is False

    with pytest.raises(QuotaExhausted) as exc_info:
        qm.increment("serper")

    err = exc_info.value
    assert err.provider == "serper"
    assert err.used == 100
    assert err.limit_val == 100


# ---------------------------------------------------------------------------
# Test 2 — Hunter default limit (25); exhausts on the 26th call
# ---------------------------------------------------------------------------


def test_hunter_default_limit_exhausts_at_25(qm: QuotaManager) -> None:
    """Hunter has a 25-query free-tier limit."""
    for _ in range(25):
        qm.increment("hunter")

    assert qm.remaining("hunter") == 0

    with pytest.raises(QuotaExhausted) as exc_info:
        qm.increment("hunter")

    err = exc_info.value
    assert err.provider == "hunter"
    assert err.used == 25
    assert err.limit_val == 25


# ---------------------------------------------------------------------------
# Test 3 — Month rollover: new month → fresh quota
# ---------------------------------------------------------------------------


def test_month_rollover_resets_quota(qm: QuotaManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """After monkeypatching month_key to a future month, can_query returns True."""
    # Exhaust serper in the current month
    for _ in range(100):
        qm.increment("serper")
    assert qm.can_query("serper") is False

    # Simulate a month rollover
    monkeypatch.setattr(qm, "month_key", lambda: "2099-01")

    # New month: a fresh row should be created and quota available
    assert qm.can_query("serper") is True
    assert qm.remaining("serper") == 100


# ---------------------------------------------------------------------------
# Test 4 — remaining() reflects increments correctly
# ---------------------------------------------------------------------------


def test_remaining_after_three_increments(qm: QuotaManager) -> None:
    """After 3 serper increments, remaining should be 97."""
    for _ in range(3):
        qm.increment("serper")

    assert qm.remaining("serper") == 97


# ---------------------------------------------------------------------------
# Test 5 — No row → can_query creates the row and returns True
# ---------------------------------------------------------------------------


def test_can_query_creates_row_when_missing(qm: QuotaManager, tmp_db: Path) -> None:
    """can_query must seed the row and return True if no row yet exists."""
    # Verify quota table is empty to start
    conn = sqlite3.connect(str(tmp_db))
    row_count = conn.execute("SELECT COUNT(*) FROM quota").fetchone()[0]
    conn.close()
    assert row_count == 0, "Expected empty quota table before first can_query"

    result = qm.can_query("serper")

    assert result is True

    # Row must now exist
    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT used, limit_val FROM quota WHERE provider = 'serper'"
    ).fetchone()
    conn.close()
    assert row is not None, "can_query should have seeded the quota row"
    assert row[0] == 0       # used = 0
    assert row[1] == 100     # limit_val = serper default


# ---------------------------------------------------------------------------
# Test 6 — get_limit reflects the stored limit
# ---------------------------------------------------------------------------


def test_get_limit_returns_configured_limit(qm: QuotaManager) -> None:
    """get_limit should return 100 for serper and 25 for hunter."""
    # Trigger row creation
    qm.can_query("serper")
    qm.can_query("hunter")

    assert qm.get_limit("serper") == 100
    assert qm.get_limit("hunter") == 25


# ---------------------------------------------------------------------------
# Test 7 — QuotaExhausted does NOT increment the counter
# ---------------------------------------------------------------------------


def test_quota_exhausted_does_not_increment_counter(qm: QuotaManager) -> None:
    """Counter must stay at 100 after a failed increment (atomic hard stop)."""
    for _ in range(100):
        qm.increment("serper")

    with pytest.raises(QuotaExhausted):
        qm.increment("serper")

    # used must still be 100, not 101
    assert qm.remaining("serper") == 0
    assert qm.get_limit("serper") == 100
