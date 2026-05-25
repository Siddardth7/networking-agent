"""
tests/test_hunter.py
Unit tests for HunterProvider (Step 3.5).

Each HTTP interaction is handled via httpx.MockTransport so no real network
calls are made.  Quota tests use a hermetic temporary SQLite database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

import httpx
import pytest

from src.core.migrations import run_migrations
from src.providers.hunter import HunterProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import AuthError, QuotaExhausted

# ---------------------------------------------------------------------------
# Shared Hunter API response fixtures
# ---------------------------------------------------------------------------

HUNTER_VERIFIED: dict = {
    "data": {
        "email": "jane.doe@boeing.com",
        "score": 94,
        "verification": {"status": "valid"},
    }
}

HUNTER_UNVERIFIED: dict = {
    "data": {
        "email": "john@gmail.com",
        "score": 30,
        "verification": {"status": "webmail"},
    }
}

HUNTER_ACCEPT_ALL: dict = {
    "data": {
        "email": "bob@company.com",
        "score": 60,
        "verification": {"status": "accept_all"},
    }
}

HUNTER_NO_EMAIL: dict = {
    "data": {
        "email": None,
        "score": 0,
        "verification": {"status": "unknown"},
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(response_body: dict, status_code: int = 200) -> httpx.Client:
    """Return an httpx.Client whose transport always returns *response_body*."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_body, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_status_client(status_code: int) -> httpx.Client:
    """Return an httpx.Client that always returns the given status code with empty body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={}, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Database fixture — hermetic per-test SQLite DB with migrations applied
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Path to a fresh temporary SQLite DB with all migrations applied."""
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
    """QuotaManager wired to the hermetic temporary DB."""
    return QuotaManager(db_path=str(tmp_db))


# ---------------------------------------------------------------------------
# Test 1 — Verified email response (status="valid")
# ---------------------------------------------------------------------------


def test_find_email_verified() -> None:
    """A 'valid' verification status → verified=True with correct fields."""
    client = _make_client(HUNTER_VERIFIED)
    provider = HunterProvider(api_key="test-key", http_client=client)

    result = provider.find_email("Jane Doe", "boeing.com")

    assert result.email == "jane.doe@boeing.com"
    assert result.verified is True
    assert result.confidence == 94
    assert result.source == "hunter"


# ---------------------------------------------------------------------------
# Test 2 — Unverified response (status="webmail")
# ---------------------------------------------------------------------------


def test_find_email_unverified() -> None:
    """A 'webmail' verification status → verified=False; email is still returned."""
    client = _make_client(HUNTER_UNVERIFIED)
    provider = HunterProvider(api_key="test-key", http_client=client)

    result = provider.find_email("John Smith", "gmail.com")

    assert result.verified is False
    assert result.email == "john@gmail.com"
    assert result.source == "hunter"


# ---------------------------------------------------------------------------
# Test 3 — accept_all status → verified=True
# ---------------------------------------------------------------------------


def test_find_email_accept_all() -> None:
    """An 'accept_all' catch-all domain → verified=True."""
    client = _make_client(HUNTER_ACCEPT_ALL)
    provider = HunterProvider(api_key="test-key", http_client=client)

    result = provider.find_email("Bob Builder", "company.com")

    assert result.verified is True
    assert result.email == "bob@company.com"
    assert result.confidence == 60
    assert result.source == "hunter"


# ---------------------------------------------------------------------------
# Test 4 — 401 raises AuthError without retrying (no sleeps)
# ---------------------------------------------------------------------------


