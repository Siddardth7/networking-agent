"""
tests/test_critic.py
Layer 4: critic rubric structure and verdict translation.
"""

from __future__ import annotations

from unittest.mock import Mock

from src.agents.critic import (
    MIN_SCORE,
    RUBRIC_DIMENSIONS,
    SEVERE_SCORE,
    CriticResult,
    _build_tool_schema,
    critique_draft,
    scan_ai_tells,
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
            "specificity",
            "one_ask",
            "tone",
            "grounded_facts",
            "economy",
            "relevance",
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
            body="A genuine, specific note.",
            contact=_contact(),
            channel="LINKEDIN_CONNECTION",
            source_facts="Did X.",
            anthropic_client=client,
        )
        assert isinstance(result, CriticResult)
        assert result.passed is True
        assert result.quality_code == "OK"

    def test_severe_dimension_holds(self):
        # AUDIT-A3 recalibration: a single SEVERE score (<= 1) holds even
        # with strong scores elsewhere; a single borderline 2 now passes.
        client = Mock()
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["specificity"] = SEVERE_SCORE
        client.messages.create.return_value = _make_critic_response(
            bad,
            issues=["specificity: generic eVTOL line, no real signal"],
        )
        result = critique_draft(
            body="Generic.",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Did X.",
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
            body="OK note.",
            contact=_contact(),
            channel="LINKEDIN_CONNECTION",
            source_facts=None,
            anthropic_client=client,
        )
        assert result.passed is True

    def test_no_tool_block_fails_safe(self):
        # If the critic refuses to emit structured output, fail safe — HOLD.
        client = Mock()
        client.messages.create.return_value = _no_tool_response()
        result = critique_draft(
            body="x",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts=None,
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
            body="x",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts=None,
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
            body="x",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts=None,
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
            body="hello",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Facts here.",
            anthropic_client=client,
            subject="My subject",
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
            body="x",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts=None,
            anthropic_client=client,
        )
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == SONNET_MODEL


# ---------------------------------------------------------------------------
# Anti-AI-detection scanner + gate integration (moat thread, issue #6)
# ---------------------------------------------------------------------------

# Human-grade drafts in Sid's 4-part voice (Intro -> Source -> Hook -> Close).
# These MUST stay clean — precision is the whole point (acceptance: human passes).
_HUMAN_DRAFTS = [
    "Saw your wing-box stress work at Joby. I'm finishing an MS in composites at "
    "UIUC and have been digging into bonded-repair fatigue. Would value 15 minutes "
    "on how your team handles it.",
    "Your talk on additive bracket qualification stuck with me. I did powder-bed "
    "fusion trials on Ti-6Al-4V for a senior project. Open to a quick chat about "
    "how Relativity approaches process control?",
    "We overlapped at Boeing structures before I went back for grad school. I'm "
    "targeting eVTOL loads roles now. Could you point me to who owns airframe "
    "loads on your team?",
]

_AI_DRAFTS = [
    ("filler opener", "I hope this message finds you well. I came across your "
     "profile and wanted to reach out."),
    ("buzzword", "I would love to leverage my passion for innovation and delve "
     "into synergies with your team."),
    ("cover-letter", "As a results-driven engineer, I am writing to express my "
     "interest. It is a testament to your work."),
]


class TestScanAITells:
    def test_empty_text_is_clean(self):
        assert scan_ai_tells("") == []

    def test_human_grade_drafts_are_clean(self):
        for draft in _HUMAN_DRAFTS:
            assert scan_ai_tells(draft) == [], f"false positive on: {draft!r}"

    def test_detects_filler_opener(self):
        tells = scan_ai_tells("I hope this message finds you well.")
        assert any("filler opener" in t for t in tells)

    def test_detects_multiple_tells(self):
        body = (
            "I hope this finds you well. I came across your profile. As a "
            "passionate engineer, I would love the opportunity to connect."
        )
        assert len(scan_ai_tells(body)) >= 3

    def test_known_ai_drafts_all_flagged(self):
        for _label, draft in _AI_DRAFTS:
            assert scan_ai_tells(draft), f"missed AI draft: {draft!r}"


class TestAntiAIDetectionGate:
    def test_clean_human_draft_passes(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS}
        )
        result = critique_draft(
            body=_HUMAN_DRAFTS[0],
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Did composites work.",
            anthropic_client=client,
        )
        assert result.passed is True
        assert result.quality_code == "OK"

    def test_ai_tell_holds_even_with_perfect_scores(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS}
        )
        result = critique_draft(
            body="I came across your profile and wanted to reach out.",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Did X.",
            anthropic_client=client,
        )
        assert result.passed is False
        assert result.quality_code == "CRITIC_HOLD"
        assert any("ai_detection" in i for i in result.issues)
        assert "AI-detection tells" in (result.reason or "")

    def test_tell_in_subject_holds(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS}
        )
        result = critique_draft(
            body=_HUMAN_DRAFTS[0],
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Did X.",
            anthropic_client=client,
            subject="Excited to connect about structures",
        )
        assert result.passed is False
        assert any("ai_detection" in i for i in result.issues)

    def test_reason_combines_score_and_tell(self):
        client = Mock()
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["specificity"] = SEVERE_SCORE
        client.messages.create.return_value = _make_critic_response(bad)
        result = critique_draft(
            body="I hope this message finds you well.",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Did X.",
            anthropic_client=client,
        )
        assert result.passed is False
        assert "dimension(s)" in (result.reason or "")
        assert "AI-detection tells" in (result.reason or "")

    def test_duplicate_tell_in_body_and_subject_deduped(self):
        client = Mock()
        client.messages.create.return_value = _make_critic_response(
            {dim: 5 for dim in RUBRIC_DIMENSIONS}
        )
        result = critique_draft(
            body="I came across your profile.",
            contact=_contact(),
            channel="COLD_EMAIL",
            source_facts="Did X.",
            anthropic_client=client,
            subject="I came across your profile",
        )
        # Same tell in body + subject -> surfaced once (dedup), not duplicated.
        assert len([i for i in result.issues if "cold-open" in i]) == 1
