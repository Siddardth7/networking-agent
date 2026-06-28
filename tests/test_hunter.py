"""
tests/test_hunter.py
Unit tests for HunterProvider (Step 3.5).

Each HTTP interaction is handled via httpx.MockTransport so no real network
calls are made.  Quota tests use a hermetic temporary SQLite database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from src.core.migrations import run_migrations
from src.providers.hunter import HunterProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import AuthError, QuotaExhausted

# ---------------------------------------------------------------------------
# Shared Hunter API response fixtures
# ---------------------------------------------------------------------------

# Domain-search responses: the org email *pattern* (#13). One lookup per company
# infers every contact's address locally — the uncapped channel.
HUNTER_PATTERN: dict = {"data": {"pattern": "{first}.{last}", "organization": "Boeing"}}

HUNTER_PATTERN_INITIAL: dict = {"data": {"pattern": "{f}{last}"}}

HUNTER_NO_PATTERN: dict = {"data": {"pattern": None, "organization": "Example"}}


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


def test_find_email_inferred_from_pattern() -> None:
    """A '{first}.{last}' pattern → a locally-inferred, unverified address."""
    client = _make_client(HUNTER_PATTERN)
    provider = HunterProvider(api_key="test-key", http_client=client)

    result = provider.find_email("Jane Doe", "boeing.com")

    assert result.email == "jane.doe@boeing.com"
    assert result.verified is False  # inference is a best-effort guess
    assert result.confidence == 50
    assert result.source == "hunter_pattern"


# ---------------------------------------------------------------------------
# Test 2 — initial-based pattern format
# ---------------------------------------------------------------------------


def test_find_email_initial_pattern() -> None:
    """A '{f}{last}' pattern infers 'jdoe@domain'."""
    client = _make_client(HUNTER_PATTERN_INITIAL)
    provider = HunterProvider(api_key="test-key", http_client=client)

    result = provider.find_email("Jane Doe", "boeing.com")

    assert result.email == "jdoe@boeing.com"
    assert result.source == "hunter_pattern"


# ---------------------------------------------------------------------------
# Test 3 — pattern cached: one lookup serves every contact at a company
# ---------------------------------------------------------------------------


def test_pattern_cached_uncapped_across_contacts(qm: QuotaManager) -> None:
    """#13: the first contact spends one Hunter credit; the rest are free."""
    qm._ensure_row("hunter", 25)
    before = qm.remaining("hunter")

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=HUNTER_PATTERN, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = HunterProvider(api_key="test-key", quota_manager=qm, http_client=client)

    a = provider.find_email("Jane Doe", "boeing.com")
    b = provider.find_email("John Smith", "boeing.com")
    c = provider.find_email("Amy Lee", "boeing.com")

    assert a.email == "jane.doe@boeing.com"
    assert b.email == "john.smith@boeing.com"
    assert c.email == "amy.lee@boeing.com"
    assert calls["n"] == 1  # one domain-search served all three
    assert qm.remaining("hunter") == before - 1  # one credit spent


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

    client = _make_client(HUNTER_PATTERN)
    provider = HunterProvider(api_key="test-key", quota_manager=qm, http_client=client)

    provider.find_email("Jane Doe", "boeing.com")

    after = qm.remaining("hunter")
    assert after == before - 1, f"Expected remaining to drop by 1 (was {before}, now {after})"


# ---------------------------------------------------------------------------
# Bonus: QuotaExhausted propagates when quota is at zero
# ---------------------------------------------------------------------------


def test_quota_exhausted_raises(qm: QuotaManager) -> None:
    """When the hunter quota is fully consumed, find_email() raises QuotaExhausted."""
    # Exhaust all 25 free-tier calls.
    for _ in range(25):
        qm.increment("hunter", 1)

    client = _make_client(HUNTER_PATTERN)
    provider = HunterProvider(api_key="test-key", quota_manager=qm, http_client=client)

    with pytest.raises(QuotaExhausted) as exc_info:
        provider.find_email("Jane Doe", "boeing.com")

    assert exc_info.value.provider == "hunter"


# ---------------------------------------------------------------------------
# Bonus: null email in data → email=None, verified=False
# ---------------------------------------------------------------------------


