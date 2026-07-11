"""
tests/test_host_draft.py
Host-token drafting seam (#50): build_draft_context (deterministic handoff) and
save_host_draft (deterministic guardrail gate + persist). No LLM calls.
"""

from __future__ import annotations

import pytest

from src.agents.drafter import build_draft_context, save_host_draft
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import Channel


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _seed_contact(*, persona="PEER_ENGINEER", focus="COMPOSITE_DESIGN", email="a@acme.com") -> int:
    with with_writer() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name, state) "
            "VALUES ('acme', 'Acme Corp', 'SELECTED')"
        )
        co = conn.execute("SELECT id FROM companies WHERE slug='acme'").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "linkedin_url, email, hook, state) VALUES (?,?,?,?,?,?,?,?, 'SELECTED')",
            (co, "Alice Smith", "Composites Engineer", persona, focus,
             "https://linkedin.com/in/alice", email, "your composites work"),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# build_draft_context
# --------------------------------------------------------------------------- #


class TestBuildContext:
    def test_unknown_contact_returns_none(self):
        assert build_draft_context(999, Channel.COLD_EMAIL) is None

    def test_returns_full_grounding(self):
        cid = _seed_contact()
        ctx = build_draft_context(cid, Channel.COLD_EMAIL)
        assert ctx["contact"]["full_name"] == "Alice Smith"
        assert ctx["contact"]["company"] == "Acme Corp"
        assert ctx["contact"]["hook"] == "your composites work"
        assert ctx["contact"]["persona"] == "PEER_ENGINEER"
        assert ctx["channel"] == "COLD_EMAIL"
        assert ctx["channel_constraints"]  # non-empty
        assert "fact_discipline" in ctx
        assert isinstance(ctx["approved_facts"], list)
        assert isinstance(ctx["persona_template"], str)
        assert "voice_doc" in ctx

    def test_channel_constraints_track_channel(self):
        cid = _seed_contact()
        li = build_draft_context(cid, Channel.LINKEDIN_CONNECTION)
        em = build_draft_context(cid, Channel.COLD_EMAIL)
        assert li["channel_constraints"] != em["channel_constraints"]

    def test_posting_none_without_job_id(self):
        """#60: Campaign mode (no job_id) → posting is None, behavior unchanged."""
        cid = _seed_contact()
        assert build_draft_context(cid, Channel.COLD_EMAIL)["posting"] is None

    def test_posting_block_added_for_job_id(self):
        """#60: a job_id names the role → posting block with role_title + job_url."""
        cid = _seed_contact()
        with with_writer() as conn:
            conn.execute(
                "INSERT INTO applications (job_id, company, role_title, job_url) "
                "VALUES ('ja-1', 'Acme Corp', 'Quality Engineer', 'https://x/req/1')"
            )
        ctx = build_draft_context(cid, Channel.COLD_EMAIL, job_id="ja-1")
        assert ctx["posting"] == {
            "job_id": "ja-1", "role_title": "Quality Engineer", "job_url": "https://x/req/1",
        }

    def test_unknown_job_id_posting_none(self):
        cid = _seed_contact()
        ctx = build_draft_context(cid, Channel.COLD_EMAIL, job_id="nope")
        assert ctx["posting"] is None

    def test_invalid_persona_focus_defaults(self):
        cid = _seed_contact(persona="BOGUS", focus="NOPE")
        ctx = build_draft_context(cid, Channel.COLD_EMAIL)
        assert ctx["contact"]["persona"] == "PEER_ENGINEER"  # default
        assert ctx["contact"]["focus_area"] == "PEER"  # default

    def test_missing_hook_is_generic(self):
        cid = _seed_contact()
        with with_writer() as conn:
            conn.execute("UPDATE contacts SET hook = NULL WHERE id = ?", (cid,))
        assert build_draft_context(cid, Channel.COLD_EMAIL)["contact"]["hook"] == "GENERIC"


# --------------------------------------------------------------------------- #
# save_host_draft
# --------------------------------------------------------------------------- #


