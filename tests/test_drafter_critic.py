"""
tests/test_drafter_critic.py
Layer 4 integration: critic verdict translates into drafts.quality_code
when hard checks pass; HARD_FAIL short-circuits the critic; the marketer
gate blocks CRITIC_HOLD just like HARD_FAIL.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.critic import RUBRIC_DIMENSIONS, SEVERE_SCORE
from src.agents.drafter import draft_for_contacts
from src.agents.marketer import _contact_has_hard_fail, run_approval_loop
from src.core.db import get_connection, init_db, with_writer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    # IMPORTANT: critic is ENABLED here (default) — that's the point.
    return path


def _seed(with_email: bool = True) -> tuple[int, list[int]]:
    init_db()
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            """INSERT INTO contacts
               (company_id, full_name, title, persona, focus_area, linkedin_url,
                email, hook, state)
               VALUES (?, 'Jane Doe', 'Stress Engineer', 'PEER_ENGINEER',
                       'STRUCTURAL_ANALYSIS', 'https://linkedin.com/x',
                       ?, 'shared structures work', 'SELECTED')""",
            (company_id, "jd@acme.com" if with_email else None),
        )
        return company_id, [c.lastrowid]


class _DraftAndCriticClient:
    """Mock Anthropic client where draft calls return plain text and critic
    calls return tool_use with the configured scores."""

    def __init__(self, draft_texts: list[str], critic_scores: dict[str, int]):
        self.draft_texts = list(draft_texts)
        self.critic_scores = critic_scores
        self.calls = 0
        self.messages = Mock()
        self.messages.create.side_effect = self._create

    def _create(self, **kwargs):
        self.calls += 1
        if "tools" in kwargs and kwargs.get("tools"):
            # Critic call — return tool_use response.
            tool = Mock()
            tool.type = "tool_use"
            payload = {dim: self.critic_scores.get(dim, 5) for dim in RUBRIC_DIMENSIONS}
            payload["issues"] = []
            tool.input = payload
            resp = Mock()
            resp.content = [tool]
            return resp
        # Drafter call — return next plain-text response.
        text = self.draft_texts.pop(0) if self.draft_texts else "Fallback."
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg


# ---------------------------------------------------------------------------
# Critic verdict reaches quality_code
# ---------------------------------------------------------------------------

class TestCriticVerdictPersisted:
    def test_critic_pass_yields_quality_code_ok(self, db_path):
        _, ids = _seed(with_email=False)  # 2 channels for speed
        # Both draft calls return clean text; both critic calls pass.
        client = _DraftAndCriticClient(
            draft_texts=["Brief LinkedIn note.", "Conversational follow-up."],
            critic_scores={dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        result = draft_for_contacts(ids, anthropic_client=client)

        for d in result[ids[0]]:
            assert d.quality_code == "OK"
            assert d.quality_flag is False

    def test_critic_low_score_yields_critic_hold(self, db_path):
        _, ids = _seed(with_email=False)
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["specificity"] = SEVERE_SCORE  # AUDIT-A3: severe holds
        client = _DraftAndCriticClient(
            draft_texts=["Generic.", "Also generic."],
            critic_scores=bad,
        )
        result = draft_for_contacts(ids, anthropic_client=client)

        for d in result[ids[0]]:
            assert d.quality_code == "CRITIC_HOLD"
            assert d.quality_flag is True

        # Persisted to DB.
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT quality_code FROM drafts WHERE contact_id = ?", (ids[0],)
            ).fetchall()
        finally:
            conn.close()
        assert all(r["quality_code"] == "CRITIC_HOLD" for r in rows)


# ---------------------------------------------------------------------------
# HARD_FAIL short-circuits — critic never runs
# ---------------------------------------------------------------------------

class TestHardFailShortCircuit:
    def test_research_needed_stays_hard_fail_not_critic_hold(self, db_path):
        _, ids = _seed(with_email=False)
        # Draft #1 has a placeholder → hard_check HARD_FAIL → no critic call.
        # Draft #2 is clean → critic call passes.
        client = _DraftAndCriticClient(
            draft_texts=[
                "Hey [RESEARCH_NEEDED] saw your work.",   # gen 1 — placeholder
                "Hey [RESEARCH_NEEDED] regen still bad.",  # AUDIT-A1 regen
                "Clean follow-up.",                        # OK
            ],
            critic_scores={dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        result = draft_for_contacts(ids, anthropic_client=client)

        by_channel = {d.channel: d for d in result[ids[0]]}
        assert by_channel["LINKEDIN_CONNECTION"].quality_code == "HARD_FAIL"
        # The hard_check short-circuit means only ONE critic call happened
        # (for the clean post-connection draft). Total: 2 first-pass drafts
        # + 1 anti-placeholder regen (AUDIT-A1) + 1 critic.
        assert client.calls == 4


# ---------------------------------------------------------------------------
# Marketer gate blocks CRITIC_HOLD just like HARD_FAIL
# ---------------------------------------------------------------------------

class TestMarketerBlocksCriticHold:
    def test_contact_has_hard_fail_detects_critic_hold(self):
        # Despite the legacy name, helper now covers CRITIC_HOLD too.
        assert _contact_has_hard_fail({"drafts": [
            {"quality_code": "CRITIC_HOLD"},
        ]}) is True

    def test_approve_all_skips_critic_hold(self, db_path, capsys):
        # Seed a DRAFTED contact directly with a CRITIC_HOLD draft.
        init_db()
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
                "quality_flag, quality_code) VALUES (?, 'LINKEDIN_CONNECTION', "
                "'body', 1, 1, 'CRITIC_HOLD')",
                (contact_id,),
            )

        inputs = iter(["APPROVE all", "SKIP 1"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert contact_id not in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "HARD_FAIL" in out or "refusing to approve" in out.lower()

    def test_force_overrides_critic_hold(self, db_path, capsys):
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('a', 'A', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, email, hook, state) "
                "VALUES (?, 'Y', 'PEER_ENGINEER', 'STRUCTURAL_ANALYSIS', "
                "'https://linkedin.com/y', 'y@a.com', 'shared', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, "
                "quality_flag, quality_code) VALUES (?, 'LINKEDIN_CONNECTION', "
                "'body', 1, 1, 'CRITIC_HOLD')",
                (contact_id,),
            )

        inputs = iter(["APPROVE 1 --force"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert contact_id in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "--force override" in out
