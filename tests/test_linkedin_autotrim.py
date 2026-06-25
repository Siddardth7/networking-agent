"""
Unit tests for the LinkedIn connection-note auto-trim (Finding A/B, v0.5.x).

The drafter gives an over-length LINKEDIN_CONNECTION note one corrective regen,
but the model can still come back marginally over the cap (observed live: 287 vs
280). `_trim_to_char_limit` is the deterministic last-resort trim that recovers
those drafts instead of HARD_FAILing them. These tests pin its contract.
"""

from __future__ import annotations

from src.agents.drafter import _trim_to_char_limit


class TestTrimToCharLimit:
    def test_under_limit_is_returned_unchanged(self):
        note = "Hi Varshit, would value connecting and hearing about your team."
        assert _trim_to_char_limit(note, 280) == note

    def test_strips_then_passes_through_when_within_limit(self):
        assert _trim_to_char_limit("  hello world  ", 280) == "hello world"

    def test_keeps_leading_whole_sentences_that_fit(self):
        text = "Aaaa bbbb cccc. Dddd eeee ffff gggg hhhh iiii."
        # Only the first sentence (15 chars) fits under 20.
        result = _trim_to_char_limit(text, 20)
        assert result == "Aaaa bbbb cccc."
        assert len(result) <= 20

    def test_word_boundary_fallback_when_first_sentence_too_long(self):
        # No sentence terminator: must fall back to word-boundary + ellipsis.
        text = "alpha beta gamma delta epsilon"
        result = _trim_to_char_limit(text, 12)
        assert result == "alpha beta…"
        assert len(result) <= 12

    def test_result_never_exceeds_limit_for_marginal_overage(self):
        # A realistic 287-char note must come back at or under 280.
        note = ("Hi Saurabh, I'm finishing an MS in Aerospace Engineering at UIUC "
                "with a composites and structures focus. ") + ("word " * 40)
        assert len(note) > 280
        result = _trim_to_char_limit(note, 280)
        assert 0 < len(result) <= 280

    def test_non_positive_limit_is_a_no_op(self):
        assert _trim_to_char_limit("anything here", 0) == "anything here"
