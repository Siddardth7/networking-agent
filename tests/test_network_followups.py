"""
tests/test_network_followups.py
Timed multi-touch follow-ups (#17, A7): pure planning + DB scheduling.

Covers the cap ("never past the cap"), the marketer-artifact gate, the no-reply
condition, the 4-7 day cadence, and the CLI dispatch.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import pytest

from src.cli.network_followups import (
    FollowupPlan,
    _collect_rows,
    _parse_ts,
    list_followups,
    plan_followups,
    run_followups,
    schedule_followups,
)
from src.core.db import get_connection, init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    return tmp_path


# --------------------------------------------------------------------------- #
# Pure planner
# --------------------------------------------------------------------------- #

LAST = datetime(2026, 6, 1, 9, 0, 0)


def _row(**overrides):
    base = {
        "outreach_log_id": 1,
        "last_touch_at": LAST,
        "sent_followups": 0,
        "pending_followups": 0,
        "responded": False,
        "gated": True,
    }
    base.update(overrides)
    return base


class TestPlanFollowups:
    def test_due_row_is_scheduled_at_gap(self):
        plans = plan_followups([_row()], max_touches=2, gap_days=5)
        assert plans == [
            FollowupPlan(outreach_log_id=1, scheduled_at=LAST + timedelta(days=5), touch_number=1)
        ]

    def test_gap_lands_in_4_to_7_window(self):
        for gap in (4, 5, 6, 7):
            plans = plan_followups([_row()], max_touches=2, gap_days=gap)
            assert plans[0].scheduled_at == LAST + timedelta(days=gap)

    def test_ungated_is_skipped(self):
        assert plan_followups([_row(gated=False)], max_touches=2, gap_days=5) == []

    def test_responded_is_skipped(self):
        assert plan_followups([_row(responded=True)], max_touches=2, gap_days=5) == []

    def test_pending_followup_blocks_double_booking(self):
        assert plan_followups([_row(pending_followups=1)], max_touches=2, gap_days=5) == []

    def test_cap_reached_is_skipped(self):
        # 2 already sent, cap 2 → nothing more scheduled (never past the cap)
        assert plan_followups([_row(sent_followups=2)], max_touches=2, gap_days=5) == []

    def test_under_cap_increments_touch_number(self):
        plans = plan_followups([_row(sent_followups=1)], max_touches=3, gap_days=5)
        assert plans[0].touch_number == 2

    def test_missing_last_touch_is_skipped(self):
        assert plan_followups([_row(last_touch_at=None)], max_touches=2, gap_days=5) == []

    def test_zero_cap_schedules_nothing(self):
        assert plan_followups([_row()], max_touches=0, gap_days=5) == []

    def test_mixed_batch_only_due_rows(self):
        rows = [
            _row(outreach_log_id=1),  # due
            _row(outreach_log_id=2, responded=True),  # skip
            _row(outreach_log_id=3, gated=False),  # skip
            _row(outreach_log_id=4, sent_followups=2),  # skip (cap)
        ]
        plans = plan_followups(rows, max_touches=2, gap_days=5)
        assert [p.outreach_log_id for p in plans] == [1]


# --------------------------------------------------------------------------- #
# _parse_ts
# --------------------------------------------------------------------------- #


class TestParseTs:
    def test_sqlite_format(self):
        assert _parse_ts("2026-06-01 09:00:00") == datetime(2026, 6, 1, 9, 0, 0)

    def test_iso_format(self):
        assert _parse_ts("2026-06-01T09:00:00") == datetime(2026, 6, 1, 9, 0, 0)

    def test_none(self):
        assert _parse_ts(None) is None

    def test_unparseable(self):
        assert _parse_ts("not-a-date") is None


# --------------------------------------------------------------------------- #
# DB seeding helpers
# --------------------------------------------------------------------------- #


def _seed_outreach(
    *,
    company_state: str = "APPROVED",
    response: str = "PENDING",
    outcome: str = "NONE",
    sent_at: str = "2026-06-01 09:00:00",
    slug: str = "acme",
) -> int:
    """Create company→contact→outreach_log and return the outreach_log id."""
    with with_writer() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name, state) VALUES (?, ?, ?)",
            (slug, slug.title(), company_state),
        )
        co = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, state, outcome) "
            "VALUES (?, 'Alice Smith', 'SENT', ?)",
            (co, outcome),
        )
        contact_id = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO outreach_log (contact_id, channel, sent_at, response) "
            "VALUES (?, 'EMAIL', ?, ?)",
            (contact_id, sent_at, response),
        )
        return int(cur.lastrowid)


def _add_followup(outreach_log_id: int, *, scheduled_at: str, sent_at: str | None = None) -> None:
    with with_writer() as conn:
        conn.execute(
            "INSERT INTO followups (outreach_log_id, scheduled_at, sent_at) VALUES (?, ?, ?)",
            (outreach_log_id, scheduled_at, sent_at),
        )


def _count_followups() -> int:
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM followups").fetchone()["n"]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# _collect_rows
# --------------------------------------------------------------------------- #


class TestCollectRows:
    def test_approved_no_reply_is_gated_and_unresponded(self):
        _seed_outreach()
        rows = _collect_rows()
        assert len(rows) == 1
        assert rows[0]["gated"] is True
        assert rows[0]["responded"] is False
        assert rows[0]["last_touch_at"] == datetime(2026, 6, 1, 9, 0, 0)

    def test_drafted_company_not_gated(self):
        _seed_outreach(company_state="DRAFTED")
        assert _collect_rows()[0]["gated"] is False

    def test_response_marks_responded(self):
        _seed_outreach(response="REPLIED")
        assert _collect_rows()[0]["responded"] is True

    def test_outcome_marks_responded(self):
        _seed_outreach(outcome="POC")
        assert _collect_rows()[0]["responded"] is True

    def test_last_touch_uses_latest_sent_followup(self):
        oid = _seed_outreach()
        _add_followup(oid, scheduled_at="2026-06-06 09:00:00", sent_at="2026-06-07 10:00:00")
        rows = _collect_rows()
        assert rows[0]["last_touch_at"] == datetime(2026, 6, 7, 10, 0, 0)
        assert rows[0]["sent_followups"] == 1

    def test_pending_followup_counted(self):
        oid = _seed_outreach()
        _add_followup(oid, scheduled_at="2026-06-06 09:00:00", sent_at=None)
        rows = _collect_rows()
        assert rows[0]["pending_followups"] == 1
        assert rows[0]["sent_followups"] == 0


# --------------------------------------------------------------------------- #
# schedule_followups (end-to-end through the DB)
# --------------------------------------------------------------------------- #


class TestScheduleFollowups:
    def test_schedules_for_no_reply_approved(self):
        _seed_outreach()
        rc = schedule_followups()
        assert rc == 0
        assert _count_followups() == 1
        conn = get_connection()
        try:
            row = conn.execute("SELECT scheduled_at, sent_at FROM followups").fetchone()
        finally:
            conn.close()
        # gap default 5 → 2026-06-06; queued unsent
        assert row["scheduled_at"].startswith("2026-06-06")
        assert row["sent_at"] is None

    def test_nothing_due_prints_and_skips(self, capsys):
        _seed_outreach(response="REPLIED")
        rc = schedule_followups()
        assert rc == 0
        assert _count_followups() == 0
        assert "No follow-ups due." in capsys.readouterr().out

    def test_rerun_does_not_double_schedule(self):
        _seed_outreach()
        schedule_followups()
        schedule_followups()  # pending follow-up now exists → no duplicate
        assert _count_followups() == 1

    def test_never_exceeds_cap(self):
        # cap is 2: one sent + one pending already → no new schedule
        oid = _seed_outreach()
        _add_followup(oid, scheduled_at="2026-06-06 09:00:00", sent_at="2026-06-07 10:00:00")
        _add_followup(oid, scheduled_at="2026-06-12 09:00:00", sent_at="2026-06-13 10:00:00")
        schedule_followups()
        assert _count_followups() == 2  # cap reached, nothing added

    def test_ungated_company_gets_no_followup(self):
        _seed_outreach(company_state="DRAFTED")
        schedule_followups()
        assert _count_followups() == 0


# --------------------------------------------------------------------------- #
# list_followups + dispatch
# --------------------------------------------------------------------------- #


class TestListAndDispatch:
    def test_list_empty(self, capsys):
        assert list_followups() == 0
        assert "No follow-ups scheduled yet." in capsys.readouterr().out

    def test_list_shows_pending_and_sent(self, capsys):
        oid = _seed_outreach()
        _add_followup(oid, scheduled_at="2026-06-06 09:00:00", sent_at=None)
        _add_followup(oid, scheduled_at="2026-06-01 09:00:00", sent_at="2026-06-02 09:00:00")
        assert list_followups() == 0
        out = capsys.readouterr().out
        assert "[PENDING]" in out
        assert "[SENT]" in out
        assert "Alice Smith @ acme" in out

    def test_dispatch_list(self, capsys):
        run_followups(argparse.Namespace(list=True))
        assert "No follow-ups scheduled yet." in capsys.readouterr().out

    def test_dispatch_schedule(self):
        _seed_outreach()
        rc = run_followups(argparse.Namespace(list=False))
        assert rc == 0
        assert _count_followups() == 1
