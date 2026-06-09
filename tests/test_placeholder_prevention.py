"""
tests/test_placeholder_prevention.py
AUDIT-A1 / AUDIT-A2 / AUDIT-A9: the generator must be re-prompted when it
emits a placeholder token, placeholder bodies must never be serialized
verbatim, and every HARD_FAIL draft must carry a populated held reason.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from src.agents.drafter import draft_for_contacts
from src.agents.guardrails import find_placeholder, redact_placeholders
from src.core.db import get_connection, init_db, with_writer


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    from src.core.config import Config, load_config
    real = load_config

    def _no_critic_cfg():
        cfg = real()
        return Config(
            anthropic_api_key=cfg.anthropic_api_key,
            serper_api_key=cfg.serper_api_key,
            hunter_api_key=cfg.hunter_api_key,
            serper_monthly_limit=cfg.serper_monthly_limit,
            hunter_monthly_limit=cfg.hunter_monthly_limit,
            finder_limit=cfg.finder_limit,
            linkedin_char_limit=cfg.linkedin_char_limit,
            email_word_limit=cfg.email_word_limit,
            batch_hard_fail_threshold=cfg.batch_hard_fail_threshold,
            enable_critic=False,
        )

    monkeypatch.setattr("src.agents.drafter.load_config", _no_critic_cfg)
    return path


def _seed_one_contact() -> int:
    """Insert one SELECTED contact with no email (2 channels drafted)."""
    init_db()
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        company_id = cursor.lastrowid
        c = conn.execute(
            """INSERT INTO contacts
               (company_id, full_name, title, persona, focus_area, linkedin_url,
                email, hook, state)
               VALUES (?, 'Nathan Test', 'Manufacturing Engineer', 'PEER_ENGINEER',
                       'MANUFACTURING', 'https://linkedin.com/in/nathan', NULL,
                       'your manufacturing work', 'SELECTED')""",
            (company_id,),
        )
        return c.lastrowid


def _mk_client(responses: list[str]):
    client = Mock()
    captured_prompts: list[str] = []

    def _create(**kwargs):
        captured_prompts.append(kwargs["messages"][0]["content"])
        text = responses.pop(0)
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg

    client.messages.create.side_effect = _create
    client.captured_prompts = captured_prompts
    return client


PLACEHOLDER_NOTE = "work at [" + "RESEARCH_NEEDED]"  # avoid tripping repo grep gate


class TestUpstreamPrevention:
    """A1 — placeholder in the first generation triggers exactly one regen."""

    def test_placeholder_first_gen_triggers_regen_with_instruction(self, db_path):
        cid = _seed_one_contact()
        client = _mk_client([
            f"Saw your {PLACEHOLDER_NOTE}. Would value connecting.",  # CONN gen 1
            "Saw your manufacturing work at Acme. Would value connecting.",  # CONN regen
            "Clean follow-up message.",  # POST gen 1
        ])
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(
            d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION"
        )
        assert conn_draft.quality_code == "OK"
        assert find_placeholder(conn_draft.body) is None
        # The regen prompt must carry an explicit anti-placeholder instruction.
        regen_prompt = client.captured_prompts[1]
        assert "placeholder" in regen_prompt.lower()

    def test_clean_first_gen_does_not_regen(self, db_path):
        cid = _seed_one_contact()
        client = _mk_client([
            "Clean connection note.",
            "Clean follow-up message.",
        ])
        draft_for_contacts([cid], anthropic_client=client)
        assert client.messages.create.call_count == 2


class TestNeverSerializePlaceholders:
    """A2 — a body that still contains a placeholder after regen is
    HARD_FAILed AND redacted before it reaches the DB."""

    def test_persistent_placeholder_is_redacted_in_db(self, db_path):
        cid = _seed_one_contact()
        client = _mk_client([
            f"Saw your {PLACEHOLDER_NOTE}.",  # CONN gen 1
            f"Still mentioning {PLACEHOLDER_NOTE}.",  # CONN regen — still dirty
            "Clean follow-up message.",  # POST gen 1
        ])
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(
            d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION"
        )
        assert conn_draft.quality_code == "HARD_FAIL"
        assert find_placeholder(conn_draft.body) is None
        assert "placeholder removed" in conn_draft.body

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT body FROM drafts WHERE contact_id = ? AND "
                "channel = 'LINKEDIN_CONNECTION'",
                (cid,),
            ).fetchone()
        finally:
            conn.close()
        assert find_placeholder(row["body"]) is None

    def test_redact_placeholders_helper(self):
        dirty = f"Loved your {PLACEHOLDER_NOTE} and [TEAM] efforts."
        clean = redact_placeholders(dirty)
        assert find_placeholder(clean) is None
        assert clean.count("(placeholder removed)") == 2


class TestHardFailReasonPersisted:
    """A9 — HARD_FAIL drafts carry a populated trace with the reason."""

    def test_hard_fail_trace_has_reason(self, db_path):
        cid = _seed_one_contact()
        over_length = "x" * 250  # over the 200-char LinkedIn cap
        client = _mk_client([
            over_length,  # CONN gen 1 (no soft faults → no regen)
            "Clean follow-up message.",  # POST gen 1
        ])
        results = draft_for_contacts([cid], anthropic_client=client)
        conn_draft = next(
            d for d in results[cid] if d.channel == "LINKEDIN_CONNECTION"
        )
        assert conn_draft.quality_code == "HARD_FAIL"
        assert conn_draft.critic_trace is not None
        trace = json.loads(conn_draft.critic_trace)
        assert trace["quality_code"] == "HARD_FAIL"
        assert trace["reason"]
        assert "250 chars" in trace["reason"]


class TestHeldReasonRendering:
    """A9 — reviewers see WHY a draft was held, in both renderers."""

    def test_artifact_formatter_renders_held_because(self):
        from src.agents.artifact_writer import _format_critic_trace
        from src.agents.critic import hard_fail_trace

        out = _format_critic_trace(
            hard_fail_trace("LinkedIn note is 250 chars (limit 200)")
        )
        assert out is not None
        assert "Held because:" in out
        assert "250 chars" in out

    def test_marketer_formatter_renders_held_because(self):
        from src.agents.critic import CriticResult
        from src.agents.marketer import _format_critic_for_reviewer

        trace = CriticResult(
            passed=False,
            quality_code="CRITIC_HOLD",
            scores={"specificity": 1},
            issues=["specificity: generic opener"],
            reason="critic flagged 1 dimension(s) below 3: specificity",
        ).to_json()
        out = _format_critic_for_reviewer(trace)
        assert out is not None
        assert "Held because:" in out
        assert "specificity" in out
