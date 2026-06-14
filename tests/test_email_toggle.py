"""
tests/test_email_toggle.py
v0.2.1 free-quota work: Hunter email enrichment is opt-in. With
``pipeline.enable_email_enrichment: false`` (the new default) the finder
runs without a Hunter key, spends zero Hunter quota, and marks contacts
EMAIL_DISABLED; the drafter already skips cold email for contacts with no
address.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.finder import find_contacts
from src.core.config import Config
from src.core.db import get_connection, init_db
from src.core.schemas import ContactCandidate, EmailResult


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    init_db()
    return path


def _cfg(monkeypatch, enable_email: bool):
    cfg = Config(
        anthropic_api_key="k",
        serper_api_key="k",
        hunter_api_key=None,
        enable_email_enrichment=enable_email,
    )
    monkeypatch.setattr("src.agents.finder.load_config", lambda: cfg)
    return cfg


def _serper_mock() -> Mock:
    serper = Mock()
    serper.search_linkedin_profiles.return_value = [
        ContactCandidate(
            full_name="Jane Doe",
            title="Quality Engineer",
            linkedin_url="https://linkedin.com/in/janedoe",
            company_slug="acme",
            snippet="Quality engineer focused on MRB dispositions.",
        ),
    ]
    serper.search_general.return_value = None
    return serper


def _classifier_client() -> Mock:
    tool = Mock()
    tool.type = "tool_use"
    tool.input = {
        "persona": "PEER_ENGINEER",
        "focus_area": "MANUFACTURING",
        "hook_signal": "focused on MRB dispositions",
    }
    client = Mock()
    client.messages.create.return_value = Mock(content=[tool])
    return client


class TestEnrichmentDisabled:
    def test_runs_without_hunter_key(self, db_path, monkeypatch):
        _cfg(monkeypatch, enable_email=False)
        results = find_contacts(
            "acme",
            limit=1,
            serper_provider=_serper_mock(),
            hunter_provider=None,
            anthropic_client=_classifier_client(),
        )
        assert len(results) == 1

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT email, source_provider FROM contacts WHERE full_name = 'Jane Doe'"
            ).fetchone()
        finally:
            conn.close()
        assert row["email"] is None
        assert row["source_provider"] == "EMAIL_DISABLED"

    def test_injected_hunter_still_used_when_disabled(self, db_path, monkeypatch):
        # Explicit DI wins over the config toggle — tests and power users
        # who hand the finder a provider mean it.
        _cfg(monkeypatch, enable_email=False)
        hunter = Mock()
        hunter.find_email.return_value = EmailResult(
            email="jane@acme.com", verified=True, confidence=90, source="hunter"
        )
        find_contacts(
            "acme",
            limit=1,
            serper_provider=_serper_mock(),
            hunter_provider=hunter,
            anthropic_client=_classifier_client(),
        )
        hunter.find_email.assert_called_once()

    def test_default_config_disables_enrichment(self):
        assert Config().enable_email_enrichment is False


class TestEnrichmentEnabled:
    def test_enabled_without_key_raises(self, db_path, monkeypatch):
        _cfg(monkeypatch, enable_email=True)
        with pytest.raises(ValueError, match="HUNTER_API_KEY"):
            find_contacts(
                "acme",
                limit=1,
                serper_provider=_serper_mock(),
                hunter_provider=None,
                anthropic_client=_classifier_client(),
            )

    def test_yaml_toggle_loads(self, tmp_path, monkeypatch):
        import os

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("pipeline:\n  enable_email_enrichment: true\n")
        os.chmod(cfg_file, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg_file))
        from src.core.config import load_config

        assert load_config().enable_email_enrichment is True


class TestPreflightSkipsHunter:
    def test_check_hunter_skipped_when_disabled(self, monkeypatch):
        from src.cli.network_check import _check_hunter

        cfg = Config(hunter_api_key=None, enable_email_enrichment=False)
        monkeypatch.setattr("src.core.config.load_config", lambda: cfg)
        lines, is_error = _check_hunter()
        assert is_error is False
        assert any("disabled" in line.lower() for line in lines)

    def test_check_hunter_errors_when_enabled_and_unkeyed(self, monkeypatch):
        from src.cli.network_check import _check_hunter

        cfg = Config(hunter_api_key=None, enable_email_enrichment=True)
        monkeypatch.setattr("src.core.config.load_config", lambda: cfg)
        lines, is_error = _check_hunter()
        assert is_error is True
