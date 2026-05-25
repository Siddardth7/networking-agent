"""
tests/test_marketer.py
Tests for src/agents/marketer.py — approval loop verb parser and core behaviors.
"""

from __future__ import annotations

import pytest

from src.agents.marketer import parse_verb, run_approval_loop, ApprovalResult
from src.core.db import get_connection, with_writer, init_db
from src.core.schemas import DraftDispatchResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB to an isolated temp file for each test."""
    from pathlib import Path
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", Path(db_path))
    init_db()
    yield db_path


def _seed_company_and_contacts(company_slug="acme"):
    """Insert a company, 2 contacts with drafts, return (company_id, contact_ids)."""
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, 'DRAFTED')",
            (company_slug, "Acme Corp"),
        )
        company_id = cursor.lastrowid

        contact_ids = []
        for i, name in enumerate(["Alice Eng", "Bob Rec"], start=1):
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
                "linkedin_url, email, email_verified, hook, state) "
                "VALUES (?, ?, ?, 'PEER_ENGINEER', 'COMPOSITE_DESIGN', ?, ?, 1, 'Shared UIUC', 'DRAFTED')",
                (company_id, name, f"Engineer {i}", f"https://linkedin.com/{name.lower().replace(' ', '')}", f"{name.lower().replace(' ', '')}@acme.com"),
            )
            contact_id = c.lastrowid
            contact_ids.append(contact_id)

            # Insert 3 drafts per contact (one per channel)
            for channel in ("LINKEDIN_CONNECTION", "LINKEDIN_POST_CONNECTION", "COLD_EMAIL"):
                qflag = 1 if (channel == "COLD_EMAIL" and i == 1) else 0
                conn.execute(
                    "INSERT INTO drafts (contact_id, channel, body, subject, version, quality_flag) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (contact_id, channel, f"Draft body for {channel}", "Subject" if channel == "COLD_EMAIL" else None, qflag),
                )

    return company_id, contact_ids


# ---------------------------------------------------------------------------
# parse_verb tests
# ---------------------------------------------------------------------------

class TestParseVerb:
    def test_approve_all(self):
        assert parse_verb("APPROVE all") == ("APPROVE_ALL",)
        assert parse_verb("approve all") == ("APPROVE_ALL",)
        assert parse_verb("APPROVE ALL") == ("APPROVE_ALL",)

    def test_approve_single_id(self):
        assert parse_verb("APPROVE 1") == ("APPROVE", 1)
        assert parse_verb("approve 42") == ("APPROVE", 42)

    def test_revise(self):
        result = parse_verb('REVISE 2 COLD_EMAIL "Too formal, be casual"')
        assert result == ("REVISE", 2, "COLD_EMAIL", "Too formal, be casual")

    def test_revise_case_insensitive(self):
        result = parse_verb('revise 1 linkedin_connection "Shorter please"')
        assert result == ("REVISE", 1, "LINKEDIN_CONNECTION", "Shorter please")

    def test_skip(self):
        assert parse_verb("SKIP 3") == ("SKIP", 3)
        assert parse_verb("skip 1") == ("SKIP", 1)

    def test_show_raw(self):
        assert parse_verb("SHOW 2 raw") == ("SHOW", 2)
        assert parse_verb("show 1 raw") == ("SHOW", 1)

    def test_quit(self):
        assert parse_verb("quit") == ("QUIT",)
        assert parse_verb("q") == ("QUIT",)
        assert parse_verb("exit") == ("QUIT",)

    def test_unrecognized(self):
        assert parse_verb("FOOBAR") is None
        assert parse_verb("") is None
        assert parse_verb("  ") is None


# ---------------------------------------------------------------------------
# APPROVE writes outreach_log rows
# ---------------------------------------------------------------------------

class TestApproveWritesOutreachLog:
    def test_approve_all_writes_log_rows(self):
        company_id, contact_ids = _seed_company_and_contacts()

        inputs = iter(["APPROVE all"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert set(result.approved_contact_ids) == set(contact_ids)
        assert not result.quit_early

        conn = get_connection()
        logs = conn.execute("SELECT * FROM outreach_log").fetchall()
        conn.close()
        # 2 contacts × 3 channels = 6 log rows
        assert len(logs) == 6
        # All sent_at should be NULL (not yet sent)
        assert all(row["sent_at"] is None for row in logs)

    def test_approve_single_contact_writes_3_log_rows(self):
        company_id, contact_ids = _seed_company_and_contacts()

        inputs = iter(["APPROVE 1", "SKIP 2"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert contact_ids[0] in result.approved_contact_ids
        conn = get_connection()
        logs = conn.execute(
            "SELECT * FROM outreach_log WHERE contact_id = ?",
            (contact_ids[0],),
        ).fetchall()
        conn.close()
        assert len(logs) == 3

    def test_approve_marks_drafts_approved(self):
        company_id, contact_ids = _seed_company_and_contacts()

        inputs = iter(["APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        conn = get_connection()
        drafts = conn.execute(
            "SELECT approved FROM drafts WHERE contact_id IN (?, ?)",
            contact_ids,
        ).fetchall()
        conn.close()
        assert all(d["approved"] == 1 for d in drafts)


# ---------------------------------------------------------------------------
# Quality flag surfaced in rendering
# ---------------------------------------------------------------------------

class TestQualityFlagRendering:
    def test_quality_flag_in_render_output(self, capsys):
        company_id, contact_ids = _seed_company_and_contacts()

        inputs = iter(["APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        captured = capsys.readouterr()
        # First contact has a COLD_EMAIL draft with quality_flag=1
        assert "⚠️" in captured.out
        assert "flagged for quality" in captured.out


# ---------------------------------------------------------------------------
# Company state transition
# ---------------------------------------------------------------------------

class TestCompanyStateTransition:
    def test_company_state_becomes_approved(self):
        company_id, _ = _seed_company_and_contacts()

        inputs = iter(["APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        conn = get_connection()
        row = conn.execute(
            "SELECT state FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        conn.close()
        assert row["state"] == "APPROVED"


# ---------------------------------------------------------------------------
# REVISE verb dispatches
# ---------------------------------------------------------------------------

class TestReviseDispatch:
    def test_revise_calls_dispatch_fn(self):
        company_id, contact_ids = _seed_company_and_contacts()

        dispatch_calls = []

        def fake_dispatch(req):
            dispatch_calls.append(req)
            return DraftDispatchResponse(
                status="OK",
                new_draft_id=999,
                new_version=2,
                body="Revised body",
                subject=None,
                quality_flag=False,
            )

        inputs = iter(['REVISE 1 LINKEDIN_CONNECTION "Make it shorter"', "APPROVE all"])
        run_approval_loop(
            company_id,
            _input_fn=lambda _: next(inputs),
            _dispatch_fn=fake_dispatch,
        )

        assert len(dispatch_calls) == 1
        assert dispatch_calls[0].contact_id == contact_ids[0]
        assert dispatch_calls[0].channel.value == "LINKEDIN_CONNECTION"
        assert dispatch_calls[0].feedback == "Make it shorter"


# ---------------------------------------------------------------------------
# QUIT exits early
# ---------------------------------------------------------------------------

class TestQuit:
    def test_quit_exits_early(self):
        company_id, _ = _seed_company_and_contacts()

        inputs = iter(["quit"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert result.quit_early is True
        assert result.approved_contact_ids == []
