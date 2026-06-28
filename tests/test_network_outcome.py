"""
tests/test_network_outcome.py
Per-contact outreach outcomes (#15): set, validate, query.
"""

from __future__ import annotations

import argparse

import pytest

from src.cli.network_outcome import (
    list_outcomes,
    run_outcome,
    set_contact_outcome,
)
from src.core.db import get_connection, init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    return tmp_path


def _seed_contact(name: str = "Alice Smith") -> int:
    with with_writer() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'FOUND')"
        )
        co = conn.execute("SELECT id FROM companies WHERE slug='acme'").fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, state) VALUES (?, ?, 'SENT')",
            (co, name),
        )
        return int(cur.lastrowid)


def _row(contact_id: int):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT outcome, outcome_notes, outcome_at FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
    finally:
        conn.close()


class TestSetOutcome:
    def test_default_outcome_is_none(self):
        cid = _seed_contact()
        assert _row(cid)["outcome"] == "NONE"  # migration default

    def test_records_outcome_notes_and_timestamp(self):
        cid = _seed_contact()
        rc = set_contact_outcome(cid, "POC", notes="intro to hiring manager")
        assert rc == 0
        row = _row(cid)
        assert row["outcome"] == "POC"
        assert row["outcome_notes"] == "intro to hiring manager"
        assert row["outcome_at"] is not None  # stamped

    def test_outcome_is_uppercased(self):
        cid = _seed_contact()
        assert set_contact_outcome(cid, "sponsorship_yes") == 0
        assert _row(cid)["outcome"] == "SPONSORSHIP_YES"

    def test_invalid_outcome_rejected(self, capsys):
        cid = _seed_contact()
        rc = set_contact_outcome(cid, "MAYBE")
        assert rc == 1
        assert "Invalid outcome" in capsys.readouterr().out
        assert _row(cid)["outcome"] == "NONE"  # unchanged

    def test_unknown_contact_rejected(self, capsys):
        rc = set_contact_outcome(99999, "REPLIED")
        assert rc == 1
        assert "Contact not found" in capsys.readouterr().out


class TestListOutcomes:
    def test_empty_when_none_recorded(self, capsys):
        _seed_contact()  # outcome stays NONE → excluded
        assert list_outcomes() == 0
        assert "No outcomes recorded yet." in capsys.readouterr().out

    def test_lists_only_recorded_outcomes(self, capsys):
        a = _seed_contact("Alice Smith")
        _seed_contact("Bob Jones")  # left at NONE
        set_contact_outcome(a, "SPONSORSHIP_YES", notes="sponsors H-1B")
        assert list_outcomes() == 0
        out = capsys.readouterr().out
        assert "[SPONSORSHIP_YES] Alice Smith @ acme" in out
        assert "sponsors H-1B" in out
        assert "Bob Jones" not in out  # NONE excluded


class TestRunOutcome:
    def test_list_dispatch(self, capsys):
        rc = run_outcome(argparse.Namespace(list=True, contact_id=None, outcome=None))
        assert rc == 0
        assert "No outcomes recorded yet." in capsys.readouterr().out

    def test_missing_args_errors(self, capsys):
        rc = run_outcome(argparse.Namespace(list=False, contact_id=None, outcome=None))
        assert rc == 1
        assert "Provide <contact_id>" in capsys.readouterr().out

    def test_record_via_run(self):
        cid = _seed_contact()
        rc = run_outcome(
            argparse.Namespace(list=False, contact_id=cid, outcome="REPLIED", notes=None)
        )
        assert rc == 0
        assert _row(cid)["outcome"] == "REPLIED"
