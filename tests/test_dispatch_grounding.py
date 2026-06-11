"""
tests/test_dispatch_grounding.py
Layer 6c: REVISE prompts now ship the same persona + APPROVED FACTS +
FACT DISCIPLINE block as the first draft; HARD_FAIL and CRITIC_HOLD are
written to drafts.quality_code; the marketer gate then blocks them.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.agents.critic import RUBRIC_DIMENSIONS, SEVERE_SCORE
from src.agents.dispatch import _build_revision_prompt, dispatch_revision
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import Channel, DraftDispatchRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", Path(tmp_path / "x.db"))
    init_db()


def _seed_contact_with_draft(channel=Channel.LINKEDIN_CONNECTION):
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "linkedin_url, hook, state) "
            "VALUES (?, 'Alice', 'Composites Engineer', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://linkedin.com/in/alice', 'shared composites', 'DRAFTED')",
            (company_id,),
        )
        contact_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, "
            "quality_flag, quality_code) VALUES (?, ?, 'Initial body.', 1, 0, 'OK')",
            (contact_id, channel.value),
        )
        return contact_id, c.lastrowid


# ---------------------------------------------------------------------------
# _build_revision_prompt — must carry first-pass grounding
# ---------------------------------------------------------------------------


class TestRevisionPromptGrounding:
    def _contact(self) -> dict:
        return {
            "full_name": "Alice",
            "title": "Composites Engineer",
            "linkedin_url": "https://linkedin.com/in/alice",
            "email": "a@acme.com",
            "hook": "shared composites work",
            "persona": "PEER_ENGINEER",
        }

    def test_prompt_includes_approved_facts_and_discipline(self):
        prompt = _build_revision_prompt(
            self._contact(),
            Channel.COLD_EMAIL,
            bullets=[],
            persona_template="PERSONA TEMPLATE",
            voice_doc="VOICE DOC",
            prior_body="old draft",
            feedback="be more specific",
        )
        # First-pass grounding survives the revision prompt.
        assert "## APPROVED FACTS" in prompt
        assert "FACT DISCIPLINE" in prompt
        assert "No invented numbers" in prompt
        assert "No re-attribution" in prompt
        assert "No placeholders" in prompt

    def test_prompt_carries_persona_template_and_voice(self):
        prompt = _build_revision_prompt(
            self._contact(),
            Channel.COLD_EMAIL,
            bullets=[],
            persona_template="UNIQUE-PERSONA-TEMPLATE-MARKER",
            voice_doc="UNIQUE-VOICE-MARKER",
            prior_body="old",
            feedback="x",
        )
        assert "UNIQUE-PERSONA-TEMPLATE-MARKER" in prompt
        assert "UNIQUE-VOICE-MARKER" in prompt

    def test_prompt_includes_prior_body_and_feedback(self):
        prompt = _build_revision_prompt(
            self._contact(),
            Channel.COLD_EMAIL,
            bullets=[],
            persona_template="x",
            voice_doc="",
            prior_body="THE-PRIOR-BODY-MARKER",
            feedback="THE-FEEDBACK-MARKER",
        )
        assert "THE-PRIOR-BODY-MARKER" in prompt
        assert "THE-FEEDBACK-MARKER" in prompt
        # Task framing replaces the "Now write the message." tail.
        assert "Now write the message." not in prompt
        assert "Revise the draft" in prompt

    def test_anti_phrases_list_appears(self):
        prompt = _build_revision_prompt(
            self._contact(),
            Channel.COLD_EMAIL,
            bullets=[],
            persona_template="x",
            voice_doc="",
            prior_body="a",
            feedback="b",
            anti_phrases=["I noticed", "your impressive work"],
        )
        assert '"I noticed"' in prompt
        assert '"your impressive work"' in prompt


# ---------------------------------------------------------------------------
# dispatch_revision: quality_code persisted; HARD_FAIL → GUARDRAIL_FLAGGED
# ---------------------------------------------------------------------------


class _DispatchClient:
    """Mock that returns plain text for drafter calls and a configurable
    critic verdict for tool_use calls."""

    def __init__(self, texts: list[str], critic_scores: dict | None = None):
        self.texts = list(texts)
        self.critic_scores = critic_scores or {dim: 5 for dim in RUBRIC_DIMENSIONS}
        self.messages = Mock()
        self.messages.create.side_effect = self._create

    def _create(self, **kwargs):
        if "tools" in kwargs and kwargs.get("tools"):
            tool = Mock()
            tool.type = "tool_use"
            payload = {dim: self.critic_scores.get(dim, 5) for dim in RUBRIC_DIMENSIONS}
            payload["issues"] = []
            tool.input = payload
            resp = Mock()
            resp.content = [tool]
            return resp
        text = self.texts.pop(0) if self.texts else "fallback"
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg


class TestDispatchPersistsQualityCode:
    def test_hard_fail_revision_recorded_and_flagged(self):
        contact_id, draft_id = _seed_contact_with_draft()
        # Revision returns a placeholder → hard_check HARD_FAIL.
        client = _DispatchClient(texts=["Hi [RESEARCH_NEEDED]."])
        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="be more specific",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "GUARDRAIL_FLAGGED"
        assert resp.quality_flag is True

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT quality_code FROM drafts WHERE id = ?",
                (resp.new_draft_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["quality_code"] == "HARD_FAIL"

    def test_clean_revision_quality_code_ok(self):
        contact_id, draft_id = _seed_contact_with_draft()
        client = _DispatchClient(texts=["Clean, brief connection note."])
        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="shorter please",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"
        assert resp.quality_flag is False

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT quality_code FROM drafts WHERE id = ?",
                (resp.new_draft_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["quality_code"] == "OK"

    def test_critic_hold_revision_recorded(self):
        contact_id, draft_id = _seed_contact_with_draft()
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["specificity"] = SEVERE_SCORE  # AUDIT-A3: severe holds
        client = _DispatchClient(
            texts=["Generic revision."],
            critic_scores=bad,
        )
        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "GUARDRAIL_FLAGGED"
        assert resp.quality_flag is True

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT quality_code FROM drafts WHERE id = ?",
                (resp.new_draft_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["quality_code"] == "CRITIC_HOLD"
