"""
tests/test_drafter_quality_code.py
Layer 3+5: drafter wires hard_check + quality_code into draft rows and
skips COLD_EMAIL when the contact has no email address.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.drafter import draft_for_contacts
from src.core.db import get_connection, init_db, with_writer


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_drafter.py for parity)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    # Layer 4 critic disabled here — these tests are for hard_check +
    # quality_code persistence. Critic-specific behavior is in
    # tests/test_critic.py and tests/test_drafter_critic.py.
    from src.core.config import Config, load_config
    real = load_config

    def _no_critic_cfg():
        cfg = real()
        return Config(
            anthropic_api_key=cfg.anthropic_api_key,
            serper_api_key=cfg.serper_api_key,
            hunter_api_key=cfg.hunter_api_key,
            serper_monthly_limit=cfg.serper_monthly_limit,
            hunter_monthly_limit=cfg.hunter_monthly_limit,
            finder_limit=cfg.finder_limit,
            linkedin_char_limit=cfg.linkedin_char_limit,
            email_word_limit=cfg.email_word_limit,
            batch_hard_fail_threshold=cfg.batch_hard_fail_threshold,
            enable_critic=False,
        )

    monkeypatch.setattr("src.agents.drafter.load_config", _no_critic_cfg)
    return path


def _seed(n: int = 1, with_email: bool = True) -> tuple[int, list[int]]:
    init_db()
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        company_id = cursor.lastrowid
        ids = []
        for i in range(n):
            c = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, persona, focus_area, linkedin_url,
                    email, hook, state)
                   VALUES (?, ?, ?, 'PEER_ENGINEER', 'COMPOSITE_DESIGN', ?, ?, ?, 'SELECTED')""",
                (
                    company_id,
                    f"Person {i}",
                    "Composites Engineer",
                    f"https://linkedin.com/in/person{i}",
                    f"p{i}@acme.com" if with_email else None,
                    "your composites work",
                ),
            )
            ids.append(c.lastrowid)
    return company_id, ids


def _mk_client(responses: list[str]):
    client = Mock()
    def _create(**kwargs):
        text = responses.pop(0)
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg
    client.messages.create.side_effect = _create
    return client


# ---------------------------------------------------------------------------
# quality_code persistence
# ---------------------------------------------------------------------------

class TestQualityCodePersisted:
    def test_clean_draft_has_quality_code_ok(self, db_path):
        _, ids = _seed(1)
        client = _mk_client([
            "Brief connection note.",
            "Conversational follow-up.",
            "Subject: hi\n\nBody.",
        ])
        result = draft_for_contacts(ids, anthropic_client=client)

        for d in result[ids[0]]:
            assert d.quality_code == "OK"
            assert d.quality_flag is False

    def test_research_needed_placeholder_marked_hard_fail(self, db_path):
        _, ids = _seed(1)
        # AUDIT-A1: a placeholder in the first generation now triggers one
        # corrective regen; only a draft that is STILL dirty afterwards is
        # HARD_FAILed (and redacted, AUDIT-A2).
        client = _mk_client([
            "Hey — saw your [RESEARCH_NEEDED] post recently.",  # gen 1
            "Hey — still citing [RESEARCH_NEEDED] here.",  # regen, still dirty
            "Conversational follow-up.",
            "Subject: hi\n\nBody.",
        ])
        result = draft_for_contacts(ids, anthropic_client=client)

        drafts = result[ids[0]]
        conn_draft = next(d for d in drafts if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.quality_code == "HARD_FAIL"
        assert conn_draft.quality_flag is True

        # And the row hits the DB
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT quality_code, quality_flag FROM drafts WHERE id = ?",
                (conn_draft.draft_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["quality_code"] == "HARD_FAIL"
        assert row["quality_flag"] == 1

    def test_overlong_linkedin_note_marked_hard_fail(self, db_path):
        _, ids = _seed(1)
        long_note = "x" * 250  # > 200-char LinkedIn cap
        client = _mk_client([
            long_note,
            "Conversational follow-up.",
            "Subject: hi\n\nBody.",
        ])
        result = draft_for_contacts(ids, anthropic_client=client)
        conn_draft = next(d for d in result[ids[0]] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.quality_code == "HARD_FAIL"


# ---------------------------------------------------------------------------
# COLD_EMAIL skipped when contact has no email
# ---------------------------------------------------------------------------

class TestColdEmailSkippedWhenNoAddress:
    def test_two_drafts_only_when_no_email(self, db_path):
        _, ids = _seed(1, with_email=False)
        # Only 2 LLM calls expected (LinkedIn connection + post-connection).
        client = _mk_client([
            "Connection note.",
            "Post-connection follow-up.",
        ])
        result = draft_for_contacts(ids, anthropic_client=client)

        channels = {d.channel for d in result[ids[0]]}
        assert channels == {"LINKEDIN_CONNECTION", "LINKEDIN_POST_CONNECTION"}
        assert client.messages.create.call_count == 2

    def test_all_three_drafts_when_email_present(self, db_path):
        _, ids = _seed(1, with_email=True)
        client = _mk_client([
            "Connection note.",
            "Post-connection follow-up.",
            "Subject: hi\n\nBody.",
        ])
        result = draft_for_contacts(ids, anthropic_client=client)
        channels = {d.channel for d in result[ids[0]]}
        assert channels == {
            "LINKEDIN_CONNECTION", "LINKEDIN_POST_CONNECTION", "COLD_EMAIL",
        }