def _draft_rows(contact_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT channel, body, subject, quality_code, quality_flag, critic_trace "
            "FROM drafts WHERE contact_id = ?",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()


def _contact_state(contact_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT state FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()["state"]
    finally:
        conn.close()


class TestSaveHostDraft:
    def test_clean_draft_persists_ok_and_marks_drafted(self):
        cid = _seed_contact()
        out = save_host_draft(
            cid, Channel.COLD_EMAIL, "Hi Alice, would value a quick chat.", subject="Hello"
        )
        assert out["quality_code"] == "OK"
        assert out["draft_id"] > 0
        rows = _draft_rows(cid)
        assert len(rows) == 1
        assert rows[0]["body"] == "Hi Alice, would value a quick chat."
        assert rows[0]["subject"] == "Hello"
        assert rows[0]["quality_flag"] == 0
        assert _contact_state(cid) == "DRAFTED"

    def test_placeholder_hard_fails_and_redacts(self):
        cid = _seed_contact()
        out = save_host_draft(cid, Channel.COLD_EMAIL, "Reaching out about [COMPANY].")
        assert out["quality_code"] == "HARD_FAIL"
        assert "[COMPANY]" not in out["body"]  # redacted
        assert _draft_rows(cid)[0]["critic_trace"] is not None

    def test_length_over_cap_hard_fails(self):
        cid = _seed_contact()
        long_note = "word " * 100  # > 280 chars on a LinkedIn connection note
        out = save_host_draft(cid, Channel.LINKEDIN_CONNECTION, long_note)
        assert out["quality_code"] == "HARD_FAIL"

    def test_fabricated_metric_hard_fails(self):
        cid = _seed_contact()
        out = save_host_draft(
            cid, Channel.COLD_EMAIL, "I cut cost 47% on the line.",
            source_facts="Reduced scrap on the layup line.",  # no 47% in facts
        )
        assert out["quality_code"] == "HARD_FAIL"

    def test_humanize_is_applied(self):
        cid = _seed_contact()
        # An em-dash tell the humanizer normalizes — body should come back changed.
        out = save_host_draft(cid, Channel.COLD_EMAIL, "Hi Alice — I admire your work.")
        assert "—" not in out["body"] or out["quality_code"] == "OK"


# --------------------------------------------------------------------------- #
# company state advance (#98): SELECTED -> DRAFTED on the host path
# --------------------------------------------------------------------------- #


def _company_state(slug="acme") -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT state FROM companies WHERE slug = ?", (slug,)).fetchone()
        return row["state"]
    finally:
        conn.close()


def _seed_second_contact() -> int:
    with with_writer() as conn:
        co = conn.execute("SELECT id FROM companies WHERE slug='acme'").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, persona, focus_area, state) "
            "VALUES (?, 'Bob Jones', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', 'SELECTED')",
            (co,),
        )
        return int(cur.lastrowid)


class TestCompanyStateAdvance:
    def test_advances_to_drafted_when_all_selected_drafted(self):
        cid = _seed_contact()
        assert _company_state() == "SELECTED"
        save_host_draft(cid, Channel.COLD_EMAIL, "Hi Alice, I admire your composites work.")
        assert _company_state() == "DRAFTED"

    def test_stays_selected_until_last_contact_drafted(self):
        c1 = _seed_contact()
        c2 = _seed_second_contact()
        # Draft the first contact only — a second contact is still SELECTED.
        save_host_draft(c1, Channel.COLD_EMAIL, "Hi Alice, I admire your composites work.")
        assert _company_state() == "SELECTED"
        # Draft the last one — now the company advances.
        save_host_draft(c2, Channel.COLD_EMAIL, "Hi Bob, I admire your composites work.")
        assert _company_state() == "DRAFTED"

    def test_does_not_regress_a_non_selected_company(self):
        cid = _seed_contact()
        with with_writer() as conn:
            conn.execute("UPDATE companies SET state = 'APPROVED' WHERE slug = 'acme'")
        save_host_draft(cid, Channel.COLD_EMAIL, "Hi Alice, I admire your composites work.")
        assert _company_state() == "APPROVED"  # guarded to SELECTED — untouched
