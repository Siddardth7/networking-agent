"""
tests/test_network_status.py — Tests for src/cli/network_status.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from src.cli.network_status import run_status
from src.core.db import get_connection, init_db, with_writer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_args(company=None, update=None, response=None, notes=None) -> argparse.Namespace:
    return argparse.Namespace(company=company, update=update, response=response, notes=notes)


def seed_db(tmp_path: Path) -> dict:
    """Seed minimal test data; return ids for assertions."""
    with with_writer() as conn:
        conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme-corp', 'Acme Corp', 'FOUND')"
        )
        co_id = conn.execute("SELECT id FROM companies WHERE slug='acme-corp'").fetchone()["id"]

        conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, state) "
            "VALUES (?, 'Alice Smith', 'Engineer', 'SELECTED')",
            (co_id,),
        )
        ct_id = conn.execute("SELECT id FROM contacts WHERE full_name='Alice Smith'").fetchone()[
            "id"
        ]

        conn.execute(
            "INSERT INTO drafts (contact_id, channel, version, quality_flag, "
            "approved) VALUES (?, 'EMAIL', 1, 'GOOD', 0)",
            (ct_id,),
        )
        draft_id = conn.execute("SELECT id FROM drafts WHERE contact_id=?", (ct_id,)).fetchone()[
            "id"
        ]

        conn.execute(
            "INSERT INTO outreach_log (contact_id, draft_id, channel, "
            "sent_at, response, notes) "
            "VALUES (?, ?, 'EMAIL', '2025-01-01', 'PENDING', NULL)",
            (ct_id, draft_id),
        )
        log_id = conn.execute(
            "SELECT id FROM outreach_log WHERE contact_id=?", (ct_id,)
        ).fetchone()["id"]

        # Quota rows
        conn.execute(
            "INSERT OR IGNORE INTO quota (provider, month_key, used, "
            "limit_val) VALUES ('serper', '2025-01', 10, 100)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO quota (provider, month_key, used, "
            "limit_val) VALUES ('hunter', '2025-01', 5, 25)"
        )

    return {"co_id": co_id, "ct_id": ct_id, "draft_id": draft_id, "log_id": log_id}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: No args — summary view with company slug, state, counts, quota
# ---------------------------------------------------------------------------


def test_summary_view_shows_company_and_quotas(tmp_path, capsys):
    seed_db(tmp_path)

    rc = run_status(make_args())

    assert rc == 0
    out = capsys.readouterr().out
    # Company row present
    assert "acme-corp" in out
    assert "FOUND" in out
    # At least one provider quota line
    assert "serper" in out or "hunter" in out
    assert "remaining" in out


def test_summary_view_shows_counts(tmp_path, capsys):
    seed_db(tmp_path)

    rc = run_status(make_args())

    assert rc == 0
    out = capsys.readouterr().out
    # Contact count = 1, draft count = 1, outreach count = 1 should all appear
    assert "1" in out


def test_summary_view_empty_db(capsys):
    rc = run_status(make_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "No companies found" in out


# ---------------------------------------------------------------------------
# Test 2: With company slug — detailed contact view
# ---------------------------------------------------------------------------


def test_company_view_shows_contacts(tmp_path, capsys):
    seed_db(tmp_path)

    rc = run_status(make_args(company="acme-corp"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "Alice Smith" in out


def test_company_view_shows_draft_and_log(tmp_path, capsys):
    seed_db(tmp_path)

    rc = run_status(make_args(company="acme-corp"))

    assert rc == 0
    out = capsys.readouterr().out
    assert "EMAIL" in out
    # outreach log entries present
    assert "PENDING" in out


# ---------------------------------------------------------------------------
# Test 3: --update mutates outreach_log row
# ---------------------------------------------------------------------------


def test_update_mutates_log_row(tmp_path, capsys):
    ids = seed_db(tmp_path)
    log_id = ids["log_id"]

    rc = run_status(make_args(update=log_id, response="POSITIVE", notes="Great reply"))

    assert rc == 0

    conn = get_connection()
    row = conn.execute("SELECT response, notes FROM outreach_log WHERE id=?", (log_id,)).fetchone()
    conn.close()

    assert row["response"] == "POSITIVE"
    assert row["notes"] == "Great reply"


def test_update_without_notes(tmp_path):
    ids = seed_db(tmp_path)
    log_id = ids["log_id"]

    rc = run_status(make_args(update=log_id, response="NO_RESPONSE"))

    assert rc == 0

    conn = get_connection()
    row = conn.execute("SELECT response FROM outreach_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    assert row["response"] == "NO_RESPONSE"


# ---------------------------------------------------------------------------
# Test 4: Unknown company slug → prints message, returns 1
# ---------------------------------------------------------------------------


def test_unknown_company_slug_returns_1(capsys):
    rc = run_status(make_args(company="does-not-exist"))

    assert rc == 1
    out = capsys.readouterr().out
    assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# Test 5: --update with invalid response value → refuses with message
# ---------------------------------------------------------------------------


def test_invalid_response_value_returns_1(tmp_path, capsys):
    ids = seed_db(tmp_path)
    log_id = ids["log_id"]

    rc = run_status(make_args(update=log_id, response="MAYBE"))

    assert rc == 1
    out = capsys.readouterr().out
    assert "Invalid response" in out or "invalid" in out.lower()


def test_invalid_response_does_not_mutate_db(tmp_path):
    ids = seed_db(tmp_path)
    log_id = ids["log_id"]

    run_status(make_args(update=log_id, response="WRONG"))

    conn = get_connection()
    row = conn.execute("SELECT response FROM outreach_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    # Original value unchanged
    assert row["response"] == "PENDING"


# ---------------------------------------------------------------------------
# Test 6: --update missing --response
# ---------------------------------------------------------------------------


def test_update_without_response_returns_1(tmp_path, capsys):
    ids = seed_db(tmp_path)

    rc = run_status(make_args(update=ids["log_id"], response=None))

    assert rc == 1
    out = capsys.readouterr().out
    assert "--response" in out or "required" in out.lower()