def test_find_email_null_result() -> None:
    """Hunter returning email=null → EmailResult with email=None, verified=False."""
    client = _make_client(HUNTER_NO_PATTERN)
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
    client = _make_client(HUNTER_PATTERN)
    provider = HunterProvider(api_key=_LEAKY_KEY, http_client=client)

    result = provider.find_email("Jane Doe", "boeing.com")

    assert result.email == "jane.doe@boeing.com"
    assert result.source == "hunter_pattern"


def test_scrub_api_key_in_exc_unit() -> None:
    """Direct unit test for the scrubber so call-site refactors can't silently
    break it."""
    from src.providers.hunter import scrub_api_key_in_exc

    leaky_url = f"https://api.hunter.io/v2/email-finder?api_key={_LEAKY_KEY}&domain=boeing.com"
    request = httpx.Request("GET", leaky_url)
    response = httpx.Response(500, request=request, content=b"server error")
    original = httpx.HTTPStatusError("500 Server Error", request=request, response=response)

    scrubbed = scrub_api_key_in_exc(original, _LEAKY_KEY)

    # Key value is gone from every public surface.
    assert _LEAKY_KEY not in str(scrubbed)
    assert _LEAKY_KEY not in repr(scrubbed)
    assert _LEAKY_KEY not in str(scrubbed.request.url)
    # And the redaction marker is present in the URL.
    url = str(scrubbed.request.url)
    assert "api_key=***" in url or "api_key=%2A%2A%2A" in url


def test_scrubbed_hunter_call_context_manager() -> None:
    """The shared context manager scrubs at any call site — exercises the CLI
    path's reuse of the helper."""
    from src.providers.hunter import scrubbed_hunter_call

    leaky_url = f"https://api.hunter.io/v2/account?api_key={_LEAKY_KEY}"
    request = httpx.Request("GET", leaky_url)
    response = httpx.Response(500, request=request, content=b"oops")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        with scrubbed_hunter_call(_LEAKY_KEY):
            raise httpx.HTTPStatusError("500 Server Error", request=request, response=response)

    exc = exc_info.value
    rendered = repr(exc) + " " + str(exc) + " " + str(exc.request.url)
    assert _LEAKY_KEY not in rendered
    assert exc.__cause__ is None  # `from None` broke the chain


# ---------------------------------------------------------------------------
# #13 — apply_email_pattern unit tests + find_email unfilled/close paths
# ---------------------------------------------------------------------------


def test_apply_email_pattern_formats() -> None:
    from src.providers.hunter import apply_email_pattern

    assert apply_email_pattern("{first}.{last}", "Jane", "Doe", "b.com") == "jane.doe@b.com"
    assert apply_email_pattern("{f}{last}", "Jane", "Doe", "b.com") == "jdoe@b.com"
    assert apply_email_pattern("{first}", "Jane", "Doe", "b.com") == "jane@b.com"
    got = apply_email_pattern("{first_name}_{last_name}", "Jane", "Doe", "b.com")
    assert got == "jane_doe@b.com"


def test_apply_email_pattern_returns_none() -> None:
    from src.providers.hunter import apply_email_pattern

    assert apply_email_pattern("", "Jane", "Doe", "b.com") is None  # no pattern
    assert apply_email_pattern("{first}.{last}", "Jane", "Doe", "") is None  # no domain
    assert apply_email_pattern("{unknown}", "Jane", "Doe", "b.com") is None  # unfilled token
    assert apply_email_pattern("{last}", "Cher", "", "b.com") is None  # empty local part


def test_find_email_pattern_unfillable_falls_through() -> None:
    # Pattern present but the name can't fill it → empty result, source "hunter",
    # so the chain falls through to Apollo.
    client = _make_client({"data": {"pattern": "{last}"}})
    provider = HunterProvider(api_key="test-key", http_client=client)
    result = provider.find_email("Cher", "b.com")  # single name → no last
    assert result.email is None
    assert result.source == "hunter"


def test_close_releases_client() -> None:
    client = _make_client(HUNTER_PATTERN)
    provider = HunterProvider(api_key="k", http_client=client)
    provider.close()
    assert client.is_closed


# ---------------------------------------------------------------------------
# #13 (was #22-deferred) — scrubber defensive branches
# ---------------------------------------------------------------------------


