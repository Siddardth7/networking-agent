"""
tests/test_critic_host.py
Host-token critic seam (#50): apply_critique + build_critique_context (pure) and
the network_critic_host CLI bridge (context | apply). No LLM.
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

from src.agents.critic import (
    RUBRIC_DIMENSIONS,
    apply_critique,
    build_critique_context,
)
from src.cli.network_critic_host import run_apply, run_context, run_critic_host
from src.core.db import get_connection, init_db, with_writer

# --------------------------------------------------------------------------- #
# apply_critique (pure verdict)
# --------------------------------------------------------------------------- #


def _good_scores(**kw):
    base = {d: 4 for d in RUBRIC_DIMENSIONS}
    base["issues"] = []
    base.update(kw)
    return base


class TestApplyCritique:
    def test_clean_draft_passes(self):
        r = apply_critique(_good_scores(), "Hi Alice, would value a quick chat about structures.")
        assert r.passed is True
        assert r.quality_code == "OK"

    def test_severe_dimension_holds(self):
        r = apply_critique(_good_scores(grounded_facts=1), "A grounded, specific note.")
        assert r.passed is False
        assert r.quality_code == "CRITIC_HOLD"
        assert "grounded_facts" in r.reason

    def test_too_many_weak_dims_holds(self):
        r = apply_critique(
            _good_scores(specificity=2, tone=2, economy=2), "A plain note."
        )
        assert r.passed is False

    def test_ai_tell_forces_hold_even_with_good_scores(self):
        r = apply_critique(_good_scores(), "I hope this email finds you well.")
        assert r.passed is False
        assert any("ai_detection" in i for i in r.issues)

    def test_unparseable_score_is_a_hold(self):
        r = apply_critique(_good_scores(tone="bogus"), "A note.")
        # tone coerced to 0 (severe) → hold.
        assert r.passed is False

    def test_non_dict_degrades_to_pass(self):
        r = apply_critique("not a dict", "A clean note.")
        # All dims default to MIN_SCORE (3) → no hold.
        assert r.passed is True


# --------------------------------------------------------------------------- #
# build_critique_context (pure grounding)
# --------------------------------------------------------------------------- #


class TestBuildCritiqueContext:
    def test_grounding_shape(self):
        ctx = build_critique_context(
            "Hi Alice, quick chat?",
            {"full_name": "Alice Smith", "title": "Composites Engineer",
             "persona": "PEER_ENGINEER", "hook": "your composites work"},
            "COLD_EMAIL",
            "Led 787 wing-box stress team.",
            subject="Quick question",
        )
        assert ctx["recipient"]["full_name"] == "Alice Smith"
        assert ctx["recipient"]["hook"] == "your composites work"
        assert ctx["channel"] == "COLD_EMAIL"
        assert ctx["approved_facts"] == "Led 787 wing-box stress team."
        assert ctx["draft"] == {"subject": "Quick question", "body": "Hi Alice, quick chat?"}
        assert set(RUBRIC_DIMENSIONS).issubset(ctx["rubric"])
        assert ctx["hold_rule"]["min_score"] == 3
        assert all(d in ctx["instruction"] for d in ("specificity", "issues"))

    def test_missing_facts_default(self):
        ctx = build_critique_context("body", {}, "COLD_EMAIL", None)
        assert "no APPROVED FACTS" in ctx["approved_facts"]
        assert ctx["recipient"]["full_name"] == "Unknown"
        assert ctx["recipient"]["hook"] == "GENERIC"


# --------------------------------------------------------------------------- #
# CLI bridge
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _seed_draft(body="Hi Alice, would value a quick chat about structures.",
                subject=None, quality_code="OK") -> int:
    with with_writer() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name, state) "
            "VALUES ('acme', 'Acme Corp', 'SELECTED')"
        )
        co = conn.execute("SELECT id FROM companies WHERE slug='acme'").fetchone()["id"]
        cid = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "email, hook, state) VALUES (?,?,?,?,?,?,?, 'DRAFTED')",
            (co, "Alice Smith", "Composites Engineer", "PEER_ENGINEER", "COMPOSITE_DESIGN",
             "alice@acme.com", "your composites work"),
        ).lastrowid
        did = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, subject, version, "
            "quality_flag, quality_code) VALUES (?,?,?,?,1,?,?)",
            (cid, "COLD_EMAIL", body, subject, int(quality_code != "OK"), quality_code),
        ).lastrowid
        return int(did)


def _draft_row(did: int) -> dict:
    conn = get_connection()
    try:
        return dict(conn.execute(
            "SELECT quality_code, quality_flag, critic_trace FROM drafts WHERE id = ?", (did,)
        ).fetchone())
    finally:
        conn.close()


def _stdin(monkeypatch, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


class TestContext:
    def test_prints_grounding_json(self, capsys, tmp_db):
        did = _seed_draft()
        assert run_context(did) == 0
        ctx = json.loads(capsys.readouterr().out)
        assert ctx["recipient"]["full_name"] == "Alice Smith"
        assert ctx["channel"] == "COLD_EMAIL"
        assert ctx["draft"]["body"].startswith("Hi Alice")
        assert "rubric" in ctx

    def test_unknown_draft(self, capsys, tmp_db):
        assert run_context(999) == 1
        assert "draft not found" in json.loads(capsys.readouterr().out)["error"]

    def test_unknown_channel_on_draft(self, capsys, tmp_db):
        did = _seed_draft()
        with with_writer() as conn:
            conn.execute("UPDATE drafts SET channel = 'BOGUS' WHERE id = ?", (did,))
        assert run_context(did) == 1
        assert "unknown channel" in json.loads(capsys.readouterr().out)["error"]


class TestApply:
    def test_pass_keeps_ok(self, capsys, tmp_db):
        did = _seed_draft()
        assert run_apply(did, json.dumps(_good_scores())) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["passed"] is True
        assert out["quality_code"] == "OK"
        row = _draft_row(did)
        assert row["quality_code"] == "OK"
        assert json.loads(row["critic_trace"])["passed"] is True

    def test_hold_downgrades_ok_to_critic_hold(self, capsys, tmp_db):
        did = _seed_draft()
        assert run_apply(did, json.dumps(_good_scores(grounded_facts=0))) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["passed"] is False
        assert out["quality_code"] == "CRITIC_HOLD"
        row = _draft_row(did)
        assert row["quality_code"] == "CRITIC_HOLD"
        assert row["quality_flag"] == 1
        assert json.loads(row["critic_trace"])["quality_code"] == "CRITIC_HOLD"

    def test_hard_fail_not_upgraded(self, capsys, tmp_db):
        # A held critic verdict must NOT overwrite the more-severe HARD_FAIL code.
        did = _seed_draft(quality_code="HARD_FAIL")
        run_apply(did, json.dumps(_good_scores(grounded_facts=0)))
        assert _draft_row(did)["quality_code"] == "HARD_FAIL"

    def test_soft_flag_downgraded_to_hold(self, capsys, tmp_db):
        did = _seed_draft(quality_code="SOFT_FLAG")
        run_apply(did, json.dumps(_good_scores(grounded_facts=0)))
        assert _draft_row(did)["quality_code"] == "CRITIC_HOLD"

    def test_unknown_draft(self, capsys, tmp_db):
        assert run_apply(999, json.dumps(_good_scores())) == 1
        assert "draft not found" in json.loads(capsys.readouterr().out)["error"]

    def test_bad_json(self, capsys, tmp_db):
        did = _seed_draft()
        assert run_apply(did, "{not json") == 1
        assert "invalid JSON" in json.loads(capsys.readouterr().out)["error"]

    def test_not_a_dict(self, capsys, tmp_db):
        did = _seed_draft()
        assert run_apply(did, json.dumps([1, 2, 3])) == 1
        assert "must be a JSON object" in json.loads(capsys.readouterr().out)["error"]


class TestDispatch:
    def test_dispatch_context(self, capsys, tmp_db):
        did = _seed_draft()
        run_critic_host(argparse.Namespace(verb="context", draft_id=did))
        assert json.loads(capsys.readouterr().out)["recipient"]["full_name"] == "Alice Smith"

    def test_dispatch_apply_reads_stdin(self, capsys, monkeypatch, tmp_db):
        did = _seed_draft()
        _stdin(monkeypatch, json.dumps(_good_scores(grounded_facts=0)))
        run_critic_host(argparse.Namespace(verb="apply", draft_id=did, scores=None))
        assert json.loads(capsys.readouterr().out)["quality_code"] == "CRITIC_HOLD"

    def test_dispatch_apply_scores_arg(self, capsys, tmp_db):
        did = _seed_draft()
        run_critic_host(
            argparse.Namespace(verb="apply", draft_id=did, scores=json.dumps(_good_scores()))
        )
        assert json.loads(capsys.readouterr().out)["passed"] is True
