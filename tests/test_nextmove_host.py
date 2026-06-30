"""
tests/test_nextmove_host.py
Host-token next-move bridge (#50): build_next_move_context + gate_host_text +
the network_nextmove_host CLI (context | gate). No LLM calls.
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

from src.agents.drafter import build_next_move_context, gate_host_text
from src.cli.network_nextmove_host import run_context, run_gate, run_nextmove_host
from src.core.db import init_db, with_writer
from src.core.schemas import Channel, NextMove


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _seed_contact(email="a@acme.com") -> int:
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
             email, "your composites work"),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# build_next_move_context (pure)
# --------------------------------------------------------------------------- #


class TestBuildNextMoveContext:
    def test_unknown_contact(self):
        assert build_next_move_context(999, "hi") is None

    def test_classifies_move_and_grounds(self):
        cid = _seed_contact()
        ctx = build_next_move_context(cid, "Happy to chat — when works?")
        assert ctx["move"] == "SCHEDULE_CALL"
        assert ctx["move_instruction"]
        assert ctx["reply"].startswith("Happy to chat")
        assert ctx["contact"]["company"] == "Acme Corp"
        assert ctx["channel"] == "COLD_EMAIL"  # has email

    def test_move_override(self):
        cid = _seed_contact()
        ctx = build_next_move_context(cid, "x", move=NextMove.SPONSORSHIP_QUESTION)
        assert ctx["move"] == "SPONSORSHIP_QUESTION"

    def test_no_email_uses_linkedin_thread(self):
        cid = _seed_contact(email=None)
        ctx = build_next_move_context(cid, "thanks")
        assert ctx["channel"] == "LINKEDIN_POST_CONNECTION"


# --------------------------------------------------------------------------- #
# gate_host_text (pure deterministic gate)
# --------------------------------------------------------------------------- #


class TestGateHostText:
    def test_clean_text_ok(self):
        out = gate_host_text("Hi Alice, would value a quick chat.", Channel.COLD_EMAIL)
        assert out["quality_code"] == "OK"
        assert out["critic_trace"] is None

    def test_placeholder_hard_fail_redacted(self):
        out = gate_host_text("About [COMPANY].", Channel.COLD_EMAIL)
        assert out["quality_code"] == "HARD_FAIL"
        assert "[COMPANY]" not in out["body"]
        assert out["critic_trace"] is not None

    def test_length_hard_fail(self):
        out = gate_host_text("word " * 100, Channel.LINKEDIN_CONNECTION)
        assert out["quality_code"] == "HARD_FAIL"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _ctx_args(**kw):
    base = {"verb": "context", "contact_id": 1, "reply": "Happy to chat",
            "move": None, "channel": None, "outcome": None}
    base.update(kw)
    return argparse.Namespace(**base)


class TestCLIContext:
    def test_prints_json(self, capsys):
        cid = _seed_contact()
        assert run_context(_ctx_args(contact_id=cid)) == 0
        assert json.loads(capsys.readouterr().out)["move"] == "SCHEDULE_CALL"

    def test_unknown_move(self, capsys):
        cid = _seed_contact()
        assert run_context(_ctx_args(contact_id=cid, move="BOGUS")) == 1
        assert "unknown move" in json.loads(capsys.readouterr().out)["error"]

    def test_unknown_channel(self, capsys):
        cid = _seed_contact()
        assert run_context(_ctx_args(contact_id=cid, channel="SMOKE")) == 1
        assert "unknown channel" in json.loads(capsys.readouterr().out)["error"]

    def test_empty_reply(self, capsys):
        cid = _seed_contact()
        assert run_context(_ctx_args(contact_id=cid, reply="  ")) == 1
        assert "empty reply" in json.loads(capsys.readouterr().out)["error"]

    def test_unknown_contact(self, capsys):
        assert run_context(_ctx_args(contact_id=999)) == 1
        assert "not found" in json.loads(capsys.readouterr().out)["error"]

    def test_valid_move_and_channel_overrides(self, capsys):
        cid = _seed_contact()
        rc = run_context(_ctx_args(
            contact_id=cid, move="sponsorship_question", channel="linkedin_post_connection"
        ))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["move"] == "SPONSORSHIP_QUESTION"
        assert out["channel"] == "LINKEDIN_POST_CONNECTION"


class TestCLIGate:
    def test_ok(self, capsys):
        assert run_gate("COLD_EMAIL", "Hi Alice, quick chat?") == 0
        assert json.loads(capsys.readouterr().out)["quality_code"] == "OK"

    def test_unknown_channel(self, capsys):
        assert run_gate("NOPE", "body") == 1
        assert "unknown channel" in json.loads(capsys.readouterr().out)["error"]

    def test_empty_body(self, capsys):
        assert run_gate("COLD_EMAIL", "   ") == 1
        assert "empty body" in json.loads(capsys.readouterr().out)["error"]


class TestDispatch:
    def test_dispatch_context(self, capsys):
        cid = _seed_contact()
        run_nextmove_host(_ctx_args(contact_id=cid))
        assert json.loads(capsys.readouterr().out)["move"] == "SCHEDULE_CALL"

    def test_dispatch_gate_stdin(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("Hi Alice, quick chat?"))
        run_nextmove_host(argparse.Namespace(verb="gate", channel="COLD_EMAIL", body=None))
        assert json.loads(capsys.readouterr().out)["quality_code"] == "OK"

    def test_dispatch_gate_body_arg(self, capsys):
        run_nextmove_host(
            argparse.Namespace(verb="gate", channel="COLD_EMAIL", body="Hi Alice, chat?")
        )
        assert json.loads(capsys.readouterr().out)["quality_code"] == "OK"
