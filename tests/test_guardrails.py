"""Tests for src/agents/guardrails.py"""

from __future__ import annotations

import pytest

from src.agents.guardrails import BLOCKLIST, check_draft


class TestCheckDraft:
    def test_clean_text_returns_none(self):
        # Use a phrase guaranteed not to overlap with either the seed
        # BLOCKLIST or any phrase commonly found under voice.md's
        # "## Forbidden Phrases" heading (which is merged in at import).
        assert check_draft("Saw your team's SAMPE paper on bonded composite repair — sharp work.") is None

    def test_i_noticed_detected(self):
        result = check_draft("I noticed your profile and wanted to connect.")
        assert result == "I noticed"

    def test_i_admire_detected(self):
        result = check_draft("I admire the work your team has done on composites.")
        assert result == "I admire"

    def test_came_across_detected(self):
        result = check_draft("I came across your company and was impressed.")
        assert result == "I came across your company"

    def test_impressive_work_detected(self):
        result = check_draft("Reaching out because of your impressive work at SpaceX.")
        assert result == "your impressive work"

    def test_case_insensitive(self):
        assert check_draft("i noticed your post.") == "I noticed"
        assert check_draft("YOUR IMPRESSIVE WORK is well known.") == "your impressive work"

    def test_returns_first_match(self):
        # Multiple phrases — should return whichever appears first in BLOCKLIST
        text = "I admire your impressive work and I noticed your profile."
        result = check_draft(text)
        # "I noticed" comes before "I admire" in BLOCKLIST... actually no,
        # BLOCKLIST order is: "I noticed", "I admire", ...
        # "I noticed" appears in the text, so that should be returned first
        assert result == "I noticed"

    def test_blocklist_covers_all_expected_phrases(self):
        expected = {"I noticed", "I admire", "I came across your company", "your impressive work"}
        assert expected.issubset(set(BLOCKLIST))

    @pytest.mark.parametrize("phrase", BLOCKLIST)
    def test_every_blocklist_phrase_is_detected(self, phrase):
        text = f"Some preamble. {phrase}. Some suffix."
        assert check_draft(text) == phrase
