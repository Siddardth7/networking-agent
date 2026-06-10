"""
tests/test_intro_dedup.py
AUDIT-A8: redundant self-intro between body and signature. The June-6
run repeated "MS Aerospace Engineering student at UIUC ... December" in
the body AND the signature of most drafts.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.drafter import draft_for_contacts
from src.agents.guardrails import detect_redundant_intro

REDUNDANT = (
    "MS Aerospace Engineering student at UIUC wrapping up in December — "
    "your MRB work caught my eye. Would value connecting.\n\n"
    "Sid Pathipaka\nMS Aerospace Engineering, UIUC"
)
CLEAN = (
    "Your MRB work caught my eye — I focus on composites and quality. "
    "Would value connecting.\n\nSid Pathipaka\nMS Aerospace Engineering, UIUC"
)


class TestDetectRedundantIntro:
    def test_repeated_program_and_school_flagged(self):
        assert detect_redundant_intro(REDUNDANT)

    def test_identity_once_passes(self):
        assert not detect_redundant_intro(CLEAN)

    def test_no_identity_passes(self):
        assert not detect_redundant_intro("Great talk at SAMPE. Would value connecting.")


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    monkeypatch.setattr("src.agents.drafter._MAX_WORKERS", 1)
    from src.core.config import Config, load_config
    real = load_config

    def _no_critic_cfg():
        cfg = real()
        return Config(
            anthropic_api_key=cfg.anthropic_api_key,
            serper_api_key=cfg.serper_api_key,
            hunter_api_key=cfg.hunter_api_key,
            enable_critic=False,
        )

    monkeypatch.setattr("src.agents.drafter.load_config", _no_critic_cfg)
    return path


def _seed_one() -> int:
    from src.core.db import init_db, with_writer
    init_db()
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            """INSERT INTO contacts
               (company_id, full_name, title, persona, focus_area, linkedin_url,
                email, hook, state)
               VALUES (?, 'Val Test', 'MRB Engineer', 'PEER_ENGINEER',
                       'MANUFACTURING', 'https://linkedin.com/in/v', NULL,
                       'your MRB work', 'SELECTED')""",
            (company_id,),
        )
        return c.lastrowid


def _mk_client(responses: list[str]):
    client = Mock()
    captured: list[str] = []

    def _create(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        msg = Mock()
        msg.content = [Mock(text=responses.pop(0))]
        return msg

    client.messages.create.side_effect = _create
    client.captured_prompts = captured
    return client


class TestDrafterDedupsIntro:
    def test_redundant_intro_triggers_regen(self, db_path):
        cid = _seed_one()
        client = _mk_client([
            REDUNDANT,           # POST-style fault on CONN gen 1
            CLEAN,               # CONN regen
            "Clean follow-up.",  # POST
        ])
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.body == CLEAN
        assert conn_draft.quality_code == "OK"
        assert "once" in client.captured_prompts[1].lower()

    def test_persistent_redundancy_soft_flagged(self, db_path):
        cid = _seed_one()
        client = _mk_client([
            REDUNDANT,
            REDUNDANT,  # regen still redundant
            "Clean follow-up.",
        ])
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.quality_code == "SOFT_FLAG"
