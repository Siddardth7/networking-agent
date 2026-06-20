"""
Integration tests for src/agents/drafter.py
Covers: 2 contacts × 3 channels = 6 drafts; guardrail regen; quality_flag; contact state.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.drafter import Draft, DrafterPartialFailure, draft_for_contacts
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
    # Disable the Layer 4 critic for these tests — they cover hard_check,
    # persistence, and atomicity. Critic-specific behavior lives in
    # tests/test_critic.py and tests/test_drafter_critic.py.
    from src.core.config import Config, load_config  # local import keeps top tidy

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


def _seed_contacts(n: int = 2, with_email: bool = True) -> tuple[int, list[int]]:
    """Insert a company + n SELECTED contacts. Returns (company_id, contact_ids).

    Contacts include an email by default so the drafter generates all three
    channels (LinkedIn × 2 + cold email). Pass ``with_email=False`` to
    exercise the "skip COLD_EMAIL when no address" branch.
    """
    init_db()
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) "
            "VALUES ('acme-corp', 'Acme Corp', 'SELECTED')"
        )
        company_id = cursor.lastrowid
        contact_ids = []
        for i in range(n):
            cursor = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, persona, focus_area, linkedin_url,
                    email, hook, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SELECTED')""",
                (
                    company_id,
                    f"Contact {i + 1}",
                    "Composites Engineer",
                    "PEER_ENGINEER",
                    "COMPOSITE_DESIGN",
                    f"https://linkedin.com/in/contact{i + 1}",
                    f"contact{i + 1}@acme.com" if with_email else None,
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
        """If the first draft for a channel contains a blocklist phrase,
        exactly one regen call is made."""
        _, contact_ids = _seed_contacts(1)
        # 3 channels; first channel (LINKEDIN_CONNECTION) triggers regen
        # Sequence: bad, good-regen, good, good
        # (3 channels = 3 initial calls; first is bad → +1 regen)
        responses = [
            # LINKEDIN_CONNECTION: BAD → triggers regen
            "I noticed your profile — want to connect?",
            "Clean regen: your composites background caught my eye.",  # regen result
            "Post-connection follow-up message.",  # LINKEDIN_POST_CONNECTION
            "Subject: Aerospace role\n\nHi, wanted to reach out.",  # COLD_EMAIL
        ]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        # 4 total calls (3 base + 1 regen)
        assert client.messages.create.call_count == 4
        # 3 drafts inserted
        assert len(result[contact_ids[0]]) == 3

    def test_double_blocklist_sets_quality_flag(self, db_path):
        """When both the initial draft AND the regen contain blocklist
        phrases, quality_flag=True."""
        _, contact_ids = _seed_contacts(1)
        responses = [
            "I noticed your profile.",  # LINKEDIN_CONNECTION: BAD
            "Your impressive work in composites stood out.",  # regen: ALSO BAD → quality_flag
            "Clean post-connection message.",  # LINKEDIN_POST_CONNECTION
            "Subject: Role inquiry\n\nHello.",  # COLD_EMAIL
        ]
        client = _make_anthropic(responses)
        result = draft_for_contacts(contact_ids, anthropic_client=client)

        drafts = result[contact_ids[0]]
        linkedin_conn_draft = next(
            d for d in drafts if d.channel == Channel.LINKEDIN_CONNECTION.value
        )
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


class TestAtomicDraftSequence:
    """P6 — per-contact draft sequence must be atomic.

    If any insert fails mid-sequence, the entire transaction (DELETE of prior
    v1 drafts, all channel INSERTs, and the DRAFTED state transition) must be
    rolled back. The contact must remain in SELECTED state with NO partial
    draft rows visible.
    """

    def test_failure_mid_insert_rolls_back_entire_sequence(self, db_path, monkeypatch):
        _, contact_ids = _seed_contacts(1)
        cid = contact_ids[0]
        responses = [f"Draft {i}" for i in range(3)]
        client = _make_anthropic(responses)

        # Monkeypatch _insert_draft so the SECOND insert raises. The first
        # insert should have been written to the shared transaction but rolled
        # back when with_writer() catches the exception.
        from src.agents import drafter as drafter_mod

        real_insert = drafter_mod._insert_draft
        call_state = {"n": 0}

        def flaky_insert(contact_id, channel, body, subject, quality_flag, conn=None):
            call_state["n"] += 1
            if call_state["n"] == 2:
                raise RuntimeError("simulated crash mid-sequence")
            return real_insert(contact_id, channel, body, subject, quality_flag, conn=conn)

        monkeypatch.setattr(drafter_mod, "_insert_draft", flaky_insert)

        with pytest.raises(RuntimeError, match="Drafting failed for contact"):
            draft_for_contacts([cid], anthropic_client=client)

        # Verify atomicity: contact stayed SELECTED, no draft rows exist.
        conn = get_connection()
        try:
            state_row = conn.execute("SELECT state FROM contacts WHERE id = ?", (cid,)).fetchone()
            draft_rows = conn.execute(
                "SELECT id FROM drafts WHERE contact_id = ?", (cid,)
            ).fetchall()
        finally:
            conn.close()

        assert state_row["state"] == "SELECTED", (
            "Contact must NOT be marked DRAFTED when the draft sequence failed"
        )
        assert len(draft_rows) == 0, (
            "No partial draft rows should remain after a rolled-back sequence"
        )

    def test_failure_rolls_back_v1_delete_too(self, db_path, monkeypatch):
        """The DELETE of prior v1 drafts must also roll back so re-running
        the contact later still sees the original drafts (no data loss)."""
        _, contact_ids = _seed_contacts(1)
        cid = contact_ids[0]

        # Pre-seed an existing v1 draft for this contact (simulating a prior
        # successful run that we're about to re-attempt).
        with with_writer() as conn:
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, subject, version, quality_flag) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'pre-existing body', NULL, 1, 0)",
                (cid,),
            )

        responses = [f"Draft {i}" for i in range(3)]
        client = _make_anthropic(responses)

        # Make the first insert raise so the DELETE that ran just before it
        # within the same transaction must be rolled back as well.
        from src.agents import drafter as drafter_mod

        def always_fail(contact_id, channel, body, subject, quality_flag, conn=None):
            raise RuntimeError("simulated failure before any insert")

        monkeypatch.setattr(drafter_mod, "_insert_draft", always_fail)

        with pytest.raises(RuntimeError):
            draft_for_contacts([cid], anthropic_client=client)

        conn = get_connection()
        try:
            rows = conn.execute("SELECT body FROM drafts WHERE contact_id = ?", (cid,)).fetchall()
            state = conn.execute("SELECT state FROM contacts WHERE id = ?", (cid,)).fetchone()[
                "state"
            ]
        finally:
            conn.close()

        assert len(rows) == 1, "Pre-existing v1 draft must survive a rolled-back attempt"
        assert rows[0]["body"] == "pre-existing body"
        assert state == "SELECTED"


