"""
tests/test_next_move.py
Reply-aware next-move drafting (#19, A8): classifier + gated draft + CLI.
"""

from __future__ import annotations

import argparse
from unittest.mock import Mock

import pytest

from src.agents.drafter import (
    NextMoveDraft,
    classify_next_move,
    draft_next_move,
)
from src.cli.network_nextmove import run_nextmove
from src.core.db import init_db, with_writer
from src.core.schemas import NextMove, Outcome


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _make_anthropic(responses: list[str]):
    """Mock Anthropic client returning *responses* in order."""
    client = Mock()

    def _create(**kwargs):
        msg = Mock()
        msg.content = [Mock(text=responses.pop(0))]
        return msg

    client.messages.create.side_effect = _create
    return client


def _seed_contact(*, email: str | None = "a@acme.com", hook: str = "your composites work") -> int:
    with with_writer() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name, state) "
            "VALUES ('acme', 'Acme Corp', 'APPROVED')"
        )
        co = conn.execute("SELECT id FROM companies WHERE slug='acme'").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "email, hook, state) VALUES (?,?,?,?,?,?,?, 'SENT')",
            (co, "Alice Smith", "Composites Engineer", "PEER_ENGINEER", "COMPOSITE_DESIGN",
             email, hook),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# classify_next_move (pure)
# --------------------------------------------------------------------------- #


class TestClassify:
    def test_default_warm_reply_schedules_call(self):
        assert classify_next_move("Thanks for reaching out, nice to hear from you!") == (
            NextMove.SCHEDULE_CALL
        )

    def test_call_cue(self):
        assert classify_next_move("Happy to chat — when works for you?") == NextMove.SCHEDULE_CALL

    def test_sponsorship_cue(self):
        assert classify_next_move("Do you need visa sponsorship?") == (
            NextMove.SPONSORSHIP_QUESTION
        )

    def test_referral_cue(self):
        assert classify_next_move("We have an open role — you should apply.") == (
            NextMove.REFERRAL_ASK
        )

    def test_intro_cue(self):
        assert classify_next_move("I can connect you with our recruiter.") == NextMove.THANK_INTRO

    def test_poc_outcome_forces_thank_intro(self):
        # No intro language in the text, but the recorded outcome is POC.
        assert classify_next_move("ok", outcome=Outcome.POC.value) == NextMove.THANK_INTRO

    def test_precedence_intro_beats_sponsorship(self):
        assert classify_next_move("Happy to refer you — do you need sponsorship?") == (
            NextMove.THANK_INTRO
        )

    def test_precedence_sponsorship_beats_call(self):
        assert classify_next_move("Let's chat about visa sponsorship") == (
            NextMove.SPONSORSHIP_QUESTION
        )

    def test_precedence_call_beats_referral(self):
        assert classify_next_move("Let's hop on a call about the open role") == (
            NextMove.SCHEDULE_CALL
        )

    def test_word_boundary_no_false_positive(self):
        # "recall" must not trigger the "call" cue.
        assert classify_next_move("I recall your message; thanks again.") == (
            NextMove.SCHEDULE_CALL  # default, not via 'call'
        )

    def test_empty_reply_defaults(self):
        assert classify_next_move("") == NextMove.SCHEDULE_CALL


# --------------------------------------------------------------------------- #
# draft_next_move (DB + gated draft)
# --------------------------------------------------------------------------- #


