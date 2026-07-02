"""
tests/test_live_smoke.py
Opt-in LIVE API smoke tests (issue #26): everything else in the suite is
mocked, so nothing catches a provider contract drifting (response shape,
auth scheme, endpoint) until a real run breaks. These hit the real services.

DOUBLY gated — they run only when BOTH hold:
  1. ``NETWORKING_AGENT_LIVE_SMOKE=1`` is set (explicit opt-in; a plain
     ``pytest`` run with keys configured must never spend credits), and
  2. the specific provider's key resolves (env var or the user's real
     ``~/.networking-agent/config.yaml``).
CI has neither, so these always skip there.

Run them:

    NETWORKING_AGENT_LIVE_SMOKE=1 pytest tests/test_live_smoke.py -v --no-cov

Cost per full run: 1 Serper search credit (of 100/mo) + ~$0.0001 of Anthropic
Haiku; the Apify check hits the free ``users/me`` endpoint. Quota/cache writes
go to a temp DB — a smoke run never touches the real state.db.

Note: conftest disables ``.env`` auto-loading for the whole suite (hermeticity
gate), so keys living only in a ``.env`` file are invisible here — put them in
the shell env or ``config.yaml`` when running the smoke.

These assert response SHAPE (the contract), never content — live results vary.
"""

from __future__ import annotations

import os

import httpx
import pytest

from src.core.config import load_config

pytestmark = pytest.mark.skipif(
    os.environ.get("NETWORKING_AGENT_LIVE_SMOKE") != "1",
    reason="live smoke is opt-in: set NETWORKING_AGENT_LIVE_SMOKE=1",
)


def _cfg():
    """The user's REAL config (env → ~/.networking-agent) — deliberate here."""
    return load_config()


def test_serper_search_contract(tmp_path, monkeypatch):
    """One real LinkedIn-profile search parses into ContactCandidates.

    The highest-value contract in the repo: discovery depends on Serper's
    organic-results shape, which the mocks pin to a 2026-06 snapshot.
    Costs 1 search credit.
    """
    cfg = _cfg()
    if not cfg.serper_api_key:
        pytest.skip("no SERPER_API_KEY configured")
    # Quota bookkeeping goes to a schema'd temp DB, never the real state.db.
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    from src.core.db import init_db
    from src.providers.quota_manager import QuotaManager
    from src.providers.serper import SerperProvider

    init_db()
    provider = SerperProvider(
        api_key=cfg.serper_api_key,
        quota_manager=QuotaManager(),
    )
    candidates = provider.search_linkedin_profiles(
        company="boeing", role_keywords=["engineer"], limit=2
    )
    # Shape, not content: a well-formed (possibly short) candidate list.
    assert isinstance(candidates, list)
    for cand in candidates:
        assert cand.full_name
        assert cand.company_slug
        assert cand.linkedin_url and "linkedin.com" in cand.linkedin_url


def test_apify_auth_contract():
    """The Apify token authenticates against the free ``users/me`` endpoint.

    Same call the doctor makes — catches auth-scheme or envelope drift.
    Costs nothing.
    """
    cfg = _cfg()
    if not cfg.apify_api_key:
        pytest.skip("no APIFY_API_KEY configured")
    resp = httpx.get(
        "https://api.apify.com/v2/users/me",
        headers={"Authorization": f"Bearer {cfg.apify_api_key}"},
        timeout=30.0,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["id"]


def test_anthropic_ping_contract():
    """A 1-token Haiku call returns a normal message envelope. ~$0.0001."""
    cfg = _cfg()
    if not cfg.anthropic_api_key:
        pytest.skip("no ANTHROPIC_API_KEY configured")
    from src.core.config import HAIKU_MODEL, get_anthropic_client

    client = get_anthropic_client(cfg.anthropic_api_key)
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except Exception as exc:  # credit exhaustion is a billing state, not drift
        if "credit balance" in str(exc).lower():
            pytest.skip(f"anthropic credit exhausted (key/auth OK): {exc}")
        raise
    assert resp.content is not None
    assert resp.usage.output_tokens >= 1
