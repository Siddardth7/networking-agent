"""
tests/test_robustness_polish.py
STRICT_AUDIT v0.2.0 polish items:
- A17: voice.md size cap (truncate + warn)
- A18: config permission check via fstat on the open descriptor
- A20: LLM response shape guards (typed EmptyLLMResponseError; non-dict
  tool input falls back)
- A21: companies.domain wins over slug inference for Hunter lookups
- A25: provider close() lifecycle
- A26: voice/library paths follow NETWORKING_AGENT_CONFIG
"""

from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

from src.core.errors import EmptyLLMResponseError


class TestVoiceDocSizeCap:
    def test_oversized_voice_doc_truncated(self, tmp_path, monkeypatch, caplog):
        import logging

        from src.agents import drafter

        cfg = tmp_path / "config.yaml"
        cfg.write_text("{}")
        os.chmod(cfg, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg))
        (tmp_path / "voice.md").write_text("v" * 20_000, encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            text = drafter._load_voice_doc()
        assert len(text) == drafter._VOICE_DOC_MAX_CHARS
        assert any("truncating" in r.message for r in caplog.records)

    def test_normal_voice_doc_untouched(self, tmp_path, monkeypatch):
        from src.agents import drafter

        cfg = tmp_path / "config.yaml"
        cfg.write_text("{}")
        os.chmod(cfg, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg))
        (tmp_path / "voice.md").write_text("short voice doc", encoding="utf-8")
        assert drafter._load_voice_doc() == "short voice doc"


class TestConfigFstat:
    def test_world_readable_config_rejected(self, tmp_path, monkeypatch):
        from src.core.config import ConfigSecurityError, load_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text("keys: {}\n")
        os.chmod(cfg, 0o644)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg))
        with pytest.raises(ConfigSecurityError):
            load_config()

    def test_0600_config_accepted(self, tmp_path, monkeypatch):
        from src.core.config import load_config

        cfg = tmp_path / "config.yaml"
        cfg.write_text("quality:\n  linkedin_char_limit: 180\n")
        os.chmod(cfg, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg))
        assert load_config().linkedin_char_limit == 180


class TestLLMShapeGuards:
    def test_empty_content_raises_typed_error(self):
        from src.agents.shared import call_claude

        client = Mock()
        client.messages.create.return_value = Mock(content=[])
        with pytest.raises(EmptyLLMResponseError):
            call_claude("hi", client)

    def test_textless_block_raises_typed_error(self):
        from src.agents.shared import call_claude

        block = Mock(spec=[])  # no .text attribute at all
        client = Mock()
        client.messages.create.return_value = Mock(content=[block])
        with pytest.raises(EmptyLLMResponseError):
            call_claude("hi", client)

    def test_non_dict_tool_input_falls_back(self):
        from src.agents.finder import _classify_contact
        from src.core.schemas import ContactCandidate, FocusArea, Persona

        tool = Mock()
        tool.type = "tool_use"
        tool.input = "not a dict"
        client = Mock()
        client.messages.create.return_value = Mock(content=[tool])
        candidate = ContactCandidate(
            full_name="X", title="Engineer",
            linkedin_url="https://linkedin.com/in/x", company_slug="acme",
        )
        persona, focus, signal = _classify_contact(candidate, "acme", client)
        assert persona == Persona.PEER_ENGINEER
        assert focus == FocusArea.PEER
        assert signal is None


class TestCompanyDomain:
    def test_stored_domain_wins(self, tmp_path, monkeypatch):
        from src.agents.finder import _company_domain
        from src.core.db import init_db, with_writer

        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "d.db")
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, domain) "
                "VALUES ('joby-aviation', 'Joby', 'joby.aero')"
            )
            company_id = c.lastrowid
        assert _company_domain(company_id, "joby-aviation") == "joby.aero"

    def test_inference_when_no_domain(self, tmp_path, monkeypatch):
        from src.agents.finder import _company_domain
        from src.core.db import init_db, with_writer

        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "d.db")
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name) VALUES ('acme-corp', 'Acme')"
            )
            company_id = c.lastrowid
        assert _company_domain(company_id, "acme-corp") == "acmecorp.com"


class TestProviderClose:
    def test_serper_close_releases_client(self):
        from src.providers.serper import SerperProvider

        http = Mock()
        provider = SerperProvider(api_key="k", http_client=http)
        provider.close()
        http.close.assert_called_once()

    def test_hunter_close_releases_client(self):
        from src.providers.hunter import HunterProvider

        http = Mock()
        provider = HunterProvider(api_key="k", http_client=http)
        provider.close()
        http.close.assert_called_once()


class TestEnvRelocatedPaths:
    def test_voice_and_library_paths_follow_config_dir(self, tmp_path, monkeypatch):
        from src.core.config import resume_library_path, voice_doc_path

        cfg = tmp_path / "config.yaml"
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg))
        assert voice_doc_path() == tmp_path / "voice.md"
        assert resume_library_path() == tmp_path / "resume_library.yaml"
