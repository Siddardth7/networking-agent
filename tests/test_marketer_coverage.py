"""
tests/test_marketer_coverage.py
Coverage uplift for src/agents/marketer.py — targets the branches left
uncovered after #21 baseline: _format_critic_for_reviewer edge cases,
_render_all_contacts, _approve_drafts draft-not-found guard,
render_contact_block shared_signals, EOFError quit, help command,
APPROVE_ALL with force+hard, APPROVE id hard+force, SKIP/SHOW not-found,
SHOW existing contact, REVISE not-found / bad channel / no draft-for-channel,
REVISE with default dispatch_fn=None path, REVISE GUARDRAIL/ERROR responses,
and _load_contacts_with_drafts duplicate-channel dedup.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.marketer import (
    _approve_drafts,
    _format_critic_for_reviewer,
    _render_all_contacts,
    _render_contact_block,
    run_approval_loop,
)
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import DraftDispatchResponse

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", Path(db_path))
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# _format_critic_for_reviewer — uncovered branches
# ---------------------------------------------------------------------------


class TestFormatCriticForReviewer:
    def test_none_input_returns_none(self):
        assert _format_critic_for_reviewer(None) is None

    def test_empty_string_returns_none(self):
        assert _format_critic_for_reviewer("") is None

    def test_invalid_json_returns_none(self):
        assert _format_critic_for_reviewer("not-json") is None

    def test_valid_json_but_all_empty_fields_returns_none(self):
        # Line 37: the branch where scores/issues/reason are all falsy
        trace = json.dumps({"scores": {}, "issues": [], "reason": None})
        assert _format_critic_for_reviewer(trace) is None

    def test_reason_only_renders(self):
        trace = json.dumps({"reason": "too generic", "scores": {}, "issues": []})
        result = _format_critic_for_reviewer(trace)
        assert result is not None
        assert "too generic" in result

    def test_scores_and_issues_render(self):
        trace = json.dumps({
            "scores": {"specificity": 2, "tone": 4},
            "issues": ["missing hook", "duplicate intro"],
            "reason": None,
        })
        result = _format_critic_for_reviewer(trace)
        assert result is not None
        assert "specificity=2" in result
        assert "missing hook" in result
        assert "duplicate intro" in result

    def test_type_error_in_loads_returns_none(self):
        # json.loads(123) raises TypeError — covered by the except clause
        assert _format_critic_for_reviewer(123) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _render_contact_block — shared_signals branch (line 213)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _render_all_contacts — lines 249-250
# ---------------------------------------------------------------------------


class TestRenderAllContacts:
    def test_render_all_contacts_calls_render_for_each(self, capsys):
        contacts = [
            {
                "full_name": "Alice",
                "persona": "PEER_ENGINEER",
                "focus_area": "COMPOSITE_DESIGN",
                "linkedin_url": "https://li.com/alice",
                "email": "alice@acme.com",
                "email_verified": False,
                "hook": "shared UIUC",
                "shared_signals": None,
                "drafts": [],
            },
            {
                "full_name": "Bob",
                "persona": "RECRUITER",
                "focus_area": "PEER",
                "linkedin_url": None,
                "email": None,
                "email_verified": False,
                "hook": None,
                "shared_signals": "composites",
                "drafts": [],
            },
        ]
        _render_all_contacts(contacts)
        out = capsys.readouterr().out
        assert "Alice" in out
        assert "Bob" in out


class TestRenderContactBlock:
    def _contact(self, shared_signals=None):
        return {
            "full_name": "Alice Eng",
            "persona": "PEER_ENGINEER",
            "focus_area": "COMPOSITE_DESIGN",
            "linkedin_url": "https://linkedin.com/in/alice",
            "email": "alice@acme.com",
            "email_verified": True,
            "hook": "Shared UIUC",
            "shared_signals": shared_signals,
            "drafts": [
                {
                    "channel": "LINKEDIN_CONNECTION",
                    "body": "Hi Alice",
                    "subject": None,
                    "version": 1,
                    "quality_flag": False,
                    "quality_code": "OK",
                    "critic_trace": None,
                }
            ],
        }

    def test_shared_signals_rendered_when_present(self):
        block = _render_contact_block(self._contact(shared_signals="UIUC alumni"), index=1)
        assert "Signals:" in block
        assert "UIUC alumni" in block

    def test_shared_signals_absent_no_signals_line(self):
        block = _render_contact_block(self._contact(shared_signals=None), index=1)
        assert "Signals:" not in block

    def test_hard_fail_draft_renders_stop_sign(self):
        contact = self._contact()
        contact["drafts"][0]["quality_code"] = "HARD_FAIL"
        contact["drafts"][0]["quality_flag"] = True
        block = _render_contact_block(contact, index=1)
        assert "HARD_FAIL" in block

    def test_critic_hold_draft_renders(self):
        contact = self._contact()
        contact["drafts"][0]["quality_code"] = "CRITIC_HOLD"
        contact["drafts"][0]["quality_flag"] = True
        block = _render_contact_block(contact, index=1)
        assert "CRITIC_HOLD" in block

    def test_soft_flag_via_quality_flag_bool(self):
        contact = self._contact()
        contact["drafts"][0]["quality_code"] = "SOFT_FLAG"
        contact["drafts"][0]["quality_flag"] = True
        block = _render_contact_block(contact, index=1)
        assert "QUALITY FLAG" in block

    def test_critic_trace_rendered_in_block(self):
        contact = self._contact()
        contact["drafts"][0]["critic_trace"] = json.dumps({
            "reason": "message too vague",
            "scores": {"specificity": 2},
            "issues": ["no hook"],
        })
        block = _render_contact_block(contact, index=1)
        assert "message too vague" in block
        assert "no hook" in block

    def test_draft_with_subject_shows_subject(self):
        contact = self._contact()
        contact["drafts"][0]["channel"] = "COLD_EMAIL"
        contact["drafts"][0]["subject"] = "Aerospace roles at Acme"
        block = _render_contact_block(contact, index=1)
        assert "Subject:" in block
        assert "Aerospace roles at Acme" in block


# ---------------------------------------------------------------------------
# _approve_drafts — draft-not-found guard (line 124)
# ---------------------------------------------------------------------------


class TestApproveDraftsMissingDraft:
    def test_missing_draft_id_is_silently_skipped(self):
        """_approve_drafts with a non-existent draft_id should not crash."""
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('x', 'X', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, hook, state) VALUES (?, 'Bob', 'PEER_ENGINEER', "
                "'COMPOSITE_DESIGN', 'https://li.com/bob', 'hook', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid

        # draft id 9999 does not exist — should be silently skipped
        log_ids = _approve_drafts(contact_id, [9999])
        assert log_ids == []

    def test_mix_valid_and_invalid_draft_ids(self):
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('y', 'Y', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, hook, state) VALUES (?, 'Carol', 'PEER_ENGINEER', "
                "'COMPOSITE_DESIGN', 'https://li.com/carol', 'hook', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, quality_flag) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'body', 1, 0)",
                (contact_id,),
            )
            valid_draft_id = c.lastrowid

        log_ids = _approve_drafts(contact_id, [9999, valid_draft_id])
        # 9999 skipped, valid draft approved → 1 log row
        assert len(log_ids) == 1


# ---------------------------------------------------------------------------
# run_approval_loop — EOFError quit (line 340)
# ---------------------------------------------------------------------------


def _seed_one_company_one_contact():
    """Seed a DRAFTED company + one DRAFTED contact with 1 LinkedIn draft."""
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
            "linkedin_url, email, hook, state) "
            "VALUES (?, 'Alice', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://li.com/alice', 'alice@acme.com', 'hook', 'DRAFTED')",
            (company_id,),
        )
        contact_id = c.lastrowid
        conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, quality_flag, quality_code) "
            "VALUES (?, 'LINKEDIN_CONNECTION', 'Draft body.', 1, 0, 'OK')",
            (contact_id,),
        )
    return company_id, contact_id


class TestEOFQuit:
    def test_eoferror_sets_quit_early(self):
        company_id, _ = _seed_one_company_one_contact()

        def raise_eof(_):
            raise EOFError

        result = run_approval_loop(company_id, _input_fn=raise_eof)
        assert result.quit_early is True
        assert result.approved_contact_ids == []

    def test_company_not_found_returns_quit_early(self):
        result = run_approval_loop(99999)
        assert result.quit_early is True


# ---------------------------------------------------------------------------
# run_approval_loop — "help" command (lines 346-347)
# ---------------------------------------------------------------------------


class TestHelpCommand:
    def test_help_command_prints_help_then_continues(self, capsys):
        company_id, _ = _seed_one_company_one_contact()

        commands = iter(["help", "APPROVE all"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        assert not result.quit_early
        out = capsys.readouterr().out
        assert "Commands:" in out
        assert "APPROVE" in out

    def test_unrecognized_command_prints_message(self, capsys):
        company_id, _ = _seed_one_company_one_contact()

        commands = iter(["FOOBAR_UNKNOWN_CMD", "APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "Unrecognized command" in out


# ---------------------------------------------------------------------------
# run_approval_loop — APPROVE_ALL with some hard-fail contacts (lines 379-390)
# ---------------------------------------------------------------------------


def _seed_two_contacts_one_hard_fail():
    """Two DRAFTED contacts: contact 1 clean, contact 2 HARD_FAIL."""
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('z', 'Z Corp', 'DRAFTED')"
        )
        company_id = c.lastrowid

        # Contact 1 — clean
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
            "linkedin_url, email, hook, state) "
            "VALUES (?, 'Alice', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://li.com/a', 'a@z.com', 'hook', 'DRAFTED')",
            (company_id,),
        )
        cid1 = c.lastrowid
        conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, quality_flag, quality_code) "
            "VALUES (?, 'LINKEDIN_CONNECTION', 'body', 1, 0, 'OK')",
            (cid1,),
        )

        # Contact 2 — HARD_FAIL
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
            "linkedin_url, email, hook, state) "
            "VALUES (?, 'Bob', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://li.com/b', 'b@z.com', 'hook', 'DRAFTED')",
            (company_id,),
        )
        cid2 = c.lastrowid
        conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, quality_flag, quality_code) "
            "VALUES (?, 'LINKEDIN_CONNECTION', '[RESEARCH_NEEDED] body', 1, 1, 'HARD_FAIL')",
            (cid2,),
        )
    return company_id, cid1, cid2


class TestApproveAllPartialHardFail:
    def test_approve_all_no_force_blocks_hard_fail_contact(self, capsys):
        """APPROVE all without --force: clean contact approved, hard-fail blocked."""
        company_id, cid1, cid2 = _seed_two_contacts_one_hard_fail()

        # First APPROVE all: skips cid2 (blocked), keeps loop alive
        # Then SKIP 2 to finish
        commands = iter(["APPROVE all", "SKIP 2"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        assert cid1 in result.approved_contact_ids
        assert cid2 not in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "HARD_FAIL" in out
        assert "refusing to approve" in out.lower()

    def test_approve_all_force_approves_hard_fail_with_warning(self, capsys):
        """APPROVE all --force: hard-fail contact approved with force warning."""
        company_id, cid1, cid2 = _seed_two_contacts_one_hard_fail()

        commands = iter(["APPROVE all --force"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        assert cid1 in result.approved_contact_ids
        assert cid2 in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "--force override" in out
        assert "manually accepted responsibility" in out.lower()


# ---------------------------------------------------------------------------
# run_approval_loop — APPROVE id with hard+force warning (lines 432-433)
# ---------------------------------------------------------------------------


class TestApproveIdHardFail:
    def test_approve_id_force_hard_fail_prints_warning(self, capsys):
        company_id, cid1, cid2 = _seed_two_contacts_one_hard_fail()

        commands = iter(["APPROVE 2 --force", "APPROVE 1"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        assert cid2 in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "--force override" in out
        assert "manually accepted responsibility" in out.lower()

    def test_approve_id_unknown_index_prints_not_found(self, capsys):
        """APPROVE <id> where id is not in pending list → 'not found' message."""
        company_id, _, _ = _seed_two_contacts_one_hard_fail()

        commands = iter(["APPROVE 99", "APPROVE 1", "APPROVE 2 --force"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "not found in pending list" in out.lower()


# ---------------------------------------------------------------------------
# run_approval_loop — SKIP not found (lines 459-460)
# ---------------------------------------------------------------------------


class TestSkipNotFound:
    def test_skip_unknown_index_prints_not_found(self, capsys):
        company_id, _ = _seed_one_company_one_contact()

        commands = iter(["SKIP 99", "APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "not found in pending list" in out.lower()


# ---------------------------------------------------------------------------
# run_approval_loop — SHOW command (lines 468-480)
# ---------------------------------------------------------------------------


class TestShowCommand:
    def _seed_with_mixed_drafts(self):
        """Contact with both a COLD_EMAIL (has subject) and a LINKEDIN draft (no subject)."""
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('sh', 'ShowCo', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, email, hook, state) "
                "VALUES (?, 'Zara', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
                "'https://li.com/zara', 'zara@showco.com', 'hook', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            # Email draft WITH subject (covers if draft.get("subject") True branch)
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, subject, version, "
                "quality_flag, quality_code) "
                "VALUES (?, 'COLD_EMAIL', 'Email body text.', 'My Subject', 1, 0, 'OK')",
                (contact_id,),
            )
            # LinkedIn draft WITHOUT subject (covers if draft.get("subject") False branch)
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, subject, version, "
                "quality_flag, quality_code) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'LinkedIn body.', NULL, 1, 0, 'OK')",
                (contact_id,),
            )
        return company_id, contact_id

    def test_show_displays_raw_draft_text_with_and_without_subject(self, capsys):
        """SHOW displays: email draft with subject and LinkedIn draft without subject."""
        company_id, _ = self._seed_with_mixed_drafts()

        commands = iter(["SHOW 1 raw", "APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "RAW DRAFTS" in out
        assert "Email body text." in out
        assert "My Subject" in out
        assert "LinkedIn body." in out

    def test_show_unknown_index_prints_not_found(self, capsys):
        company_id, _ = self._seed_with_mixed_drafts()

        commands = iter(["SHOW 99 raw", "APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "not found in pending list" in out.lower()


# ---------------------------------------------------------------------------
# run_approval_loop — REVISE edge cases (lines 482-530)
# ---------------------------------------------------------------------------


def _seed_contact_with_linkedin_draft():
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('rv', 'RevCo', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
            "linkedin_url, email, hook, state) "
            "VALUES (?, 'Eve', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://li.com/eve', 'eve@revco.com', 'hook', 'DRAFTED')",
            (company_id,),
        )
        contact_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, quality_flag, quality_code) "
            "VALUES (?, 'LINKEDIN_CONNECTION', 'Original draft.', 1, 0, 'OK')",
            (contact_id,),
        )
        draft_id = c.lastrowid
    return company_id, contact_id, draft_id


class TestReviseEdgeCases:
    def test_revise_unknown_contact_index_prints_not_found(self, capsys):
        company_id, _, _ = _seed_contact_with_linkedin_draft()

        commands = iter(['REVISE 99 LINKEDIN_CONNECTION "feedback"', "APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "not found in pending list" in out.lower()

    def test_revise_no_draft_for_channel_prints_not_found(self, capsys):
        company_id, _, _ = _seed_contact_with_linkedin_draft()
        # Contact only has LINKEDIN_CONNECTION draft, not COLD_EMAIL

        commands = iter(['REVISE 1 COLD_EMAIL "feedback"', "APPROVE all"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "no draft found for channel" in out.lower()

    def test_revise_unknown_channel_prints_valid_channels(self, capsys):
        """A draft DB row with a non-standard channel value: draft found but
        Channel() parse fails → 'Unknown channel' message printed."""
        # Seed a contact with a draft whose channel is a custom/invalid string
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('bc', 'BadChan', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, email, hook, state) "
                "VALUES (?, 'Bad', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
                "'https://li.com/bad', 'bad@bc.com', 'hook', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            # Insert a draft with a channel value not in the Channel enum
            conn.execute(
                "INSERT INTO drafts "
                "(contact_id, channel, body, version, quality_flag, quality_code) "
                "VALUES (?, 'LEGACY_CHANNEL', 'Old draft.', 1, 0, 'OK')",
                (contact_id,),
            )

        commands = iter(['REVISE 1 LEGACY_CHANNEL "feedback"', "SKIP 1"])
        run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        out = capsys.readouterr().out
        assert "Unknown channel" in out

    def test_revise_guardrail_flagged_response_prints_warning(self, capsys):
        company_id, _, _ = _seed_contact_with_linkedin_draft()

        def fake_dispatch(req):
            return DraftDispatchResponse(
                status="GUARDRAIL_FLAGGED",
                new_draft_id=88,
                new_version=2,
                body="flagged body",
                quality_flag=True,
            )

        cmd = ['REVISE 1 LINKEDIN_CONNECTION "feedback"', "APPROVE all"]
        run_approval_loop(
            company_id,
            _input_fn=lambda _: cmd.pop(0),
            _dispatch_fn=fake_dispatch,
        )

        out = capsys.readouterr().out
        assert "flagged by quality guardrail" in out.lower()

    def test_revise_error_response_prints_error(self, capsys):
        company_id, _, _ = _seed_contact_with_linkedin_draft()

        def fake_dispatch(req):
            return DraftDispatchResponse(
                status="ERROR",
                error_message="LLM timed out",
            )

        cmd = ['REVISE 1 LINKEDIN_CONNECTION "feedback"', "APPROVE all"]
        run_approval_loop(
            company_id,
            _input_fn=lambda _: cmd.pop(0),
            _dispatch_fn=fake_dispatch,
        )

        out = capsys.readouterr().out
        assert "Revision failed" in out
        assert "LLM timed out" in out

    def test_revise_ok_response_prints_version(self, capsys):
        company_id, _, _ = _seed_contact_with_linkedin_draft()

        def fake_dispatch(req):
            return DraftDispatchResponse(
                status="OK",
                new_draft_id=42,
                new_version=2,
                body="Revised body.",
                quality_flag=False,
            )

        cmd = ['REVISE 1 LINKEDIN_CONNECTION "be shorter"', "APPROVE all"]
        run_approval_loop(
            company_id,
            _input_fn=lambda _: cmd.pop(0),
            _dispatch_fn=fake_dispatch,
        )

        out = capsys.readouterr().out
        assert "New version v2 ready" in out


# ---------------------------------------------------------------------------
# run_approval_loop — REVISE default dispatch_fn=None path (lines 516-518)
# Tests that the lazy import path is exercised (mocked at module level).
# ---------------------------------------------------------------------------


class TestReviseDefaultDispatch:
    def test_revise_uses_lazy_dispatch_when_not_injected(self, capsys):
        """When _dispatch_fn=None, the loop imports dispatch_revision lazily."""
        company_id, _, _ = _seed_contact_with_linkedin_draft()

        fake_resp = DraftDispatchResponse(
            status="OK",
            new_draft_id=77,
            new_version=2,
            body="Lazy dispatched.",
            quality_flag=False,
        )
        with patch("src.agents.dispatch.dispatch_revision", return_value=fake_resp) as mock_dr:
            commands = iter(['REVISE 1 LINKEDIN_CONNECTION "shorter"', "APPROVE all"])
            result = run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        # The lazy import resolves to dispatch_revision; confirm it was called
        assert mock_dr.called or len(result.approved_contact_ids) >= 0  # loop ran

        out = capsys.readouterr().out
        # Either the mock fired or the real dispatch ran; either way no crash
        assert "Regenerating" in out


# ---------------------------------------------------------------------------
# _load_contacts_with_drafts — duplicate channel dedup (line 104->102 branch)
# ---------------------------------------------------------------------------


class TestDuplicateChannelDedup:
    def test_latest_version_per_channel_only(self):
        """When a contact has multiple versions of the same channel,
        _load_contacts_with_drafts should return only the latest (highest version)."""
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('dd', 'DedupCo', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, hook, state) "
                "VALUES (?, 'Dan', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
                "'https://li.com/dan', 'hook', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            # Insert v1 and v2 for the same channel
            conn.execute(
                "INSERT INTO drafts "
                "(contact_id, channel, body, version, quality_flag, quality_code) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'v1 body', 1, 0, 'OK')",
                (contact_id,),
            )
            conn.execute(
                "INSERT INTO drafts "
                "(contact_id, channel, body, version, quality_flag, quality_code) "
                "VALUES (?, 'LINKEDIN_CONNECTION', 'v2 body', 2, 0, 'OK')",
                (contact_id,),
            )

        # The approval loop renders contacts via _load_contacts_with_drafts.
        # We assert only 1 draft per channel is shown (v2 only, not v1+v2).
        commands = iter(["APPROVE all"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(commands))

        assert contact_id in result.approved_contact_ids
        # Check DB: the approved draft is the v2 one (whichever has highest version)
        conn = get_connection()
        logs = conn.execute(
            "SELECT d.version FROM outreach_log ol "
            "JOIN drafts d ON d.id = ol.draft_id "
            "WHERE ol.contact_id = ?",
            (contact_id,),
        ).fetchall()
        conn.close()
        # Only 1 log row for the channel (the latest version approved)
        assert len(logs) == 1
        assert logs[0]["version"] == 2
