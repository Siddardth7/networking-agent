"""
tests/test_apify.py
Unit tests for ApifyProvider (primary LinkedIn discovery).
Hermetic: httpx.MockTransport + a temp-SQLite QuotaManager. No real API calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from src.core.migrations import run_migrations
from src.providers.apify import ApifyProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted

# Real harvestapi shapes (trimmed). Full mode: singular currentPosition + headline
# + about + clean URL. Short mode: plural currentPositions array + summary, no
# headline, opaque URN URL.
APIFY_FULL = [
    {
        "firstName": "André",
        "lastName": "Tavares Ferreira",
        "headline": "Founder @AFERIA",
        "linkedinUrl": "https://www.linkedin.com/in/andre-tavares-ferreira",
        "about": "Business transformation in manufacturing.",
        "location": {"linkedinText": "Solingen, Germany", "countryCode": "DE"},
        "currentPosition": {"companyName": "AFERIA", "position": "Founder"},
    },
]

APIFY_SHORT = [
    {
        "firstName": "Nick",
        "lastName": "van Genugten",
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAAIJZ1U",
        "summary": "Supply chain visibility.",
        "location": {"linkedinText": "Veghel, Netherlands"},
        "currentPositions": [{"companyName": "Provenant", "title": "Founder"}],
    },
]

APIFY_FIVE = [
    {"firstName": f"P{i}", "lastName": "X", "linkedinUrl": f"https://lnkd.in/{i}"}
    for i in range(5)
]


@pytest.fixture()
def qm(tmp_path: Path) -> QuotaManager:
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    conn.commit()
    conn.close()
    return QuotaManager(db_path=str(db_path))


def _client(payload, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_full_mode_parses_nested_fields(qm: QuotaManager) -> None:
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=_client(APIFY_FULL))
    [c] = provider.search_linkedin_profiles(company="AFERIA", role_keywords=["Founder"], limit=10)
    assert c.full_name == "André Tavares Ferreira"
    assert c.title == "Founder @AFERIA"  # headline wins
    assert c.linkedin_url == "https://www.linkedin.com/in/andre-tavares-ferreira"
    assert c.snippet == "Business transformation in manufacturing."
    assert c.location == "Solingen, Germany"  # lifted from location.linkedinText
    assert c.company_slug == "aferia"


def test_short_mode_uses_position_array_and_summary(qm: QuotaManager) -> None:
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=_client(APIFY_SHORT))
    [c] = provider.search_linkedin_profiles(company="Provenant", role_keywords=[], limit=10)
    assert c.full_name == "Nick van Genugten"
    assert c.title == "Founder"  # from currentPositions[0].title (no headline)
    assert c.snippet == "Supply chain visibility."
    assert c.location == "Veghel, Netherlands"


def test_limit_respected(qm: QuotaManager) -> None:
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=_client(APIFY_FIVE))
    results = provider.search_linkedin_profiles(company="ACME", role_keywords=[], limit=2)
    assert len(results) == 2


def test_quota_exhaustion_raises_before_http(qm: QuotaManager) -> None:
    # Drain the monthly cap, then the next search must raise without scraping.
    for _ in range(40):  # _DEFAULT_LIMITS["apify"]
        qm.increment("apify")
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=_client(APIFY_FULL))
    with pytest.raises(QuotaExhausted):
        provider.search_linkedin_profiles(company="ACME", role_keywords=[], limit=5)


def test_non_list_payload_returns_empty(qm: QuotaManager) -> None:
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=_client({"error": "x"}))
    assert provider.search_linkedin_profiles(company="ACME", role_keywords=[], limit=5) == []


def test_search_query_broadens_across_keywords(qm: QuotaManager) -> None:
    """FINDER_AUDIT D4: searchQuery uses the top keywords (OR-joined), not just
    the first, so ranking isn't biased to a single role."""
    import json as _json

    captured: dict = {}

    def handler(request):
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json=APIFY_FULL, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=client)
    provider.search_linkedin_profiles(
        company="Joby",
        role_keywords=["quality engineer", "stress engineer", "composites engineer"],
        limit=5,
    )
    sq = captured["body"]["searchQuery"]
    assert "quality engineer" in sq
    assert "stress engineer" in sq  # not just the first keyword
    assert " OR " in sq
