"""
tests/test_retry.py
Tests for the retry/backoff policy in src/providers/retry.py.
"""

from __future__ import annotations

import httpx
import pytest

from src.providers.retry import (
    AuthError,
    ClientError,
    QuotaExhausted,
    with_retry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, headers: dict | None = None, text: str = "") -> httpx.Response:
    """Build a minimal httpx.Response for testing."""
    request = httpx.Request("GET", "https://example.com")
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        text=text,
        request=request,
    )


class _Sequence:
    """Callable that returns responses from a list, then repeats the last one."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = responses
        self._idx = 0

    def __call__(self) -> httpx.Response:
        item = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------------------
# Test: 429 with Retry-After header
# ---------------------------------------------------------------------------


def test_429_with_retry_after_uses_header_value(monkeypatch):
    """429 + Retry-After:3 → callback called with 3.0, then succeeds on next call."""
    sleeps: list[float] = []
    messages: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    seq = _Sequence(
        [
            _make_response(429, headers={"Retry-After": "3"}),
            _make_response(200),
        ]
    )

    result = with_retry(seq, on_rate_limit_message=lambda s: messages.append(s))

    assert result.status_code == 200
    assert messages == [3.0], "on_rate_limit_message should be called with 3.0"
    assert sleeps == [3.0], "should sleep exactly 3 seconds"


# ---------------------------------------------------------------------------
# Test: 401 raises AuthError immediately
# ---------------------------------------------------------------------------


def test_401_raises_auth_error_immediately(monkeypatch):
    """401 → AuthError raised without any sleep."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    seq = _Sequence([_make_response(401)])

    with pytest.raises(AuthError):
        with_retry(seq)

    assert sleeps == [], "no sleep should occur for 401"


def test_403_raises_auth_error_immediately(monkeypatch):
    """403 → AuthError raised without any sleep."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(AuthError):
        with_retry(_Sequence([_make_response(403)]))

    assert sleeps == []


# ---------------------------------------------------------------------------
# Test: 503 three times → retry with correct backoff 1s/2s/4s
# ---------------------------------------------------------------------------


def test_503_retries_three_times_with_correct_backoff(monkeypatch):
    """503 three times then 200 → 3 sleeps at 1, 2, 4 seconds."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    seq = _Sequence(
        [
            _make_response(503),
            _make_response(503),
            _make_response(503),
            _make_response(200),
        ]
    )

    result = with_retry(seq)

    assert result.status_code == 200
    assert sleeps == [1.0, 2.0, 4.0]


def test_503_exhausts_retries_raises(monkeypatch):
    """503 four times → raises httpx.HTTPStatusError after 3 retries."""
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: None)

    seq = _Sequence([_make_response(503)] * 4)

    with pytest.raises(httpx.HTTPStatusError):
        with_retry(seq)


# ---------------------------------------------------------------------------
# Test: Timeout then success
# ---------------------------------------------------------------------------


def test_timeout_retries_once_then_succeeds(monkeypatch):
    """Timeout → 1 sleep of 5s → success."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    request = httpx.Request("GET", "https://example.com")
    timeout_exc = httpx.TimeoutException("timeout", request=request)

    seq = _Sequence([timeout_exc, _make_response(200)])

    result = with_retry(seq)

    assert result.status_code == 200
    assert sleeps == [5.0]


def test_timeout_exhausts_retry_raises(monkeypatch):
    """Two timeouts → raises TimeoutException."""
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: None)

    request = httpx.Request("GET", "https://example.com")
    timeout_exc = httpx.TimeoutException("timeout", request=request)

    seq = _Sequence([timeout_exc, timeout_exc])

    with pytest.raises(httpx.TimeoutException):
        with_retry(seq)


# ---------------------------------------------------------------------------
# Test: 429 without Retry-After → default backoff
# ---------------------------------------------------------------------------


def test_429_without_retry_after_uses_default_backoff(monkeypatch):
    """429 without Retry-After header → default backoff 2s."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    seq = _Sequence(
        [
            _make_response(429),  # no Retry-After
            _make_response(200),
        ]
    )

    with_retry(seq)

    assert sleeps == [2.0], "default 429 backoff should be 2s"


# ---------------------------------------------------------------------------
# Test: other 4xx raises ClientError
# ---------------------------------------------------------------------------


def test_other_4xx_raises_client_error(monkeypatch):
    """404 → ClientError raised immediately (no retry)."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(ClientError) as exc_info:
        with_retry(_Sequence([_make_response(404, text="not found")]))

    assert exc_info.value.status_code == 404
    assert sleeps == []


# ---------------------------------------------------------------------------
# Test: QuotaExhausted exception attributes
# ---------------------------------------------------------------------------


def test_quota_exhausted_attributes():
    exc = QuotaExhausted("serper", used=100, limit_val=100)
    assert exc.provider == "serper"
    assert exc.used == 100
    assert exc.limit_val == 100
    assert "serper" in str(exc)
