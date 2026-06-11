"""
tests/test_serper_snippet.py
Layer 1: SerperProvider now captures the ``snippet`` field on each
organic result and exposes a ``search_general`` method for the Tier-4
company-news hook.
"""

from __future__ import annotations

import httpx

from src.providers.serper import SerperProvider


def _mock_client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# _parse_organic_result captures snippet
# ---------------------------------------------------------------------------


class TestSnippetCapture:
    def test_snippet_populated_when_present(self):
        payload = {
            "organic": [
                {
                    "title": "Jane Doe - Composites Engineer at Acme | LinkedIn",
                    "link": "https://linkedin.com/in/janedoe",
                    "snippet": "Composites engineer; led 787 bonded repair certification.",
                },
            ]
        }
        provider = SerperProvider(api_key="k", http_client=_mock_client(payload))
        results = provider.search_linkedin_profiles(
            company="acme",
            role_keywords=["engineer"],
            limit=1,
        )
        assert len(results) == 1
        assert results[0].snippet == "Composites engineer; led 787 bonded repair certification."

    def test_missing_snippet_becomes_none(self):
        payload = {
            "organic": [
                {
                    "title": "Jane Doe - Engineer at Acme",
                    "link": "https://linkedin.com/in/janedoe",
                    # No snippet field.
                },
            ]
        }
        provider = SerperProvider(api_key="k", http_client=_mock_client(payload))
        results = provider.search_linkedin_profiles(
            company="acme",
            role_keywords=["engineer"],
            limit=1,
        )
        assert results[0].snippet is None

    def test_whitespace_only_snippet_becomes_none(self):
        payload = {
            "organic": [
                {
                    "title": "Jane Doe - Engineer at Acme",
                    "link": "https://linkedin.com/in/janedoe",
                    "snippet": "   ",
                },
            ]
        }
        provider = SerperProvider(api_key="k", http_client=_mock_client(payload))
        results = provider.search_linkedin_profiles(
            company="acme",
            role_keywords=["engineer"],
            limit=1,
        )
        assert results[0].snippet is None


# ---------------------------------------------------------------------------
# search_general for Tier-4 company news
# ---------------------------------------------------------------------------


class TestSearchGeneral:
    def test_returns_top_snippet(self):
        payload = {
            "organic": [
                {"title": "News headline", "link": "https://x", "snippet": "Acme closed Series D."},
            ]
        }
        provider = SerperProvider(api_key="k", http_client=_mock_client(payload))
        snippet = provider.search_general("acme news 2026")
        assert snippet == "Acme closed Series D."

    def test_returns_first_non_empty_snippet(self):
        payload = {
            "organic": [
                {"title": "Empty", "link": "https://x", "snippet": "  "},
                {"title": "Real", "link": "https://y", "snippet": "Real news here."},
            ]
        }
        provider = SerperProvider(api_key="k", http_client=_mock_client(payload))
        assert provider.search_general("q") == "Real news here."

    def test_no_organic_returns_none(self):
        provider = SerperProvider(api_key="k", http_client=_mock_client({"organic": []}))
        assert provider.search_general("q") is None

    def test_no_snippets_returns_none(self):
        payload = {
            "organic": [
                {"title": "x", "link": "https://x"},  # no snippet
                {"title": "y", "link": "https://y", "snippet": ""},
            ]
        }
        provider = SerperProvider(api_key="k", http_client=_mock_client(payload))
        assert provider.search_general("q") is None
