"""
tests/test_apollo.py
Unit tests for ApolloProvider (email fallback after Hunter).
Hermetic: httpx.MockTransport + a temp-SQLite QuotaManager.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from src.core.migrations import run_migrations
from src.providers.apollo import ApolloProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted


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


def test_match_returns_email(qm: QuotaManager) -> None:
    client = _client({"person": {"email": "jane@acme.com"}})
    provider = ApolloProvider(api_key="k", quota_manager=qm, http_client=client)
    result = provider.find_email(full_name="Jane Doe", company_domain="acme.com")
    assert result.email == "jane@acme.com"
    assert result.source == "apollo"
    assert result.verified is False  # Apollo match isn't a verified address


def test_masked_email_treated_as_no_result(qm: QuotaManager) -> None:
    client = _client({"person": {"email": "email_not_unlocked@domain.com"}})
    provider = ApolloProvider(api_key="k", quota_manager=qm, http_client=client)
    result = provider.find_email(full_name="Jane Doe", company_domain="acme.com")
    assert result.email is None
    assert result.source == "apollo"


def test_no_person_returns_empty(qm: QuotaManager) -> None:
    provider = ApolloProvider(api_key="k", quota_manager=qm, http_client=_client({}))
    result = provider.find_email(full_name="Jane Doe", company_domain="acme.com")
    assert result.email is None


def test_quota_exhaustion_raises(qm: QuotaManager) -> None:
    for _ in range(50):  # _DEFAULT_LIMITS["apollo"]
        qm.increment("apollo")
    provider = ApolloProvider(
        api_key="k", quota_manager=qm, http_client=_client({"person": {"email": "x@y.com"}})
    )
    with pytest.raises(QuotaExhausted):
        provider.find_email(full_name="Jane Doe", company_domain="acme.com")


def test_no_quota_manager_skips_increment() -> None:
    # #22: quota_manager=None → the increment branch is skipped, match still works.
    provider = ApolloProvider(
        api_key="k", quota_manager=None, http_client=_client({"person": {"email": "z@acme.com"}})
    )
    result = provider.find_email(full_name="Jane Doe", company_domain="acme.com")
    assert result.email == "z@acme.com"


def test_close_releases_client() -> None:
    client = _client({})
    provider = ApolloProvider(api_key="k", http_client=client)
    provider.close()
    assert client.is_closed
