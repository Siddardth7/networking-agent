"""
tests/test_email_path.py
Issue #14: hermetic end-to-end email resolution. The REAL Hunter-pattern provider
(#13) + Apollo flow through ``finder._resolve_email`` with a real QuotaManager —
exhaustion and fallback ordering validated without any network.

(``test_provider_fallback.py`` covers _resolve_email's branches with stubs; this
proves the real providers behave the same through that path, and that Hunter's
pattern cache makes the channel uncapped across a batch.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from src.agents.finder import _resolve_email
from src.core.migrations import run_migrations
from src.core.schemas import ContactCandidate
from src.providers.apollo import ApolloProvider
from src.providers.hunter import HunterProvider
from src.providers.quota_manager import QuotaManager

_PATTERN = {"data": {"pattern": "{first}.{last}"}}
_NO_PATTERN = {"data": {"pattern": None}}


@pytest.fixture()
def qm(tmp_path: Path) -> QuotaManager:
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    conn.commit()
    conn.close()
    return QuotaManager(db_path=str(db_path))


class _CountingClient:
    """An httpx.Client wrapper that counts how many requests it served."""

    def __init__(self, payload: dict) -> None:
        self.calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(200, json=payload, request=request)

        self.client = httpx.Client(transport=httpx.MockTransport(handler))


def _hunter(qm: QuotaManager, payload: dict) -> tuple[HunterProvider, _CountingClient]:
    cc = _CountingClient(payload)
    return HunterProvider(api_key="k", quota_manager=qm, http_client=cc.client), cc


def _apollo(qm: QuotaManager, email: str | None) -> tuple[ApolloProvider, _CountingClient]:
    cc = _CountingClient({"person": {"email": email}})
    return ApolloProvider(api_key="k", quota_manager=qm, http_client=cc.client), cc


def _cand(name: str = "Jane Doe", email: str | None = None) -> ContactCandidate:
    return ContactCandidate(full_name=name, company_slug="acme", email=email)


def _state() -> dict[str, bool]:
    return {"hunter_exhausted": False, "apollo_exhausted": False}


# ---------------------------------------------------------------------------
# Fallback ordering
# ---------------------------------------------------------------------------


def test_source_email_wins_no_provider_calls(qm: QuotaManager) -> None:
    hunter, hc = _hunter(qm, _PATTERN)
    apollo, ac = _apollo(qm, "a@acme.com")
    r = _resolve_email(_cand(email="given@acme.com"), hunter, apollo, "acme.com", _state())
    assert r.email == "given@acme.com" and r.source == "IMPORT"
    assert hc.calls == 0 and ac.calls == 0  # neither provider touched


def test_hunter_pattern_resolves_and_skips_apollo(qm: QuotaManager) -> None:
    hunter, hc = _hunter(qm, _PATTERN)
    apollo, ac = _apollo(qm, "a@acme.com")
    r = _resolve_email(_cand("Jane Doe"), hunter, apollo, "acme.com", _state())
    assert r.email == "jane.doe@acme.com"
    assert r.source == "hunter_pattern"
    assert ac.calls == 0  # Hunter hit → Apollo never consulted


def test_hunter_no_pattern_falls_back_to_apollo(qm: QuotaManager) -> None:
    hunter, _hc = _hunter(qm, _NO_PATTERN)
    apollo, ac = _apollo(qm, "a@acme.com")
    r = _resolve_email(_cand("Jane Doe"), hunter, apollo, "acme.com", _state())
    assert r.email == "a@acme.com"  # Apollo fallback
    assert ac.calls == 1


# ---------------------------------------------------------------------------
# Quota exhaustion + sentinels
# ---------------------------------------------------------------------------


def test_hunter_exhausted_falls_back_to_apollo(qm: QuotaManager) -> None:
    for _ in range(25):  # exhaust the hunter free tier
        qm.increment("hunter", 1)
    hunter, hc = _hunter(qm, _PATTERN)
    apollo, ac = _apollo(qm, "a@acme.com")
    state = _state()
    r = _resolve_email(_cand("Jane Doe"), hunter, apollo, "acme.com", state)
    assert r.email == "a@acme.com"
    assert state["hunter_exhausted"] is True
    assert hc.calls == 0  # quota gate fired before any Hunter HTTP call


def test_both_exhausted_yields_hunter_exhausted_sentinel(qm: QuotaManager) -> None:
    for _ in range(25):
        qm.increment("hunter", 1)
    for _ in range(50):
        qm.increment("apollo", 1)
    hunter, _hc = _hunter(qm, _PATTERN)
    apollo, _ac = _apollo(qm, "a@acme.com")
    r = _resolve_email(_cand("Jane Doe"), hunter, apollo, "acme.com", _state())
    assert r.email is None
    assert r.source == "HUNTER_EXHAUSTED"


def test_apollo_exhausted_yields_apollo_sentinel(qm: QuotaManager) -> None:
    # Apollo-only enrichment (no Hunter) with Apollo capped → APOLLO_EXHAUSTED.
    # (With Hunter present and running, its empty result would win instead — see
    # test_hunter_ran_empty_apollo_empty_returns_hunter_source.)
    for _ in range(50):
        qm.increment("apollo", 1)
    apollo, ac = _apollo(qm, "a@acme.com")
    r = _resolve_email(_cand("Jane Doe"), None, apollo, "acme.com", _state())
    assert r.source == "APOLLO_EXHAUSTED"
    assert ac.calls == 0  # quota gate fired before the Apollo HTTP call


def test_hunter_ran_empty_apollo_empty_returns_hunter_source(qm: QuotaManager) -> None:
    # Both providers run and find nothing → the "hunter ran, empty" result wins.
    hunter, _hc = _hunter(qm, _NO_PATTERN)
    apollo, ac = _apollo(qm, None)
    r = _resolve_email(_cand("Jane Doe"), hunter, apollo, "acme.com", _state())
    assert r.email is None
    assert r.source == "hunter"
    assert ac.calls == 1  # Apollo did run, just found nothing


# ---------------------------------------------------------------------------
# Uncapped: the pattern is fetched once per company for a whole batch
# ---------------------------------------------------------------------------


def test_batch_shares_one_hunter_lookup(qm: QuotaManager) -> None:
    hunter, hc = _hunter(qm, _PATTERN)
    apollo, ac = _apollo(qm, "a@acme.com")
    state = _state()  # shared across the batch, as ingest does

    names = ["Jane Doe", "John Smith", "Amy Lee"]
    results = [_resolve_email(_cand(n), hunter, apollo, "acme.com", state) for n in names]

    assert [r.email for r in results] == [
        "jane.doe@acme.com",
        "john.smith@acme.com",
        "amy.lee@acme.com",
    ]
    assert hc.calls == 1  # one domain-search served the whole batch
    assert ac.calls == 0
    assert qm.remaining("hunter") == 24  # exactly one credit spent
