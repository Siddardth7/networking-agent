"""
tests/test_shared.py
Layer 6: src/agents/shared.py is the single source of truth for the
constants drafter.py and dispatch.py both depend on.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.shared import (
    CHANNEL_CONSTRAINTS,
    DEFAULT_TIMEOUT_SECONDS,
    call_claude,
    call_claude_with_timeout,
    parse_email_body_subject,
)
from src.core.schemas import Channel

# ---------------------------------------------------------------------------
# CHANNEL_CONSTRAINTS
# ---------------------------------------------------------------------------


class TestChannelConstraints:
    def test_covers_every_channel(self):
        assert set(CHANNEL_CONSTRAINTS.keys()) == set(Channel)

    def test_linkedin_uses_280_char_cutoff(self):
        text = CHANNEL_CONSTRAINTS[Channel.LINKEDIN_CONNECTION]
        # 280-char safe cutoff (under LinkedIn's real 300-char note cap, which
        # the text may reference); the legacy 200 value must be gone.
        assert "280" in text
        assert "200" not in text

    def test_email_uses_150_word_cap(self):
        text = CHANNEL_CONSTRAINTS[Channel.COLD_EMAIL]
        assert "150" in text

    def test_drafter_and_dispatch_share_same_constants(self):
        # Whatever they expose must literally BE this dict (no shadow copy).
        from src.agents import dispatch, drafter

        assert drafter._CHANNEL_CONSTRAINTS is CHANNEL_CONSTRAINTS
        assert dispatch._CHANNEL_CONSTRAINTS is CHANNEL_CONSTRAINTS


# ---------------------------------------------------------------------------
# parse_email_body_subject
# ---------------------------------------------------------------------------


class TestParseEmail:
    def test_subject_and_body_split(self):
        body, subject = parse_email_body_subject(
            "Subject: My subject\n\nHello there.\n\nMore body."
        )
        assert subject == "My subject"
        assert body.startswith("Hello there.")

    def test_no_subject_returns_none(self):
        body, subject = parse_email_body_subject("Just a body, no subject line.")
        assert subject is None
        assert body == "Just a body, no subject line."

    def test_case_insensitive_subject_marker(self):
        body, subject = parse_email_body_subject("SUBJECT: X\n\nbody")
        assert subject == "X"


# ---------------------------------------------------------------------------
# call_claude / call_claude_with_timeout
# ---------------------------------------------------------------------------


class TestCallClaude:
    def test_call_claude_returns_first_block_text(self):
        client = Mock()
        msg = Mock()
        msg.content = [Mock(text="  hello  ")]
        client.messages.create.return_value = msg
        out = call_claude("prompt", client)
        # Whitespace stripped.
        assert out == "hello"

    def test_call_claude_with_timeout_returns_immediately(self):
        client = Mock()
        msg = Mock()
        msg.content = [Mock(text="quick")]
        client.messages.create.return_value = msg
        assert call_claude_with_timeout("prompt", client, timeout=5.0) == "quick"

    def test_call_claude_with_timeout_raises_on_hang(self):
        client = Mock()

        def hang(**kwargs):
            import time

            time.sleep(2)
            return Mock(content=[Mock(text="late")])

        client.messages.create.side_effect = hang
        with pytest.raises(TimeoutError):
            call_claude_with_timeout("prompt", client, timeout=0.2)

    def test_default_timeout_constant_exists(self):
        # Sanity guard so dispatch can keep importing it.
        assert DEFAULT_TIMEOUT_SECONDS > 0
