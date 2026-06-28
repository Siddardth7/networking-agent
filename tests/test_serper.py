"""
tests/test_serper.py
Unit tests for SerperProvider (Step 3.4).

All tests use httpx.MockTransport to avoid real API calls.
QuotaManager is wired to a hermetic temporary SQLite database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from src.core.migrations import run_migrations
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted
from src.providers.serper import SerperProvider

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

SERPER_RESPONSE = {
    "organic": [
        {
            "title": "Jane Doe - Senior Quality Engineer at Boeing | LinkedIn",
            "link": "https://www.linkedin.com/in/janedoe",
            "snippet": "...",
        },
        {
            "title": "Bob Smith - MRB Engineer at Lockheed Martin | LinkedIn",
            "link": "https://www.linkedin.com/in/bobsmith",
        },
    ]
}

# A response with 5 organic results for the "limit respected" test
SERPER_RESPONSE_5 = {
    "organic": [
        {
            "title": f"Person {i} - Engineer at ACME | LinkedIn",
            "link": f"https://www.linkedin.com/in/person{i}",
        }
        for i in range(1, 6)
    ]
}


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


def _make_client(response_json: dict, status_code: int = 200) -> httpx.Client:
    """Return an httpx.Client backed by a MockTransport that returns *response_json*."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_json, request=request)

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def _make_sequence_client(responses: list[tuple[int, dict | None]]) -> httpx.Client:
    """Return a client whose handler returns responses in sequence.

    Each element in *responses* is a ``(status_code, json_body)`` tuple.
    Once the list is exhausted, the last response is repeated.
    """
    state = {"idx": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(state["idx"], len(responses) - 1)
        state["idx"] += 1
        status, body = responses[idx]
        return httpx.Response(
            status,
            json=body if body is not None else {},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


# ---------------------------------------------------------------------------
# Test 1 — Successful parse
# ---------------------------------------------------------------------------


def test_successful_parse(qm: QuotaManager) -> None:
    """200 response → 2 ContactCandidates with correct fields."""
    client = _make_client(SERPER_RESPONSE)
    provider = SerperProvider(api_key="test-key", quota_manager=qm, http_client=client)

    results = provider.search_linkedin_profiles(
        company="Lockheed Martin",
        role_keywords=["quality engineer", "supplier quality", "MRB"],
        limit=10,
    )

    assert len(results) == 2

    # First result: Jane Doe
    assert results[0].full_name == "Jane Doe"
    assert results[0].title == "Senior Quality Engineer"
    assert results[0].linkedin_url == "https://www.linkedin.com/in/janedoe"
    assert results[0].company_slug == "lockheed-martin"

    # Second result: Bob Smith
    assert results[1].full_name == "Bob Smith"
    assert results[1].title == "MRB Engineer"
    assert results[1].linkedin_url == "https://www.linkedin.com/in/bobsmith"
    assert results[1].company_slug == "lockheed-martin"


# ---------------------------------------------------------------------------
# Test 2 — Quota increment
# ---------------------------------------------------------------------------


def test_quota_increment(qm: QuotaManager) -> None:
    """Each Serper network call decrements remaining quota by 1.

    Serper free-tier caps ``num`` at 10, so the provider pages internally
    until it accumulates *limit* candidates OR a page returns no new URLs.
    A 2-result mock with limit=2 fits in one page → exactly one call.
    """
    qm.can_query("serper")
    before = qm.remaining("serper")

    client = _make_client(SERPER_RESPONSE)
    provider = SerperProvider(api_key="test-key", quota_manager=qm, http_client=client)

    provider.search_linkedin_profiles(
        company="Boeing",
        role_keywords=["quality"],
        limit=2,
    )

    after = qm.remaining("serper")
    assert after == before - 1


# ---------------------------------------------------------------------------
# Test 3 — 429 retry (monkeypatched sleep)
# ---------------------------------------------------------------------------


def test_429_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 twice then 200 → retry happens (sleep is called); final result is valid."""
    sleeps: list[float] = []
    monkeypatch.setattr("src.providers.retry.time.sleep", lambda s: sleeps.append(s))

    # Two 429s then a successful 200
    client = _make_sequence_client(
        [
            (429, None),
            (429, None),
            (200, SERPER_RESPONSE),
        ]
    )

    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles(
        company="Boeing",
        role_keywords=["quality engineer"],
        limit=10,
    )

    # Retry should have slept twice
    assert len(sleeps) == 2, f"Expected 2 sleeps for 2 retries, got {sleeps}"

    # Final result should be parsed normally
    assert len(results) == 2
    assert results[0].full_name == "Jane Doe"


# ---------------------------------------------------------------------------
# Test 4 — Limit respected
# ---------------------------------------------------------------------------


def test_limit_respected() -> None:
    """5 organic results in response but limit=2 → only 2 ContactCandidates returned."""
    client = _make_client(SERPER_RESPONSE_5)
    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles(
        company="ACME",
        role_keywords=["engineer"],
        limit=2,
    )

    assert len(results) == 2


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_missing_title_field_skipped() -> None:
    """Organic result missing 'title' key is skipped gracefully."""
    response = {
        "organic": [
            {"link": "https://www.linkedin.com/in/nametitle"},  # no title
            {
                "title": "Alice Example - Engineer at ACME | LinkedIn",
                "link": "https://www.linkedin.com/in/aliceexample",
            },
        ]
    }
    client = _make_client(response)
    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles("ACME", ["engineer"], limit=5)

    assert len(results) == 1
    assert results[0].full_name == "Alice Example"


def test_missing_link_field_skipped() -> None:
    """Organic result missing 'link' key is skipped gracefully."""
    response = {
        "organic": [
            {"title": "No Link Person - Engineer at ACME | LinkedIn"},  # no link
            {
                "title": "Bob Builder - Architect at ACME | LinkedIn",
                "link": "https://www.linkedin.com/in/bobbuilder",
            },
        ]
    }
    client = _make_client(response)
    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles("ACME", ["engineer"], limit=5)

    assert len(results) == 1
    assert results[0].full_name == "Bob Builder"


def test_empty_organic_returns_empty_list() -> None:
    """Response with empty organic array → empty result list."""
    client = _make_client({"organic": []})
    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles("ACME", ["engineer"], limit=5)

    assert results == []


def test_no_quota_manager_skips_tracking() -> None:
    """When quota_manager is None, no quota tracking occurs and call succeeds."""
    client = _make_client(SERPER_RESPONSE)
    provider = SerperProvider(api_key="test-key", quota_manager=None, http_client=client)

    results = provider.search_linkedin_profiles("Boeing", ["quality"], limit=10)

    assert len(results) == 2


def test_quota_exhausted_is_reraised(qm: QuotaManager) -> None:
    """QuotaExhausted raised by quota_manager propagates to the caller."""
    # Exhaust the serper quota completely
    for _ in range(100):
        qm.increment("serper")

    client = _make_client(SERPER_RESPONSE)
    provider = SerperProvider(api_key="test-key", quota_manager=qm, http_client=client)

    with pytest.raises(QuotaExhausted):
        provider.search_linkedin_profiles("Boeing", ["quality"], limit=5)


def test_company_slug_spaces_replaced() -> None:
    """Company name with spaces → slug uses hyphens."""
    client = _make_client(SERPER_RESPONSE)
    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles(
        company="General Electric",
        role_keywords=["engineer"],
        limit=10,
    )

    for r in results:
        assert r.company_slug == "general-electric"


def test_title_pipe_separator_stripped() -> None:
    """Title with ' | ' separator (not ' at ') strips correctly."""
    response = {
        "organic": [
            {
                "title": "Carol Chen - Staff Engineer | LinkedIn",
                "link": "https://www.linkedin.com/in/carolchen",
            }
        ]
    }
    client = _make_client(response)
    provider = SerperProvider(api_key="test-key", http_client=client)

    results = provider.search_linkedin_profiles("ACME", ["engineer"], limit=5)

    assert len(results) == 1
    assert results[0].full_name == "Carol Chen"
    assert results[0].title == "Staff Engineer"


def test_location_added_to_query(qm: QuotaManager) -> None:
    """Issue #8: a location filter is added to the Google query."""
    import json as _json

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"organic": []}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SerperProvider(api_key="k", quota_manager=qm, http_client=client)
    provider.search_linkedin_profiles(
        company="Joby", role_keywords=["stress engineer"], limit=5, location="Dayton, OH"
    )
    assert "Dayton, OH" in captured["body"]["q"]
