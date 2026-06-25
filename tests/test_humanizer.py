"""Tests for the deterministic humanizer pass."""

from src.agents.humanizer import humanize


class TestExactlyFamily:
    def test_exactly_the_kind_of_stripped(self):
        assert (
            humanize("that's exactly the kind of work I want")
            == "that's the kind of work I want"
        )

    def test_exactly_the_direction_stripped(self):
        assert (
            humanize("your role is exactly the direction I'm headed")
            == "your role is the direction I'm headed"
        )

    def test_type_and_sort_variants(self):
        assert humanize("exactly the type of role") == "the type of role"
        assert humanize("exactly the sort of team") == "the sort of team"

    def test_capitalization_preserved_at_sentence_start(self):
        # "Exactly" capitalized → the following word carries the capital.
        assert humanize("Exactly the kind of work.") == "The kind of work."

    def test_case_insensitive_match(self):
        assert humanize("EXACTLY the kind of") == "the kind of"

    def test_idempotent(self):
        once = humanize("it's exactly the kind of thing")
        assert humanize(once) == once

    def test_clean_text_unchanged(self):
        clean = "I came across your profile while researching composites."
        assert humanize(clean) == clean

    def test_does_not_touch_unrelated_exactly(self):
        # "exactly" not followed by the noun-phrase family is left alone.
        assert humanize("that is exactly right") == "that is exactly right"

    def test_empty_and_none_safe(self):
        assert humanize("") == ""