class TestPartialFailureAggregation:
    """P7 — `draft_for_contacts` must drain every worker future to completion,
    then raise a single `DrafterPartialFailure` carrying the partial results
    map (cid → drafts for every worker that succeeded) and the per-contact
    error list. Successful workers' DB writes are atomic from P6, so the
    aggregated exception surfaces what actually got persisted.
    """

    def test_one_fails_others_succeed_aggregates_partial_results(self, db_path, monkeypatch):
        """One contact's worker raises; the other two complete and the
        exception exposes the two successes via `.partial_results` and the
        one failure via `.errors`."""
        from src.agents import drafter as drafter_module

        _, contact_ids = _seed_contacts(3)
        failing_cid = contact_ids[1]

        # 6 responses for the 2 succeeding contacts (3 channels each). The
        # failing contact raises before any LLM call, so it consumes zero.
        responses = [f"Draft {i}" for i in range(6)]
        client = _make_anthropic(responses)

        real_fn = drafter_module._draft_all_channels_for_contact

        def selective_fail(
            contact_id, anthropic_client, library_path, opener_registry=None, ask_angle=None
        ):
            if contact_id == failing_cid:
                raise RuntimeError(f"boom for {contact_id}")
            return real_fn(contact_id, anthropic_client, library_path, opener_registry, ask_angle)

        monkeypatch.setattr(drafter_module, "_draft_all_channels_for_contact", selective_fail)

        with pytest.raises(DrafterPartialFailure) as exc_info:
            draft_for_contacts(contact_ids, anthropic_client=client)

        exc = exc_info.value
        # Partial results cover ONLY the two successful contacts.
        assert set(exc.partial_results.keys()) == set(contact_ids) - {failing_cid}
        for cid, drafts in exc.partial_results.items():
            assert len(drafts) == 3, f"contact {cid} should have all 3 channel drafts"
        # Exactly one error recorded, for the failing contact.
        assert len(exc.errors) == 1
        err_cid, err_exc = exc.errors[0]
        assert err_cid == failing_cid
        assert isinstance(err_exc, RuntimeError)
        assert "boom" in str(err_exc)
        # Subclass of RuntimeError so legacy `except RuntimeError` still works.
        assert isinstance(exc, RuntimeError)

    def test_all_workers_fail_partial_results_empty(self, db_path, monkeypatch):
        """Every worker raises → `.partial_results` is an empty dict and
        `.errors` lists every contact_id."""
        from src.agents import drafter as drafter_module

        _, contact_ids = _seed_contacts(3)
        client = _make_anthropic(["unused"] * 9)

        def always_fail(contact_id, anthropic_client, library_path):
            raise RuntimeError(f"fail-{contact_id}")

        monkeypatch.setattr(drafter_module, "_draft_all_channels_for_contact", always_fail)

        with pytest.raises(DrafterPartialFailure) as exc_info:
            draft_for_contacts(contact_ids, anthropic_client=client)

        exc = exc_info.value
        assert exc.partial_results == {}
        assert {cid for cid, _ in exc.errors} == set(contact_ids)
        assert len(exc.errors) == 3

    def test_all_workers_succeed_returns_dict_no_exception(self, db_path):
        """Regression guard: happy path is unchanged — `draft_for_contacts`
        returns a dict mapping every contact_id to its drafts, no exception."""
        _, contact_ids = _seed_contacts(2)
        responses = [f"Clean draft {i}" for i in range(6)]
        client = _make_anthropic(responses)

        result = draft_for_contacts(contact_ids, anthropic_client=client)

        assert set(result.keys()) == set(contact_ids)
        assert sum(len(v) for v in result.values()) == 6
