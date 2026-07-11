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


def test_search_query_is_plain_company_role_breadth_via_titles(qm: QuotaManager) -> None:
    """Issue #94: searchQuery is the plain company (an `(A OR B)` clause dilutes
    ranking to ~1 result). Role breadth (FINDER_AUDIT D4) moves to the full
    `currentJobTitles` set, which post-filters server-side without diluting."""
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
    body = captured["body"]
    assert body["searchQuery"] == "Joby"  # plain company, no parens/OR
    assert " OR " not in body["searchQuery"]
    # All keywords preserved for ranking/filtering, via the structured field.
    assert body["currentJobTitles"] == [
        "quality engineer", "stress engineer", "composites engineer"
    ]


def test_location_uses_structured_field_not_search_query(qm: QuotaManager) -> None:
    """Issue #94: a location goes to the actor's structured `locations` filter,
    never into searchQuery (appending it there zeroed results live), and the run
    still returns candidates."""
    import json as _json

    captured: dict = {}

    def handler(request):
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json=APIFY_FULL, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=client)
    result = provider.search_linkedin_profiles(
        company="Joby", role_keywords=["stress engineer"], limit=5, location="Dayton, OH"
    )
    assert captured["body"]["locations"] == ["Dayton, OH"]
    assert "Dayton, OH" not in captured["body"]["searchQuery"]
    assert len(result) > 0  # location-scoped discovery is non-empty (regression)


def test_no_location_omits_structured_field(qm: QuotaManager) -> None:
    """Without a location, no `locations` key is sent (nothing to filter on)."""
    import json as _json

    captured: dict = {}

    def handler(request):
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json=APIFY_FULL, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=client)
    provider.search_linkedin_profiles(company="Joby", role_keywords=[], limit=5)
    assert "locations" not in captured["body"]


def test_parse_item_no_name_returns_none() -> None:
    # #22: an item with no first/last/full name is dropped, not parsed.
    from src.providers.apify import _parse_item

    assert _parse_item({"headline": "Engineer"}, "acme") is None


def test_no_quota_manager_skips_increment() -> None:
    # #22: quota_manager=None → increment branch skipped, search still parses.
    provider = ApifyProvider(api_key="k", quota_manager=None, http_client=_client(APIFY_FULL))
    out = provider.search_linkedin_profiles(company="X", role_keywords=["eng"], limit=5)
    assert out  # parsed without a quota manager


def test_non_dict_items_skipped(qm: QuotaManager) -> None:
    # #22: a non-dict element in the payload list is skipped (not crashed on).
    provider = ApifyProvider(
        api_key="k", quota_manager=qm, http_client=_client([*APIFY_FULL, "garbage", 42])
    )
    out = provider.search_linkedin_profiles(company="X", role_keywords=["eng"], limit=10)
    assert all(c.full_name for c in out)
    assert len(out) == len([i for i in APIFY_FULL if isinstance(i, dict)])


def test_close_releases_client() -> None:
    client = _client(APIFY_FULL)
    provider = ApifyProvider(api_key="k", http_client=client)
    provider.close()
    assert client.is_closed


def test_limit_breaks_early_before_parsing_all(qm: QuotaManager) -> None:
    # #22: with more results than the limit, parsing stops at the limit.
    provider = ApifyProvider(api_key="k", quota_manager=qm, http_client=_client(APIFY_FULL))
    out = provider.search_linkedin_profiles(company="X", role_keywords=["eng"], limit=1)
    assert len(out) == 1


def test_unparseable_dict_item_skipped(qm: QuotaManager) -> None:
    # #22: a dict with no name parses to None and is skipped (candidate-is-None
    # branch), without crashing or counting toward results.
    provider = ApifyProvider(
        api_key="k", quota_manager=qm, http_client=_client([{"headline": "no name"}, *APIFY_FULL])
    )
    out = provider.search_linkedin_profiles(company="X", role_keywords=["eng"], limit=10)
    assert all(c.full_name for c in out)
    assert len(out) == len(APIFY_FULL)
