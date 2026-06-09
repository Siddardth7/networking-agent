"""
tests/test_critic.py
Layer 4: critic rubric structure and verdict translation.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.critic import (
    MIN_SCORE,
    RUBRIC_DIMENSIONS,
    CriticResult,
    _build_tool_schema,
    critique_draft,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_critic_response(scores: dict, issues: list[str] | None = None) -> Mock:
    tool = Mock()
    tool.type = "tool_use"
    payload = {dim: scores.get(dim, 5) for dim in RUBRIC_DIMENSIONS}
    payload["issues"] = issues or []
    tool.input = payload
    resp = Mock()
    resp.content = [tool]
    return resp


def _no_tool_response() -> Mock:
    block = Mock()
    block.type = "text"
    block.text = "I refuse to use the tool."
    resp = Mock()
    resp.content = [block]
    return resp


def _contact() -> dict:
    return {
        "full_name": "Jane Doe",
        "title": "Stress Engineer",
        "persona": "PEER_ENGINEER",
        "hook": "your structures work",
    }


# ---------------------------------------------------------------------------
# Rubric schema sanity
# ---------------------------------------------------------------------------

class TestRubricSchema:
    def test_six_dimensions_defined(self):
        assert set(RUBRIC_DIMENSIONS) == {
            "specificity", "one_ask", "tone",
            "grounded_facts", "economy", "relevance",
        }

    def test_tool_schema_requires_every_dimension(self):
        schema = _build_tool_schema()
        required = set(schema["input_schema"]["required"])
        for dim in RUBRIC_DIMENSIONS:
            assert dim in required
        assert "issues" in required

    def test_each_dimension_is_int_0_to_5(self):
        schema = _build_tool_schema()
        props = schema["input_schema"]["properties"]
        for dim in RUBRIC_DIMENSIONS:
            assert props[dim]["type"] == "integer"
            assert props[dim]["minimum"] == 0
            assert props[dim]["maximum"] == 5


# ---------------------------------------------------------------------------
# Verdict translation
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_all_high_scores_pass(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        result = critique_draft(
            body="A genuine, specific note.", contact=_contact(),
            channel="LINKEDIN_CONNECTION", source_facts="Did X.",
            anthropic_client=client,
        )
        assert isinstance(result, CriticResult)
        assert result.passed is True
        assert result.quality_code == "OK"

    def test_any_dimension_below_min_holds(self):
        # Specificity tanks → CRITIC_HOLD even with strong scores elsewhere.
        client = Mock()
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["specificity"] = MIN_SCORE - 1
        client.messages.create.return_value = _make_critic_response(
            bad, issues=["specificity: generic eVTOL line, no real signal"],
        )
        result = critique_draft(
            body="Generic.", contact=_contact(),
            channel="COLD_EMAIL", source_facts="Did X.",
            anthropic_client=client,
        )
        assert result.passed is False
        assert result.quality_code == "CRITIC_HOLD"
        assert any("specificity" in i for i in result.issues)
        assert "specificity" in (result.reason or "")

    def test_min_score_exactly_is_passing(self):
        # Boundary: MIN_SCORE is the floor (≥ passes, < holds).
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: MIN_SCORE for dim in RUBRIC_DIMENSIONS},
        )
        result = critique_draft(
            body="OK note.", contact=_contact(),
            channel="LINKEDIN_CONNECTION", source_facts=None,
            anthropic_client=client,
        )
        assert result.passed is True

    def test_no_tool_block_fails_safe(self):
        # If the critic refuses to emit structured output, fail safe — HOLD.
        client = Mock()
        client.messages.create.return_value = _no_tool_response()
        result = critique_draft(
            body="x", contact=_contact(),
            channel="COLD_EMAIL", source_facts=None,
            anthropic_client=client,
        )
        assert result.passed is False
        assert result.quality_code == "CRITIC_HOLD"

    def test_unparseable_score_treated_as_zero(self):
        client = Mock()
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["tone"] = "not an int"
        client.messages.create.return_value = _make_critic_response(bad)
        result = critique_draft(
            body="x", contact=_contact(),
            channel="COLD_EMAIL", source_facts=None,
            anthropic_client=client,
        )
        # Unparseable becomes 0 — below MIN_SCORE → HOLD.
        assert result.passed is False
        assert result.scores["tone"] == 0

    def test_scores_clamped_into_range(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS} | {"specificity": 99},
        )
        result = critique_draft(
            body="x", contact=_contact(),
            channel="COLD_EMAIL", source_facts=None,
            anthropic_client=client,
        )
        assert result.scores["specificity"] == 5


# ---------------------------------------------------------------------------
# Prompt content sanity (the critic actually sees what it needs)
# ---------------------------------------------------------------------------

class TestPromptContent:
    def test_prompt_includes_recipient_and_channel(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        critique_draft(
            body="hello", contact=_contact(),
            channel="COLD_EMAIL", source_facts="Facts here.",
            anthropic_client=client, subject="My subject",
        )
        kwargs = client.messages.create.call_args.kwargs
        msg = kwargs["messages"][0]["content"]
        assert "Jane Doe" in msg
        assert "Stress Engineer" in msg
        assert "COLD_EMAIL" in msg
        assert "Facts here." in msg
        assert "My subject" in msg

    def test_uses_sonnet_model(self):
        from src.core.config import SONNET_MODEL
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        critique_draft(
            body="x", contact=_contact(),
            channel="COLD_EMAIL", source_facts=None,
            anthropic_client=client,
        )
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == SONNET_MODEL
