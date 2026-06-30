"""
tests/test_network_draft_host.py
Host-token drafting bridge CLI (#50): context | save, JSON in/out, no LLM.
"""

from __future__ import annotations

import argparse
import json

import pytest

from src.cli.network_draft_host import run_context, run_draft_host, run_save
from src.core.db import init_db, with_writer


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
            "VALUES ('acme', 'Acme Corp', 'SELECTED')"
        )
        co = conn.execute("SELECT id FROM companies WHERE slug='acme'").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "email, hook, state) VALUES (?,?,?,?,?,?,?, 'SELECTED')",
            (co, "Alice Smith", "Composites Engineer", "PEER_ENGINEER", "COMPOSITE_DESIGN",
             email, "your composites work"),
        )
        return int(cur.lastrowid)


def _args(**kw):
    base = {"verb": "context", "contact_id": 1, "channel": "COLD_EMAIL",
            "subject": None, "body": None}
    base.update(kw)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- #
# context
# --------------------------------------------------------------------------- #


class TestContext:
    def test_prints_grounding_json(self, capsys):
        cid = _seed_contact()
        rc = run_context(cid, "COLD_EMAIL")
        assert rc == 0
        ctx = json.loads(capsys.readouterr().out)
        assert ctx["contact"]["full_name"] == "Alice Smith"
        assert ctx["channel"] == "COLD_EMAIL"
        assert "channel_constraints" in ctx

    def test_unknown_channel(self, capsys):
        cid = _seed_contact()
        assert run_context(cid, "SMOKE") == 1
        assert "unknown channel" in json.loads(capsys.readouterr().out)["error"]

    def test_unknown_contact(self, capsys):
        assert run_context(999, "COLD_EMAIL") == 1
        assert "not found" in json.loads(capsys.readouterr().out)["error"]


# --------------------------------------------------------------------------- #
# save
# --------------------------------------------------------------------------- #


class TestSave:
    def test_persists_and_returns_json(self, capsys):
        cid = _seed_contact()
        rc = run_save(cid, "COLD_EMAIL", "Hi Alice, would value a quick chat.", "Hello")
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["quality_code"] == "OK"
        assert out["draft_id"] > 0
        assert out["subject"] == "Hello"

    def test_hard_fail_surfaced(self, capsys):
        cid = _seed_contact()
        run_save(cid, "COLD_EMAIL", "About [COMPANY].", None)
        assert json.loads(capsys.readouterr().out)["quality_code"] == "HARD_FAIL"

    def test_unknown_channel(self, capsys):
        cid = _seed_contact()
        assert run_save(cid, "NOPE", "body", None) == 1
        assert "unknown channel" in json.loads(capsys.readouterr().out)["error"]

    def test_empty_body(self, capsys):
        cid = _seed_contact()
        assert run_save(cid, "COLD_EMAIL", "   ", None) == 1
        assert "empty body" in json.loads(capsys.readouterr().out)["error"]


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #


class TestDispatch:
    def test_dispatch_context(self, capsys):
        cid = _seed_contact()
        run_draft_host(_args(verb="context", contact_id=cid))
        assert json.loads(capsys.readouterr().out)["contact"]["full_name"] == "Alice Smith"

    def test_dispatch_save_with_body_arg(self, capsys):
        cid = _seed_contact()
        run_draft_host(_args(verb="save", contact_id=cid, body="Hi Alice, quick chat?",
                             subject="Hey"))
        assert json.loads(capsys.readouterr().out)["draft_id"] > 0

    def test_dispatch_save_reads_stdin(self, capsys, monkeypatch):
        cid = _seed_contact()
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("Hi Alice from stdin."))
        run_draft_host(_args(verb="save", contact_id=cid, body=None))
        assert json.loads(capsys.readouterr().out)["quality_code"] == "OK"