def test_scrub_non_http_exception() -> None:
    # A non-httpx exception: args are scrubbed (str args), non-str args passed
    # through unchanged.
    from src.providers.hunter import scrub_api_key_in_exc

    exc = ValueError(f"failed api_key={_LEAKY_KEY}", 42)
    scrubbed = scrub_api_key_in_exc(exc, _LEAKY_KEY)
    assert isinstance(scrubbed, ValueError)
    assert _LEAKY_KEY not in str(scrubbed.args[0])
    assert scrubbed.args[1] == 42  # non-str arg untouched


def test_scrub_request_error_without_bound_request() -> None:
    from src.providers.hunter import scrub_api_key_in_exc

    exc = httpx.ConnectError(f"connect failed api_key={_LEAKY_KEY}")  # no request bound
    scrubbed = scrub_api_key_in_exc(exc, _LEAKY_KEY)
    assert isinstance(scrubbed, httpx.ConnectError)
    assert _LEAKY_KEY not in str(scrubbed)


def test_scrub_http_error_when_request_rebuild_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # If httpx.Request() raises during rebuild, the scrubber degrades gracefully
    # (except: pass) and still returns a scrubbed HTTPStatusError.
    import src.providers.hunter as hunter_mod

    leaky = f"https://api.hunter.io/v2/domain-search?api_key={_LEAKY_KEY}"
    req = httpx.Request("GET", leaky)
    resp = httpx.Response(500, request=req, content=b"x")
    exc = httpx.HTTPStatusError("500", request=req, response=resp)

    def _boom(*a, **k):
        raise RuntimeError("no request for you")

    monkeypatch.setattr(hunter_mod.httpx, "Request", _boom)
    scrubbed = scrub_via(hunter_mod, exc)
    assert _LEAKY_KEY not in str(scrubbed)


def test_scrub_http_error_when_response_rebuild_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.providers.hunter as hunter_mod

    leaky = f"https://api.hunter.io/v2/domain-search?api_key={_LEAKY_KEY}"
    req = httpx.Request("GET", leaky)
    resp = httpx.Response(500, request=req, content=b"x")
    exc = httpx.HTTPStatusError("500", request=req, response=resp)

    def _boom(*a, **k):
        raise RuntimeError("no response")

    monkeypatch.setattr(hunter_mod.httpx, "Response", _boom)
    scrubbed = scrub_via(hunter_mod, exc)
    assert _LEAKY_KEY not in str(scrubbed)


def test_scrub_request_error_when_request_rebuild_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.providers.hunter as hunter_mod

    leaky = f"https://api.hunter.io/v2/domain-search?api_key={_LEAKY_KEY}"
    req = httpx.Request("GET", leaky)
    exc = httpx.ConnectError(f"failed {leaky}", request=req)

    def _boom(*a, **k):
        raise RuntimeError("no request")

    monkeypatch.setattr(hunter_mod.httpx, "Request", _boom)
    scrubbed = scrub_via(hunter_mod, exc)
    assert _LEAKY_KEY not in str(scrubbed)


def scrub_via(hunter_mod, exc):
    return hunter_mod.scrub_api_key_in_exc(exc, _LEAKY_KEY)


def test_scrub_generic_exc_reconstruct_fails_returns_original() -> None:
    # A non-http exception whose constructor rejects the scrubbed args → the
    # scrubber gives back the original rather than crashing.
    from src.providers.hunter import scrub_api_key_in_exc

    class _NoArgError(Exception):
        def __init__(self) -> None:  # rejects type(exc)(*new_args)
            super().__init__("fixed message")

    exc = _NoArgError()
    assert scrub_api_key_in_exc(exc, _LEAKY_KEY) is exc


def test_scrub_request_error_reconstruct_fails_returns_original() -> None:
    # A RequestError subclass that won't accept (msg, request=) or (msg) → both
    # construction attempts raise and the original is returned.
    from src.providers.hunter import scrub_api_key_in_exc

    class _NoArgReqError(httpx.RequestError):
        def __init__(self) -> None:
            super().__init__("boom")

    exc = _NoArgReqError()
    assert scrub_api_key_in_exc(exc, _LEAKY_KEY) is exc
