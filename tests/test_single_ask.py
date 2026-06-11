"""
tests/test_single_ask.py
AUDIT-A7: single-ask enforcement at the DRAFTER level (not just the
critic). Multi-ask drafts were a main driver of June-6 critic holds:
"grab 15 minutes... Otherwise, if there's someone on the team..." —
research and the voice doc both demand exactly one ask.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.drafter import draft_for_contacts
from src.agents.guardrails import detect_multi_ask
from src.core.db import init_db, with_writer

MULTI_ASK = (
    "Saw your MRB work. Happy to grab 15 minutes if you're open to it. "
    "Also, if you know anyone on the hiring side, I'd appreciate a pointer."
)
SINGLE_ASK = "Saw your MRB work. Would you have 15 minutes in the next couple weeks?"


class TestDetectMultiAsk:
    def test_two_questions_flagged(self):
        assert detect_multi_ask(
            "Are you mostly auditing, or do you get into material certs? Could we grab 15 minutes?"
        )

    def test_two_ask_sentences_flagged(self):
        assert detect_multi_ask(MULTI_ASK)

    def test_hedge_stacked_ask_flagged(self):
        assert detect_multi_ask(
            "Happy to grab 15 minutes, or if there's a better person on "
            "your team, point me their way."
        )

    def test_otherwise_hedge_flagged(self):
        assert detect_multi_ask(
            "Would love 15 minutes. Otherwise, if you know someone closer "
            "to the MRB side, happy to be redirected."
        )

    def test_single_clean_ask_passes(self):
        assert not detect_multi_ask(SINGLE_ASK)

    def test_single_ask_with_minutes_and_would_you_passes(self):
        # One sentence containing two ask-ish phrases is still ONE ask.
        assert not detect_multi_ask("Would you have 15 minutes next week to talk composites?")

    def test_no_ask_passes(self):
        # Zero asks is the critic's problem (one_ask rubric), not multi-ask.
        assert not detect_multi_ask("Enjoyed your talk at SAMPE. Great work.")


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
               VALUES (?, 'Jimmy Test', 'MRB Engineer', 'PEER_ENGINEER',
                       'MANUFACTURING', 'https://linkedin.com/in/j', NULL,
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


class TestDrafterEnforcesSingleAsk:
    def test_multi_ask_triggers_regen(self, db_path):
        cid = _seed_one()
        client = _mk_client(
            [
                MULTI_ASK,  # CONN gen 1 — multi-ask
                SINGLE_ASK,  # CONN regen — clean
                "Clean follow-up.",  # POST
            ]
        )
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.body == SINGLE_ASK
        assert conn_draft.quality_code == "OK"
        assert "one ask" in client.captured_prompts[1].lower()

    def test_persistent_multi_ask_soft_flagged(self, db_path):
        cid = _seed_one()
        client = _mk_client(
            [
                MULTI_ASK,  # CONN gen 1
                MULTI_ASK,  # CONN regen — still multi-ask
                "Clean follow-up.",
            ]
        )
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.quality_code == "SOFT_FLAG"

    def test_prompt_carries_one_ask_rule(self, db_path):
        cid = _seed_one()
        client = _mk_client([SINGLE_ASK, "Clean follow-up."])
        draft_for_contacts([cid], anthropic_client=client)
        first_prompt = client.captured_prompts[0]
        assert "exactly ONE ask" in first_prompt
