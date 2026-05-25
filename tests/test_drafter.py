"""
Integration tests for src/agents/drafter.py
Covers: 2 contacts × 3 channels = 6 drafts; guardrail regen; quality_flag; contact state.
"""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from src.agents.drafter import Draft, draft_for_contacts
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import Channel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


def _seed_contacts(n: int = 2) -> tuple[int, list[int]]:
    """Insert a company + n SELECTED contacts. Returns (company_id, contact_ids)."""
    init_db()
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme-corp', 'Acme Corp', 'SELECTED')"
        )
        company_id = cursor.lastrowid
        contact_ids = []
        for i in range(n):
            cursor = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, persona, focus_area, linkedin_url, hook, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'SELECTED')""",
                (
                    company_id,
                    f"Contact {i + 1}",
                    "Composites Engineer",
                    "PEER_ENGINEER",
                    "COMPOSITE_DESIGN",
                    f"https://linkedin.com/in/contact{i + 1}",
                    "your composites work",
                ),
            )
            contact_ids.append(cursor.lastrowid)
    return company_id, contact_ids


def _make_anthropic(responses: list[str]):
    """Build a mock Anthropic client that returns *responses* in order."""
    client = Mock()
    def _create(**kwargs):
        text = responses.pop(0)
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg
    client.messages.create.side_effect = _create
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDraftForContacts:
    def test_two_contacts_produce_six_drafts(self, db_path):
        _, contact_ids = _seed_contacts(2)
        # 6 clean drafts
        responses = [f"Draft text {i}" for i in range(6)]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        assert set(result.keys()) == set(contact_ids)
        total_drafts = sum(len(v) for v in result.values())
        assert total_drafts == 6

    def test_six_rows_in_db(self, db_path):
        _, contact_ids = _seed_contacts(2)
        responses = [f"Clean draft {i}" for i in range(6)]
        client = _make_anthropic(responses)
        draft_for_contacts(contact_ids, anthropic_client=client)

        conn = get_connection()
        try:
            rows = conn.execute("SELECT contact_id, channel FROM drafts ORDER BY id").fetchall()
        finally:
            conn.close()

        assert len(rows) == 6
        channels_per_contact = {}
        for r in rows:
            channels_per_contact.setdefault(r["contact_id"], set()).add(r["channel"])
        for cid in contact_ids:
            assert channels_per_contact[cid] == {
                Channel.LINKEDIN_CONNECTION.value,
                Channel.LINKEDIN_POST_CONNECTION.value,
                Channel.COLD_EMAIL.value,
            }

    def test_contact_state_transitions_to_drafted(self, db_path):
        _, contact_ids = _seed_contacts(2)
        responses = [f"Draft {i}" for i in range(6)]
        client = _make_anthropic(responses)
        draft_for_contacts(contact_ids, anthropic_client=client)

        conn = get_connection()
        try:
            states = conn.execute(
                f"SELECT state FROM contacts WHERE id IN ({','.join('?' * len(contact_ids))})",
                contact_ids,
            ).fetchall()
        finally:
            conn.close()

        assert all(r["state"] == "DRAFTED" for r in states)

    def test_blocklist_phrase_triggers_one_regen(self, db_path):
        """If the first draft for a channel contains a blocklist phrase, exactly one regen call is made."""
        _, contact_ids = _seed_contacts(1)
        # 3 channels; first channel (LINKEDIN_CONNECTION) triggers regen
        # Sequence: bad, good-regen, good, good  (3 channels = 3 initial calls; first is bad → +1 regen)
        responses = [
            "I noticed your profile — want to connect?",  # LINKEDIN_CONNECTION: BAD → triggers regen
            "Clean regen: your composites background caught my eye.",  # regen result
            "Post-connection follow-up message.",           # LINKEDIN_POST_CONNECTION
            "Subject: Aerospace role\n\nHi, wanted to reach out.",  # COLD_EMAIL
        ]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        # 4 total calls (3 base + 1 regen)
        assert client.messages.create.call_count == 4
        # 3 drafts inserted
        assert len(result[contact_ids[0]]) == 3

    def test_double_blocklist_sets_quality_flag(self, db_path):
        """When both the initial draft AND the regen contain blocklist phrases, quality_flag=True."""
        _, contact_ids = _seed_contacts(1)
        responses = [
            "I noticed your profile.",                     # LINKEDIN_CONNECTION: BAD
            "I admire your composites work greatly.",      # regen: ALSO BAD → quality_flag
            "Clean post-connection message.",              # LINKEDIN_POST_CONNECTION
            "Subject: Role inquiry\n\nHello.",             # COLD_EMAIL
        ]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        drafts = result[contact_ids[0]]
        linkedin_conn_draft = next(d for d in drafts if d.channel == Channel.LINKEDIN_CONNECTION.value)
        assert linkedin_conn_draft.quality_flag is True

        # Also verify the DB row has quality_flag=1
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT quality_flag FROM drafts WHERE id = ?",
                (linkedin_conn_draft.draft_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row["quality_flag"] == 1

    def test_clean_drafts_have_quality_flag_false(self, db_path):
        _, contact_ids = _seed_contacts(1)
        responses = [f"Clean draft {i}" for i in range(3)]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        for draft in result[contact_ids[0]]:
            assert draft.quality_flag is False

    def test_cold_email_subject_extracted(self, db_path):
        _, contact_ids = _seed_contacts(1)
        responses = [
            "Short connection note.",
            "Follow-up message text.",
            "Subject: Structures role at Acme\n\nHi there, wanted to reach out about your team.",
        ]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        email_draft = next(
            d for d in result[contact_ids[0]] if d.channel == Channel.COLD_EMAIL.value
        )
        assert email_draft.subject == "Structures role at Acme"
        assert "Hi there" in email_draft.body
        assert "Subject:" not in email_draft.body

    def test_draft_objects_have_correct_fields(self, db_path):
        _, contact_ids = _seed_contacts(1)
        responses = [f"Draft {i}" for i in range(3)]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        for draft in result[contact_ids[0]]:
            assert isinstance(draft, Draft)
            assert draft.contact_id == contact_ids[0]
            assert draft.draft_id > 0
            assert draft.version == 1
            assert draft.channel in {c.value for c in Channel}

    def test_empty_contact_list_returns_empty_dict(self, db_path):
        init_db()
        client = Mock()
        result = draft_for_contacts([], anthropic_client=client)
        assert result == {}
        client.messages.create.assert_not_called()
