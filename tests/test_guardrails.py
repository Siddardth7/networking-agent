"""Tests for src/agents/guardrails.py"""

from __future__ import annotations

import pytest

from src.agents.guardrails import BLOCKLIST, check_draft


class TestCheckDraft:
    def test_clean_text_returns_none(self):
        # Use a phrase guaranteed not to overlap with either the seed
        # BLOCKLIST or any phrase commonly found under voice.md's
        # "## Forbidden Phrases" heading (which is merged in at import).
        assert (
            check_draft("Saw your team's SAMPE paper on bonded composite repair — sharp work.")
            is None
        )

    def test_i_noticed_detected(self):
        result = check_draft("I noticed your profile and wanted to connect.")
        assert result == "I noticed"

    def test_specific_admiration_not_hard_blocked(self):
        # "I admire" is no longer a blunt hard-ban — specific admiration is a
        # valid hook (the critic judges specificity). Generic tells still fail.
        assert check_draft("I admire your work on the 787 empennage program.") is None

    def test_came_across_detected(self):
        result = check_draft("I came across your company and was impressed.")
        assert result == "I came across your company"

    def test_impressive_work_detected(self):
        result = check_draft("Reaching out because of your impressive work at SpaceX.")
        assert result == "your impressive work"

    def test_exactly_the_kind_of_detected(self):
        result = check_draft("Your background is exactly the kind of expertise we value.")
        assert result == "exactly the kind of"

    def test_case_insensitive(self):
        assert check_draft("i noticed your post.") == "I noticed"
        assert check_draft("YOUR IMPRESSIVE WORK is well known.") == "your impressive work"

    def test_returns_first_match(self):
        # Multiple phrases — should return whichever appears first in BLOCKLIST
        text = "your impressive work and I noticed your profile."
        result = check_draft(text)
        # BLOCKLIST order is "I noticed" before "your impressive work", so the
        # iteration returns "I noticed" first even though it appears later.
        assert result == "I noticed"

    def test_blocklist_covers_all_expected_phrases(self):
        expected = {
            "I noticed",
            "I came across your company",
            "your impressive work",
            "exactly the kind of",
        }
        assert expected.issubset(set(BLOCKLIST))

    @pytest.mark.parametrize("phrase", BLOCKLIST)
    def test_every_blocklist_phrase_is_detected(self, phrase):
        text = f"Some preamble. {phrase}. Some suffix."
        assert check_draft(text) == phrase
