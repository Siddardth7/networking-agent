"""
tests/test_guardrails_hard_check.py
Layer 3+5: tests for `hard_check` (placeholders, numeric provenance, length)
and voice.md merging into the soft blocklist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.guardrails import (
    HardCheckResult,
    _build_blocklist,
    _load_voice_forbidden_phrases,
    hard_check,
)


# ---------------------------------------------------------------------------
# hard_check: bracket / placeholder detection
# ---------------------------------------------------------------------------

class TestBracketCheck:
    def test_research_needed_is_hard_fail(self):
        text = "Saw your post on [RESEARCH_NEEDED] — wanted to learn more."
        result = hard_check(text)
        assert result.passed is False
        assert result.quality_code == "HARD_FAIL"
        assert "RESEARCH_NEEDED" in (result.reason or "")

    def test_generic_uppercase_placeholder_blocked(self):
        result = hard_check("Reaching out about [COMPANY] role.")
        assert result.passed is False
        assert result.quality_code == "HARD_FAIL"

    def test_clean_text_passes(self):
        result = hard_check("Brief, specific note grounded in real facts.")
        assert result.passed is True
        assert result.quality_code == "OK"

    def test_lowercase_brackets_ignored(self):
        # [reasonable] could be a markdown footnote / citation, not a placeholder.
        result = hard_check("See the paper [smith2023] for context.")
        assert result.passed is True


# ---------------------------------------------------------------------------
# hard_check: numeric provenance
# ---------------------------------------------------------------------------

class TestNumericProvenance:
    def test_unsourced_percent_metric_blocked(self):
        # Draft claims 12% — but source_facts mention no metric like that.
        result = hard_check(
            "We hit 12% weight savings on the bracket.",
            source_facts="Designed a composite bracket; passed FAA cert testing.",
            channel="COLD_EMAIL",
        )
        assert result.passed is False
        assert result.quality_code == "HARD_FAIL"
        assert "12" in (result.reason or "")

    def test_sourced_metric_allowed(self):
        result = hard_check(
            "We hit 12% weight savings on the bracket.",
            source_facts="Composite bracket project achieved 12% weight reduction vs baseline.",
            channel="COLD_EMAIL",
        )
        assert result.passed is True

    def test_plus_suffix_metric_checked(self):
        # "15+ load cases" must appear in facts.
        result = hard_check(
            "Ran 15+ load cases on the wing model.",
            source_facts="Stress analysis on coursework wing model.",
            channel="COLD_EMAIL",
        )
        assert result.passed is False

    def test_time_reference_not_a_metric(self):
        # "15 minutes" has no % or + adjacent — should NOT trip the metric check.
        result = hard_check(
            "Happy to grab 15 minutes when convenient.",
            source_facts="Some unrelated facts.",
            channel="COLD_EMAIL",
        )
        assert result.passed is True

    def test_no_source_facts_skips_provenance_check(self):
        # When we have no facts loaded, we can't verify — so we don't block.
        result = hard_check(
            "Built a fixture that gave us 40% cycle-time reduction.",
            source_facts=None,
            channel="COLD_EMAIL",
        )
        assert result.passed is True


# ---------------------------------------------------------------------------
# hard_check: length
# ---------------------------------------------------------------------------

class TestLengthCheck:
    def test_linkedin_over_200_chars_hard_fail(self):
        text = "a" * 201
        result = hard_check(text, channel="LINKEDIN_CONNECTION", linkedin_char_limit=200)
        assert result.passed is False
        assert "201" in (result.reason or "")
        assert "200" in (result.reason or "")

    def test_linkedin_exactly_200_chars_ok(self):
        result = hard_check("a" * 200, channel="LINKEDIN_CONNECTION", linkedin_char_limit=200)
        assert result.passed is True

    def test_cold_email_over_word_limit_hard_fail(self):
        text = " ".join(["word"] * 151)
        result = hard_check(text, channel="COLD_EMAIL", email_word_limit=150)
        assert result.passed is False
        assert "151" in (result.reason or "")

    def test_post_connection_no_length_check(self):
        # LINKEDIN_POST_CONNECTION has no enforced cap (relationship-building tone).
        text = "a" * 5000
        result = hard_check(text, channel="LINKEDIN_POST_CONNECTION")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Voice.md parsing and BLOCKLIST merging
# ---------------------------------------------------------------------------

class TestVoiceMdMerge:
    def test_load_forbidden_phrases_from_voice_md(self, tmp_path: Path):
        voice = tmp_path / "voice.md"
        voice.write_text(
            "# Voice Guide\n\n"
            "## Tone\n\n- Be direct.\n\n"
            "## Forbidden Phrases\n\n"
            '- "I wanted to reach out"\n'
            "- circle back\n"
            "- touch base — outdated jargon\n\n"
            "## Next Section\n\nstuff\n"
        )
        phrases = _load_voice_forbidden_phrases(voice)
        assert "I wanted to reach out" in phrases
        assert "circle back" in phrases
        assert "touch base" in phrases  # comment after — stripped

    def test_missing_voice_file_returns_empty(self, tmp_path: Path):
        phrases = _load_voice_forbidden_phrases(tmp_path / "absent.md")
        assert phrases == []

    def test_blocklist_merges_seed_and_voice(self, tmp_path: Path):
        voice = tmp_path / "voice.md"
        voice.write_text(
            "## Forbidden Phrases\n\n- custom-voice-phrase\n"
        )
        bl = _build_blocklist(voice)
        # Seed phrases always present.
        assert "I noticed" in bl
        assert "I admire" in bl
        # Voice-supplied phrase joined in.
        assert "custom-voice-phrase" in bl

    def test_blocklist_dedupes_case_insensitive(self, tmp_path: Path):
        voice = tmp_path / "voice.md"
        # "I noticed" is in seed; voice repeats it with different casing.
        voice.write_text("## Forbidden Phrases\n\n- i noticed\n")
        bl = _build_blocklist(voice)
        # Only one instance survives.
        lowered = [p.lower() for p in bl]
        assert lowered.count("i noticed") == 1


# ---------------------------------------------------------------------------
# HardCheckResult shape
# ---------------------------------------------------------------------------

class TestResultShape:
    def test_ok_result_fields(self):
        r = hard_check("clean text")
        assert isinstance(r, HardCheckResult)
        assert r.passed is True
        assert r.reason is None
        assert r.quality_code == "OK"

    def test_failed_result_carries_reason(self):
        r = hard_check("oops [RESEARCH_NEEDED]")
        assert r.passed is False
        assert r.quality_code == "HARD_FAIL"
        assert r.reason  # non-empty