class TestDraftNextMove:
    def test_unknown_contact_returns_none(self):
        client = _make_anthropic(["unused"])
        assert draft_next_move(999, "hi", anthropic_client=client) is None

    def test_email_contact_parses_subject(self):
        cid = _seed_contact(email="a@acme.com")
        client = _make_anthropic(["Subject: Quick chat?\n\nWould love 15 minutes."])
        d = draft_next_move(cid, "Happy to chat!", anthropic_client=client, enable_critic=False)
        assert isinstance(d, NextMoveDraft)
        assert d.move == NextMove.SCHEDULE_CALL
        assert d.subject == "Quick chat?"
        assert d.body == "Would love 15 minutes."
        assert d.quality_code == "OK"

    def test_no_email_uses_linkedin_thread_no_subject(self):
        cid = _seed_contact(email=None)
        client = _make_anthropic(["Glad to hear from you — free for a quick call this week?"])
        d = draft_next_move(cid, "Happy to chat!", anthropic_client=client, enable_critic=False)
        assert d.subject is None
        assert "call" in d.body.lower()

    def test_move_override(self):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Re\n\nWould the team sponsor a visa?"])
        d = draft_next_move(
            cid, "anything", anthropic_client=client, move=NextMove.SPONSORSHIP_QUESTION,
            enable_critic=False,
        )
        assert d.move == NextMove.SPONSORSHIP_QUESTION

    def test_outcome_biases_classification(self):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Thanks!\n\nAppreciate the intro."])
        d = draft_next_move(
            cid, "sure", anthropic_client=client, outcome=Outcome.POC.value, enable_critic=False
        )
        assert d.move == NextMove.THANK_INTRO

    def test_hard_fail_on_placeholder_is_redacted(self):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Hi\n\nReaching out about [COMPANY] role."])
        d = draft_next_move(cid, "Happy to chat", anthropic_client=client, enable_critic=False)
        assert d.quality_code == "HARD_FAIL"
        assert "[COMPANY]" not in d.body  # redacted
        assert d.critic_trace is not None  # hard-fail trace persisted

    def test_hard_fail_non_placeholder_not_redacted(self):
        # A length HARD_FAIL (cold email over the word cap) — no placeholder, so
        # the body is returned as-is (not run through redaction).
        cid = _seed_contact(email="a@acme.com")
        long_body = "word " * 200  # ~200 words > 150-word cap
        client = _make_anthropic([f"Subject: Hi\n\n{long_body}"])
        d = draft_next_move(cid, "Happy to chat", anthropic_client=client, enable_critic=False)
        assert d.quality_code == "HARD_FAIL"
        assert "word word" in d.body  # untouched, not redacted

    def test_critic_hold(self, monkeypatch):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Hi\n\nLet's find 15 minutes to talk."])

        held = Mock()
        held.passed = False
        held.quality_code = "CRITIC_HOLD"
        held.to_json = lambda: '{"verdict":"hold"}'
        monkeypatch.setattr("src.agents.drafter.critique_draft", lambda **k: held)

        d = draft_next_move(cid, "Happy to chat", anthropic_client=client, enable_critic=True)
        assert d.quality_code == "CRITIC_HOLD"
        assert d.critic_trace == '{"verdict":"hold"}'

    def test_critic_pass_records_trace(self, monkeypatch):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Hi\n\nLet's find 15 minutes to talk."])

        ok = Mock()
        ok.passed = True
        ok.quality_code = "OK"
        ok.to_json = lambda: '{"verdict":"pass"}'
        monkeypatch.setattr("src.agents.drafter.critique_draft", lambda **k: ok)

        d = draft_next_move(cid, "Happy to chat", anthropic_client=client, enable_critic=True)
        assert d.quality_code == "OK"
        assert d.critic_trace == '{"verdict":"pass"}'

    def test_critic_failopen_on_exception(self, monkeypatch):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Hi\n\nLet's find 15 minutes to talk."])

        def _boom(**k):
            raise RuntimeError("sonnet down")

        monkeypatch.setattr("src.agents.drafter.critique_draft", _boom)
        d = draft_next_move(cid, "Happy to chat", anthropic_client=client, enable_critic=True)
        assert d.quality_code == "OK"  # fail-open: hard_check is the safety net
        assert d.critic_trace is None


# --------------------------------------------------------------------------- #
# CLI run_nextmove
# --------------------------------------------------------------------------- #


def _args(**kw):
    base = {"contact_id": 1, "reply": "Happy to chat", "move": None, "channel": None,
            "outcome": None}
    base.update(kw)
    return argparse.Namespace(**base)


class TestCLI:
    def test_empty_reply(self, capsys):
        assert run_nextmove(_args(reply="   "), anthropic_client=_make_anthropic([])) == 1
        assert "Provide the contact's reply" in capsys.readouterr().out

    def test_invalid_move(self, capsys):
        assert run_nextmove(_args(move="BOGUS"), anthropic_client=_make_anthropic([])) == 1
        assert "Invalid --move" in capsys.readouterr().out

    def test_invalid_channel(self, capsys):
        assert run_nextmove(_args(channel="SMOKE"), anthropic_client=_make_anthropic([])) == 1
        assert "Invalid --channel" in capsys.readouterr().out

    def test_contact_not_found(self, capsys):
        client = _make_anthropic(["Subject: Hi\n\nbody"])
        assert run_nextmove(_args(contact_id=999), anthropic_client=client) == 1
        assert "Contact not found" in capsys.readouterr().out

    def test_happy_path_prints_move_and_body(self, capsys):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Quick chat?\n\nGot 15 minutes this week?"])
        rc = run_nextmove(
            _args(contact_id=cid, reply="Happy to chat"), anthropic_client=client
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Next move: SCHEDULE_CALL" in out
        assert "Subject: Quick chat?" in out
        assert "Got 15 minutes this week?" in out

    def test_move_and_channel_override(self, capsys):
        cid = _seed_contact()
        client = _make_anthropic(["Would the team sponsor a visa for this role?"])
        rc = run_nextmove(
            _args(
                contact_id=cid,
                reply="anything",
                move="sponsorship_question",
                channel="linkedin_post_connection",
            ),
            anthropic_client=client,
        )
        assert rc == 0
        assert "Next move: SPONSORSHIP_QUESTION" in capsys.readouterr().out

    def test_builds_client_when_not_injected(self, monkeypatch, capsys):
        cid = _seed_contact()
        client = _make_anthropic(["Subject: Hi\n\nGot 15 minutes?"])
        monkeypatch.setattr(
            "src.cli.network_nextmove.get_anthropic_client", lambda: client
        )
        assert run_nextmove(_args(contact_id=cid, reply="Happy to chat")) == 0
