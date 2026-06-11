"""
tests/test_marketer_gate.py
Layer 5: marketer refuses to approve HARD_FAIL drafts unless --force is given.
"""

from __future__ import annotations

import pytest

from src.agents.marketer import (
    _contact_has_hard_fail,
    parse_verb,
    run_approval_loop,
)
from src.core.db import get_connection, init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from pathlib import Path

    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", Path(db_path))
    init_db()
    yield db_path


def _seed(quality_codes_by_channel: dict[str, str]) -> tuple[int, int]:
    """Seed one DRAFTED contact with the given quality_codes per channel.

    Returns ``(company_id, contact_id)``.
    """
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
            "linkedin_url, email, hook, state) "
            "VALUES (?, 'A Person', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://linkedin.com/x', 'x@a.com', 'shared', 'DRAFTED')",
            (company_id,),
        )
        contact_id = c.lastrowid
        for channel, code in quality_codes_by_channel.items():
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, "
                "quality_flag, quality_code) VALUES (?, ?, ?, 1, ?, ?)",
                (contact_id, channel, f"draft for {channel}", int(code != "OK"), code),
            )
    return company_id, contact_id


# ---------------------------------------------------------------------------
# parse_verb gains --force tuple element
# ---------------------------------------------------------------------------


class TestParseVerbForce:
    def test_approve_all_default_no_force(self):
        assert parse_verb("APPROVE all") == ("APPROVE_ALL", False)

    def test_approve_all_with_force(self):
        assert parse_verb("APPROVE all --force") == ("APPROVE_ALL", True)

    def test_approve_id_default_no_force(self):
        assert parse_verb("APPROVE 1") == ("APPROVE", 1, False)

    def test_approve_id_with_force(self):
        assert parse_verb("APPROVE 1 --force") == ("APPROVE", 1, True)


# ---------------------------------------------------------------------------
# _contact_has_hard_fail helper
# ---------------------------------------------------------------------------


class TestHardFailDetection:
    def test_no_drafts_is_false(self):
        assert _contact_has_hard_fail({"drafts": []}) is False

    def test_all_ok_is_false(self):
        assert (
            _contact_has_hard_fail(
                {
                    "drafts": [
                        {"quality_code": "OK"},
                        {"quality_code": "OK"},
                    ]
                }
            )
            is False
        )

    def test_any_hard_fail_is_true(self):
        assert (
            _contact_has_hard_fail(
                {
                    "drafts": [
                        {"quality_code": "OK"},
                        {"quality_code": "HARD_FAIL"},
                    ]
                }
            )
            is True
        )

    def test_legacy_null_code_treated_as_ok(self):
        assert (
            _contact_has_hard_fail(
                {
                    "drafts": [
                        {"quality_code": None},
                        {"quality_code": None},
                    ]
                }
            )
            is False
        )

    def test_soft_flag_alone_is_not_hard_fail(self):
        assert (
            _contact_has_hard_fail(
                {
                    "drafts": [
                        {"quality_code": "SOFT_FLAG"},
                    ]
                }
            )
            is False
        )


# ---------------------------------------------------------------------------
# Gate behavior: APPROVE all blocked when any HARD_FAIL present
# ---------------------------------------------------------------------------


class TestApprovalGate:
    def test_approve_all_skips_hard_fail_contact(self, capsys):
        company_id, contact_id = _seed(
            {
                "LINKEDIN_CONNECTION": "HARD_FAIL",
                "LINKEDIN_POST_CONNECTION": "OK",
                "COLD_EMAIL": "OK",
            }
        )

        inputs = iter(["APPROVE all", "SKIP 1"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        # Should NOT appear in approved list — blocked by gate.
        assert contact_id not in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "HARD_FAIL" in out
        assert "refusing to approve" in out.lower()

        # And the DB confirms no outreach_log rows.
        conn = get_connection()
        try:
            logs = conn.execute("SELECT id FROM outreach_log").fetchall()
        finally:
            conn.close()
        assert logs == []

    def test_approve_id_blocked_without_force(self, capsys):
        company_id, contact_id = _seed(
            {
                "LINKEDIN_CONNECTION": "HARD_FAIL",
                "LINKEDIN_POST_CONNECTION": "OK",
            }
        )

        inputs = iter(["APPROVE 1", "SKIP 1"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert contact_id not in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "refusing to approve" in out.lower()

    def test_approve_id_force_overrides_with_warning(self, capsys):
        company_id, contact_id = _seed(
            {
                "LINKEDIN_CONNECTION": "HARD_FAIL",
                "LINKEDIN_POST_CONNECTION": "OK",
            }
        )

        inputs = iter(["APPROVE 1 --force"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        # Force pushes it through.
        assert contact_id in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "--force override" in out
        assert "manually accepted responsibility" in out.lower()

    def test_approve_all_force_clears_all(self, capsys):
        company_id, contact_id = _seed(
            {
                "LINKEDIN_CONNECTION": "HARD_FAIL",
                "LINKEDIN_POST_CONNECTION": "OK",
                "COLD_EMAIL": "HARD_FAIL",
            }
        )

        inputs = iter(["APPROVE all --force"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert contact_id in result.approved_contact_ids
        out = capsys.readouterr().out
        assert "--force override" in out

    def test_ok_contact_approved_normally(self):
        company_id, contact_id = _seed(
            {
                "LINKEDIN_CONNECTION": "OK",
                "LINKEDIN_POST_CONNECTION": "OK",
                "COLD_EMAIL": "SOFT_FLAG",  # soft flag should NOT block
            }
        )

        inputs = iter(["APPROVE all"])
        result = run_approval_loop(company_id, _input_fn=lambda _: next(inputs))

        assert contact_id in result.approved_contact_ids
        conn = get_connection()
        try:
            logs = conn.execute("SELECT id FROM outreach_log").fetchall()
        finally:
            conn.close()
        assert len(logs) == 3
