"""
tests/test_search_cache.py
v0.2.1 free-quota work: Serper responses are cached in SQLite so repeat
queries (re-runs, resumed runs, trial iterations) never re-spend search
credits. Cache hits skip BOTH the HTTP call and the quota increment.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.core.db import get_connection, init_db, with_writer
from src.core.search_cache import cache_get, cache_put
from src.providers.serper import SerperProvider


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    init_db()
    return path


class TestCacheRoundtrip:
    def test_put_then_get(self, db_path):
        payload = {"q": "site:linkedin.com/in acme", "num": 10}
        response = {"organic": [{"title": "Jane Doe - Engineer", "link": "x"}]}
        cache_put("serper", payload, response)
        assert cache_get("serper", payload, ttl_days=14) == response

    def test_miss_on_unknown_payload(self, db_path):
        assert cache_get("serper", {"q": "never stored"}, ttl_days=14) is None

    def test_payload_key_order_insensitive(self, db_path):
        cache_put("serper", {"q": "acme", "num": 10}, {"organic": []})
        assert cache_get("serper", {"num": 10, "q": "acme"}, ttl_days=14) == {"organic": []}

    def test_providers_namespaced(self, db_path):
        cache_put("serper", {"q": "acme"}, {"organic": ["a"]})
        assert cache_get("other", {"q": "acme"}, ttl_days=14) is None

    def test_ttl_zero_disables_cache(self, db_path):
        cache_put("serper", {"q": "acme"}, {"organic": []})
        assert cache_get("serper", {"q": "acme"}, ttl_days=0) is None

    def test_expired_entry_is_a_miss(self, db_path):
        payload = {"q": "stale"}
        cache_put("serper", payload, {"organic": []})
        # Backdate the row past the TTL window.
        with with_writer() as conn:
            conn.execute("UPDATE search_cache SET created_at = datetime('now', '-30 days')")
        assert cache_get("serper", payload, ttl_days=14) is None
        assert cache_get("serper", payload, ttl_days=60) is not None

    def test_put_overwrites_existing_entry(self, db_path):
        payload = {"q": "acme"}
        cache_put("serper", payload, {"organic": ["old"]})
        cache_put("serper", payload, {"organic": ["new"]})
        assert cache_get("serper", payload, ttl_days=14) == {"organic": ["new"]}
        conn = get_connection()
        try:
            n = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
        finally:
            conn.close()
        assert n == 1


def _serper_response(titles: list[str]) -> Mock:
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "organic": [
            {
                "title": f"{t} - Engineer - Acme",
                "link": f"https://linkedin.com/in/{t.lower()}",
                "snippet": "snippet",
            }
            for t in titles
        ]
    }
    return resp


class TestSerperUsesCache:
    def test_second_identical_search_skips_http_and_quota(self, db_path):
        http = Mock()
        http.post.return_value = _serper_response(["Jane"])
        quota = Mock()
        provider = SerperProvider(
            api_key="k", quota_manager=quota, http_client=http, cache_ttl_days=14
        )

        first = provider.search_linkedin_profiles("Acme", ["quality"], limit=1)
        second = provider.search_linkedin_profiles("Acme", ["quality"], limit=1)

        assert [c.full_name for c in first] == ["Jane"]
        assert [c.full_name for c in second] == ["Jane"]
        assert http.post.call_count == 1
        assert quota.increment.call_count == 1

    def test_different_query_is_a_cache_miss(self, db_path):
        http = Mock()
        http.post.return_value = _serper_response(["Jane"])
        provider = SerperProvider(api_key="k", http_client=http, cache_ttl_days=14)

        provider.search_linkedin_profiles("Acme", ["quality"], limit=1)
        provider.search_linkedin_profiles("Other Co", ["quality"], limit=1)
        assert http.post.call_count == 2

    def test_cache_disabled_by_default_zero_ttl(self, db_path):
        # Default construction (no cache_ttl_days) must not cache — unit
        # callers and tests get untouched pass-through behavior.
        http = Mock()
        http.post.return_value = _serper_response(["Jane"])
        provider = SerperProvider(api_key="k", http_client=http)

        provider.search_linkedin_profiles("Acme", ["quality"], limit=1)
        provider.search_linkedin_profiles("Acme", ["quality"], limit=1)
        assert http.post.call_count == 2

    def test_search_general_cached(self, db_path):
        http = Mock()
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"organic": [{"snippet": "Acme news snippet"}]}
        http.post.return_value = resp
        quota = Mock()
        provider = SerperProvider(
            api_key="k", quota_manager=quota, http_client=http, cache_ttl_days=14
        )

        assert provider.search_general("acme news") == "Acme news snippet"
        assert provider.search_general("acme news") == "Acme news snippet"
        assert http.post.call_count == 1
        assert quota.increment.call_count == 1


class TestConfigKnob:
    def test_default_ttl(self):
        from src.core.config import Config

        assert Config().search_cache_ttl_days == 14

    def test_ttl_from_yaml(self, tmp_path, monkeypatch):
        import os

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("providers:\n  search_cache_ttl_days: 7\n")
        os.chmod(cfg_file, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg_file))
        from src.core.config import load_config

        assert load_config().search_cache_ttl_days == 7