def test_find_email_401_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 response must raise AuthError immediately — no sleep() called."""
    sleep_calls: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    client = _make_status_client(401)
    provider = HunterProvider(api_key="bad-key", http_client=client)

    with pytest.raises(AuthError):
        provider.find_email("Jane Doe", "boeing.com")

    assert sleep_calls == [], "time.sleep() must not be called on a 401"


# ---------------------------------------------------------------------------
# Test 5 — Quota increment: remaining decreases by 1 after successful call
# ---------------------------------------------------------------------------


def test_quota_incremented_after_successful_call(qm: QuotaManager) -> None:
    """After a successful find_email(), quota remaining for 'hunter' drops by 1."""
    # Seed the quota row so we can measure the starting value.
    qm._ensure_row("hunter", 25)
    before = qm.remaining("hunter")

    client = _make_client(HUNTER_VERIFIED)
    provider = HunterProvider(api_key="test-key", quota_manager=qm, http_client=client)

    provider.find_email("Jane Doe", "boeing.com")

    after = qm.remaining("hunter")
    assert after == before - 1, (
        f"Expected remaining to drop by 1 (was {before}, now {after})"
    )


# ---------------------------------------------------------------------------
# Bonus: QuotaExhausted propagates when quota is at zero
# ---------------------------------------------------------------------------


def test_quota_exhausted_raises(qm: QuotaManager) -> None:
    """When the hunter quota is fully consumed, find_email() raises QuotaExhausted."""
    # Exhaust all 25 free-tier calls.
    for _ in range(25):
        qm.increment("hunter", 1)

    client = _make_client(HUNTER_VERIFIED)
    provider = HunterProvider(api_key="test-key", quota_manager=qm, http_client=client)

    with pytest.raises(QuotaExhausted) as exc_info:
        provider.find_email("Jane Doe", "boeing.com")

    assert exc_info.value.provider == "hunter"


# ---------------------------------------------------------------------------
# Bonus: null email in data → email=None, verified=False
# ---------------------------------------------------------------------------


def test_find_email_null_result() -> None:
    """Hunter returning email=null → EmailResult with email=None, verified=False."""
    client = _make_client(HUNTER_NO_EMAIL)
    provider = HunterProvider(api_key="test-key", http_client=client)

    result = provider.find_email("Unknown Person", "example.com")

    assert result.email is None
    assert result.verified is False
    assert result.source == "hunter"


# ---------------------------------------------------------------------------
# P9 — API key scrubbing in raised exceptions
# ---------------------------------------------------------------------------


_LEAKY_KEY = "46aa5fc7deadbeef0123456789abcdef01234567"


def _make_5xx_client() -> httpx.Client:
    """Return an httpx.Client that always returns HTTP 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_timeout_client() -> httpx.Client:
    """Return an httpx.Client whose transport raises ConnectTimeout with the leaky URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_find_email_scrubs_key_on_http_error(monkeypatch) -> None:
    """After 5xx exhaustion, the raised HTTPStatusError must not contain the api_key."""
    # Speed up retry sleeps so the test runs fast
    import src.providers.retry as retry_mod
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_a, **_k: None)

    client = _make_5xx_client()
    provider = HunterProvider(api_key=_LEAKY_KEY, http_client=client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        provider.find_email("Jane Doe", "boeing.com")

    exc = exc_info.value
    rendered = repr(exc) + " " + str(exc) + " " + str(exc.request.url)
    assert _LEAKY_KEY not in rendered
    assert "api_key=" + _LEAKY_KEY not in rendered
    url_str = str(exc.request.url)
    assert "api_key=***" in url_str or "api_key=%2A%2A%2A" in url_str
    # __cause__/__context__ should be broken to prevent leak resurfacing
    assert exc.__cause__ is None


def test_find_email_scrubs_key_on_timeout(monkeypatch) -> None:
    """After timeout-retry exhaustion, raised TimeoutException must not contain the api_key."""
    import src.providers.retry as retry_mod
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_a, **_k: None)

    client = _make_timeout_client()
    provider = HunterProvider(api_key=_LEAKY_KEY, http_client=client)

    with pytest.raises(httpx.RequestError) as exc_info:
        provider.find_email("Jane Doe", "boeing.com")

    exc = exc_info.value
    rendered = repr(exc) + " " + str(exc)
    try:
        rendered += " " + str(exc.request.url)
    except (RuntimeError, AttributeError):
        pass
    assert _LEAKY_KEY not in rendered
    assert exc.__cause__ is None


def test_find_email_happy_path_no_scrubbing() -> None:
    """Regression: happy path with leaky-looking key still returns a valid result."""
    client = _make_client(HUNTER_VERIFIED)
    provider = HunterProvider(api_key=_LEAKY_KEY, http_client=client)

    result = provider.find_email("Jane Doe", "boeing.com")

    assert result.email == "jane.doe@boeing.com"
    assert result.verified is True
