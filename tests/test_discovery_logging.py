"""Issue #96: discovery diagnostics must surface, so a 0-result run is
distinguishable from a provider failure.

Covers the shared stderr-handler helper, the CLI shortfall warning, and the
Apify provider logging the exact resolved query it sent.
"""

from __future__ import annotations

import argparse
import logging
from unittest.mock import MagicMock

import httpx
import pytest

import src.cli.network_classify_host as classify_host
from src.cli import _CLI_LOG_HANDLER, configure_cli_logging
from src.core.schemas import ContactCandidate
from src.providers.apify import ApifyProvider

APIFY_ONE = [{
    "firstName": "Ada", "lastName": "Byron",
    "linkedinUrl": "https://www.linkedin.com/in/ada",
}]


@pytest.fixture(autouse=True)
def _clean_cli_handler():
    """Keep the module-global ``networking_agent`` logger hermetic across tests."""
    logger = logging.getLogger("networking_agent")

    def _remove():
        for h in list(logger.handlers):
            if getattr(h, "name", None) == _CLI_LOG_HANDLER:
                logger.removeHandler(h)

    _remove()
    yield
    _remove()


class TestConfigureCliLogging:
    def test_installs_named_stderr_handler(self):
        logger = logging.getLogger("networking_agent")
        configure_cli_logging()
        handlers = [h for h in logger.handlers if getattr(h, "name", None) == _CLI_LOG_HANDLER]
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.StreamHandler)
        assert logger.level == logging.INFO

    def test_idempotent(self):
        logger = logging.getLogger("networking_agent")
        configure_cli_logging()
        configure_cli_logging()
        named = [h for h in logger.handlers if getattr(h, "name", None) == _CLI_LOG_HANDLER]
        assert len(named) == 1


def test_run_discover_warns_on_shortfall(caplog, monkeypatch):
    """A run returning fewer than the limit logs a one-line, human-readable reason."""
    provider = MagicMock()
    provider.search_linkedin_profiles.return_value = []  # clean-empty, no error
    monkeypatch.setattr(classify_host, "load_config",
                        lambda: MagicMock(finder_role_keywords=["engineer"]))
    monkeypatch.setattr(classify_host, "build_discovery_chain", lambda cfg: ([provider], None))

    with caplog.at_level(logging.WARNING, logger="networking_agent.classify_host"):
        rc = classify_host.run_discover(
            argparse.Namespace(slug="caterpillar", limit=10, location="Peoria IL", keywords=None)
        )
    assert rc == 0
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "0/10" in msg
    assert "caterpillar" in msg
    assert "Peoria IL" in msg


def test_run_discover_no_warning_when_limit_met(caplog, monkeypatch):
    """A run that meets the limit stays quiet — no shortfall noise."""
    cand = ContactCandidate(
        full_name="Ada Byron", title="Engineer", snippet="x", company_slug="cat",
    )
    provider = MagicMock()
    provider.search_linkedin_profiles.return_value = [cand]
    monkeypatch.setattr(classify_host, "load_config",
                        lambda: MagicMock(finder_role_keywords=["engineer"]))
    monkeypatch.setattr(classify_host, "build_discovery_chain", lambda cfg: ([provider], None))

    with caplog.at_level(logging.WARNING, logger="networking_agent.classify_host"):
        rc = classify_host.run_discover(
            argparse.Namespace(slug="cat", limit=1, location=None, keywords=None)
        )
    assert rc == 0
    assert not [r for r in caplog.records if r.name == "networking_agent.classify_host"]


def test_apify_logs_resolved_query(caplog):
    """The provider logs the exact searchQuery / titles / locations it sent."""
    def handler(request):
        return httpx.Response(200, json=APIFY_ONE, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ApifyProvider(api_key="k", quota_manager=None, http_client=client)
    with caplog.at_level(logging.INFO, logger="networking_agent.apify"):
        provider.search_linkedin_profiles(
            company="Caterpillar", role_keywords=["thermal engineer"],
            limit=10, location="Peoria IL",
        )
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "searchQuery='Caterpillar'" in msg
    assert "thermal engineer" in msg
    assert "Peoria IL" in msg
    assert "1 items" in msg
