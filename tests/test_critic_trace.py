"""
tests/test_critic_trace.py
Visibility fix: critic per-dimension scores + issues persist to
drafts.critic_trace and surface in the artifact + marketer render.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.agents.artifact_writer import _format_critic_trace, write_artifact
from src.agents.critic import CriticResult, RUBRIC_DIMENSIONS, MIN_SCORE
from src.agents.drafter import draft_for_contacts
from src.agents.marketer import _format_critic_for_reviewer, run_approval_loop
from src.core.db import get_connection, init_db, with_writer


# ---------------------------------------------------------------------------
# CriticResult.to_json shape
# ---------------------------------------------------------------------------

class TestCriticResultSerialization:
    def test_roundtrip(self):
        r = CriticResult(
            passed=False, quality_code="CRITIC_HOLD",
            scores={"specificity": 2, "one_ask": 5, "tone": 4,
                    "grounded_facts": 5, "economy": 3, "relevance": 4},
            issues=["specificity: generic opener"],
            reason="critic flagged 1 dimension(s) below 3: specificity",
        )
        blob = r.to_json()
        loaded = json.loads(blob)
        assert loaded["passed"] is False
        assert loaded["quality_code"] == "CRITIC_HOLD"
        assert loaded["scores"]["specificity"] == 2
        assert loaded["issues"] == ["specificity: generic opener"]
        assert "specificity" in loaded["reason"]

    def test_passing_result_also_serializes(self):
        r = CriticResult(
            passed=True, quality_code="OK",
            scores={d: 5 for d in RUBRIC_DIMENSIONS},
            issues=[],
        )
        loaded = json.loads(r.to_json())
        assert loaded["passed"] is True
        assert loaded["issues"] == []


# ---------------------------------------------------------------------------
# Migration 003 — critic_trace column exists with default NULL
# ---------------------------------------------------------------------------

class TestMigration003:
    def test_column_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "x.db")
        init_db()
        conn = get_connection()
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(drafts)")}
        finally:
            conn.close()
        assert "critic_trace" in cols

    def test_default_null(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "x.db")
        init_db()
        with with_writer() as conn:
            conn.execute("INSERT INTO companies (slug, name) VALUES ('a', 'A')")
            conn.execute("INSERT INTO contacts (company_id, full_name) VALUES (1, 'X')")
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body) "
                "VALUES (1, 'LINKEDIN_CONNECTION', 'hi')"
            )
        conn = get_connection()
        try:
            row = conn.execute("SELECT critic_trace FROM drafts").fetchone()
        finally:
            conn.close()
        assert row["critic_trace"] is None


# ---------------------------------------------------------------------------
# Drafter persists trace on CRITIC_HOLD and on critic-pass
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


def _seed_one_contact() -> int:
    init_db()
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        cid = c.lastrowid
        c = conn.execute(
            """INSERT INTO contacts
               (company_id, full_name, title, persona, focus_area,
                linkedin_url, email, hook, state)
               VALUES (?, 'Jane Doe', 'Stress Engineer', 'PEER_ENGINEER',
                       'STRUCTURAL_ANALYSIS', 'https://linkedin.com/x',
                       NULL, 'shared structures work', 'SELECTED')""",
            (cid,),
        )
        return c.lastrowid


class _Client:
    def __init__(self, draft_texts, critic_scores):
        self.queue = list(draft_texts)
        self.critic_scores = critic_scores
        self.messages = Mock()
        self.messages.create.side_effect = self._create

    def _create(self, **kwargs):
        if kwargs.get("tools"):
            tool = Mock()
            tool.type = "tool_use"
            payload = {dim: self.critic_scores.get(dim, 5) for dim in RUBRIC_DIMENSIONS}
            payload["issues"] = ["specificity: too vague"] if any(v < MIN_SCORE for v in payload.values() if isinstance(v, int)) else []
            tool.input = payload
            resp = Mock()
            resp.content = [tool]
            return resp
        msg = Mock()
        msg.content = [Mock(text=self.queue.pop(0))]
        return msg


class TestDrafterPersistsTrace:
    def test_held_draft_carries_trace(self, db_path):
        contact_id = _seed_one_contact()
        bad = {d: 5 for d in RUBRIC_DIMENSIONS}
        bad["specificity"] = MIN_SCORE - 1
        client = _Client(
            draft_texts=["Hello world.", "Follow-up text."],
            critic_scores=bad,
        )
        result = draft_for_contacts([contact_id], anthropic_client=client)

        for d in result[contact_id]:
            assert d.quality_code == "CRITIC_HOLD"
            assert d.critic_trace is not None
            trace = json.loads(d.critic_trace)
            assert trace["passed"] is False
            assert trace["scores"]["specificity"] == MIN_SCORE - 1

        # DB row also carries the trace
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT critic_trace, quality_code FROM drafts WHERE contact_id = ?",
                (contact_id,),
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            assert r["quality_code"] == "CRITIC_HOLD"
            assert r["critic_trace"] is not None

    def test_passing_draft_also_carries_trace(self, db_path):
        contact_id = _seed_one_contact()
        client = _Client(
            draft_texts=["Brief grounded note.", "Brief grounded follow-up."],
            critic_scores={d: 5 for d in RUBRIC_DIMENSIONS},
        )
        result = draft_for_contacts([contact_id], anthropic_client=client)

        for d in result[contact_id]:
            assert d.quality_code == "OK"
            assert d.critic_trace is not None
            trace = json.loads(d.critic_trace)
            assert trace["passed"] is True
            # Every dimension should be 5 in this mock
            assert all(v == 5 for v in trace["scores"].values())

    def test_hard_fail_skips_critic_trace(self, db_path):
        contact_id = _seed_one_contact()
        client = _Client(
            draft_texts=["Hey [RESEARCH_NEEDED] saw your work.", "Clean follow-up."],
            critic_scores={d: 5 for d in RUBRIC_DIMENSIONS},
        )
        result = draft_for_contacts([contact_id], anthropic_client=client)

        by_channel = {d.channel: d for d in result[contact_id]}
        # HARD_FAIL short-circuits before the critic runs.
        hf = by_channel["LINKEDIN_CONNECTION"]
        assert hf.quality_code == "HARD_FAIL"
        assert hf.critic_trace is None


# ---------------------------------------------------------------------------
# Artifact + marketer surface the trace
# ---------------------------------------------------------------------------

class TestArtifactSurfacesTrace:
    def test_artifact_renders_scores_and_issues(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "a.db")
        init_db()
        trace = json.dumps({
            "passed": False, "quality_code": "CRITIC_HOLD",
            "scores": {"specificity": 2, "one_ask": 5, "tone": 4,
                       "grounded_facts": 5, "economy": 3, "relevance": 4},
            "issues": ["specificity: opens with generic eVTOL line"],
            "reason": "below threshold",
        })
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('a', 'A', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, state) "
                "VALUES (?, 'X', 'PEER_ENGINEER', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, "
                "quality_flag, quality_code, critic_trace) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'b', 1, 1, 'CRITIC_HOLD', ?)",
                (contact_id, trace),
            )
        path = write_artifact(company_id, _output_dir=tmp_path / "out")
        text = path.read_text()
        assert "Critic scores:" in text
        assert "specificity=2" in text
        assert "opens with generic eVTOL line" in text

    def test_format_critic_trace_handles_missing(self):
        assert _format_critic_trace(None) is None
        assert _format_critic_trace("") is None
        assert _format_critic_trace("not json") is None

    def test_format_critic_trace_empty_payload_returns_none(self):
        assert _format_critic_trace(json.dumps({"passed": True})) is None


class TestMarketerSurfacesTrace:
    def test_render_includes_critic_lines_when_present(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "m.db")
        init_db()
        trace = json.dumps({
            "passed": False, "quality_code": "CRITIC_HOLD",
            "scores": {"specificity": 2, "tone": 5, "one_ask": 4,
                       "grounded_facts": 5, "economy": 3, "relevance": 4},
            "issues": ["specificity: vague opener"],
            "reason": "below threshold",
        })
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('a', 'A', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, email, hook, state) "
                "VALUES (?, 'X', 'PEER_ENGINEER', 'STRUCTURAL_ANALYSIS', "
                "'https://linkedin.com/x', 'x@a.com', 'shared', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, "
                "quality_flag, quality_code, critic_trace) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'body', 1, 1, 'CRITIC_HOLD', ?)",
                (contact_id, trace),
            )

        inputs = iter(["SKIP 1"])
        run_approval_loop(company_id, _input_fn=lambda _: next(inputs))
        out = capsys.readouterr().out
        assert "Critic scores:" in out
        assert "specificity=2" in out
        assert "vague opener" in out

    def test_format_for_reviewer_handles_missing(self):
        assert _format_critic_for_reviewer(None) is None
        assert _format_critic_for_reviewer("not json") is None
