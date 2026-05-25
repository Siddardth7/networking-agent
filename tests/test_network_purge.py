"""
tests/test_network_purge.py — Tests for src/cli/network_purge.py

Covers:
1. Purge contact → matching rows in contacts/drafts/outreach_log all deleted
2. --all without --confirm → refuses (returns 1), no DB changes
3. Audit log line written with correct content
4. Purge company → drafts dir removed
5. No flag → refuses with message
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import src.core.db as db_module
from src.core.db import init_db, with_writer, get_connection
from src.cli.network_purge import run_purge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    *,
    contact: int | None = None,
    company: str | None = None,
    all: bool = False,
    confirm: bool = False,
) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for run_purge."""
    return argparse.Namespace(
        contact=contact,
        company=company,
        all=all,
        confirm=confirm,
    )


def _seed_contact(company_slug: str = "acme") -> tuple[int, int, int, int]:
    """Insert one company, one contact, one draft, one outreach_log row.

    Returns (company_id, contact_id, draft_id, outreach_id).
    """
    with with_writer() as conn:
        conn.execute(
            "INSERT INTO companies (slug, name) VALUES (?, ?)",
            (company_slug, "Acme Corp"),
        )
        company_id = conn.execute(
            "SELECT id FROM companies WHERE slug = ?", (company_slug,)
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO contacts (company_id, full_name) VALUES (?, ?)",
            (company_id, "Alice Smith"),
        )
        contact_id = conn.execute(
            "SELECT id FROM contacts WHERE company_id = ?", (company_id,)
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO drafts (contact_id, channel, body) VALUES (?, ?, ?)",
            (contact_id, "email", "Hello Alice"),
        )
        draft_id = conn.execute(
            "SELECT id FROM drafts WHERE contact_id = ?", (contact_id,)
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO outreach_log (contact_id, draft_id, channel) VALUES (?, ?, ?)",
            (contact_id, draft_id, "email"),
        )
        outreach_id = conn.execute(
            "SELECT id FROM outreach_log WHERE contact_id = ?", (contact_id,)
        ).fetchone()["id"]

    return company_id, contact_id, draft_id, outreach_id


def _count(table: str, where: str, value: int) -> int:
    """Return the row count for a simple WHERE condition."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where} = ?", (value,)
        ).fetchone()
        return row[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test in its own SQLite database."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    init_db()
    return db_path


# ---------------------------------------------------------------------------
# Test 1: Purge contact deletes rows in contacts, drafts, outreach_log
# ---------------------------------------------------------------------------

class TestPurgeContact:
    def test_contact_rows_deleted(self, tmp_path: Path) -> None:
        """Purging a contact must hard-delete its contacts/drafts/outreach_log rows."""
        _company_id, contact_id, draft_id, outreach_id = _seed_contact()

        log_path = tmp_path / "purge.log"
        args = _make_args(contact=contact_id)
        rc = run_purge(args, _log_path=log_path)

        assert rc == 0
        assert _count("contacts", "id", contact_id) == 0
        assert _count("drafts", "contact_id", contact_id) == 0
        assert _count("outreach_log", "contact_id", contact_id) == 0

    def test_only_target_contact_deleted(self, tmp_path: Path) -> None:
        """Purging contact A must not delete contact B's rows."""
        _, contact_a, _, _ = _seed_contact("acme")
        _, contact_b, _, _ = _seed_contact("globex")

        log_path = tmp_path / "purge.log"
        args = _make_args(contact=contact_a)
        run_purge(args, _log_path=log_path)

        assert _count("contacts", "id", contact_a) == 0
        assert _count("contacts", "id", contact_b) == 1


# ---------------------------------------------------------------------------
# Test 2: --all without --confirm refuses, returns 1, no DB changes
# ---------------------------------------------------------------------------

class TestAllWithoutConfirm:
    def test_refuses_without_confirm(self, tmp_path: Path, capsys) -> None:
        """--all without --confirm must return 1 and print refusal message."""
        _seed_contact()

        log_path = tmp_path / "purge.log"
        args = _make_args(all=True, confirm=False)
        rc = run_purge(args, _log_path=log_path)

        assert rc == 1
        captured = capsys.readouterr()
        assert "Use --all --confirm to purge all data." in captured.out

    def test_no_db_changes_on_refusal(self, tmp_path: Path) -> None:
        """When --all is refused, the database must remain untouched."""
        _, contact_id, _, _ = _seed_contact()

        log_path = tmp_path / "purge.log"
        args = _make_args(all=True, confirm=False)
        run_purge(args, _log_path=log_path)

        # Contact row must still exist.
        assert _count("contacts", "id", contact_id) == 1

    def test_no_audit_log_on_refusal(self, tmp_path: Path) -> None:
        """A refused --all must not write to the audit log."""
        log_path = tmp_path / "purge.log"
        args = _make_args(all=True, confirm=False)
        run_purge(args, _log_path=log_path)

        assert not log_path.exists()


# ---------------------------------------------------------------------------
# Test 3: Audit log line written with correct content
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_contact_audit_line(self, tmp_path: Path) -> None:
        """Purging a contact writes the correct audit line format."""
        _, contact_id, _, _ = _seed_contact()
        log_path = tmp_path / "purge.log"

        args = _make_args(contact=contact_id)
        run_purge(args, _log_path=log_path)

        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        # Format: <ISO timestamp> | purged contact=<id> reason=user-request
        assert f"purged contact={contact_id} reason=user-request" in lines[0]
        assert "|" in lines[0]

    def test_company_audit_line(self, tmp_path: Path) -> None:
        """Purging a company writes the correct audit line format."""
        _seed_contact("beta-corp")
        log_path = tmp_path / "purge.log"
        drafts_dir = tmp_path / "drafts"

        args = _make_args(company="beta-corp")
        run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert "purged company=beta-corp reason=user-request" in lines[0]

    def test_all_audit_line(self, tmp_path: Path) -> None:
        """Purging all data writes 'purged all reason=user-request'."""
        _seed_contact()
        log_path = tmp_path / "purge.log"
        drafts_dir = tmp_path / "drafts"

        args = _make_args(all=True, confirm=True)
        run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert "purged all reason=user-request" in lines[0]

    def test_audit_lines_accumulate(self, tmp_path: Path) -> None:
        """Multiple purges append separate lines to the log."""
        _, contact_a, _, _ = _seed_contact("acme")
        _, contact_b, _, _ = _seed_contact("globex")
        log_path = tmp_path / "purge.log"

        run_purge(_make_args(contact=contact_a), _log_path=log_path)
        run_purge(_make_args(contact=contact_b), _log_path=log_path)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# Test 4: Purge company removes drafts dir
# ---------------------------------------------------------------------------

class TestPurgeCompany:
    def test_company_data_deleted(self, tmp_path: Path) -> None:
        """Purging a company deletes its contacts, drafts, outreach_log, and company row."""
        company_id, contact_id, _, _ = _seed_contact("spacex")
        log_path = tmp_path / "purge.log"
        drafts_dir = tmp_path / "drafts"

        args = _make_args(company="spacex")
        rc = run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        assert rc == 0
        assert _count("companies", "id", company_id) == 0
        assert _count("contacts", "company_id", company_id) == 0
        assert _count("drafts", "contact_id", contact_id) == 0
        assert _count("outreach_log", "contact_id", contact_id) == 0

    def test_drafts_dir_removed(self, tmp_path: Path) -> None:
        """Purging a company removes the drafts/<slug>/ directory."""
        _seed_contact("lockheed")
        drafts_dir = tmp_path / "drafts"
        slug_dir = drafts_dir / "lockheed"
        slug_dir.mkdir(parents=True)
        (slug_dir / "draft_v1.txt").write_text("Dear Alice,\n")

        log_path = tmp_path / "purge.log"
        args = _make_args(company="lockheed")
        run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        assert not slug_dir.exists()

    def test_missing_drafts_dir_is_ok(self, tmp_path: Path) -> None:
        """Purging a company with no drafts dir must not raise an error."""
        _seed_contact("boeing")
        drafts_dir = tmp_path / "drafts"  # does not exist yet
        log_path = tmp_path / "purge.log"

        args = _make_args(company="boeing")
        rc = run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        assert rc == 0

    def test_unknown_company_slug_is_noop(self, tmp_path: Path) -> None:
        """Purging a non-existent company slug is a silent no-op (returns 0)."""
        log_path = tmp_path / "purge.log"
        drafts_dir = tmp_path / "drafts"
        args = _make_args(company="nonexistent-co")
        rc = run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)
        assert rc == 0


# ---------------------------------------------------------------------------
# Test 5: No flag refuses with message
# ---------------------------------------------------------------------------

class TestNoFlag:
    def test_no_flag_returns_1(self, tmp_path: Path) -> None:
        """Calling run_purge with no target flag must return 1."""
        log_path = tmp_path / "purge.log"
        args = _make_args()  # contact=None, company=None, all=False
        rc = run_purge(args, _log_path=log_path)
        assert rc == 1

    def test_no_flag_prints_error(self, tmp_path: Path, capsys) -> None:
        """No target flag must print a descriptive error message."""
        log_path = tmp_path / "purge.log"
        args = _make_args()
        run_purge(args, _log_path=log_path)

        captured = capsys.readouterr()
        assert "specify a target" in captured.out

    def test_no_flag_no_db_changes(self, tmp_path: Path) -> None:
        """No target flag must leave the database unchanged."""
        _, contact_id, _, _ = _seed_contact()
        log_path = tmp_path / "purge.log"
        args = _make_args()
        run_purge(args, _log_path=log_path)

        assert _count("contacts", "id", contact_id) == 1


# ---------------------------------------------------------------------------
# Test 6: --all --confirm purges everything
# ---------------------------------------------------------------------------

class TestPurgeAll:
    def test_all_confirm_clears_db(self, tmp_path: Path) -> None:
        """--all --confirm must delete every row from all tables."""
        _seed_contact("alpha")
        _seed_contact("bravo")
        log_path = tmp_path / "purge.log"
        drafts_dir = tmp_path / "drafts"

        args = _make_args(all=True, confirm=True)
        rc = run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        assert rc == 0
        conn = get_connection()
        try:
            assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0] == 0
        finally:
            conn.close()

    def test_all_confirm_removes_drafts_dir(self, tmp_path: Path) -> None:
        """--all --confirm must remove the entire drafts directory."""
        _seed_contact()
        drafts_dir = tmp_path / "drafts"
        (drafts_dir / "some-company").mkdir(parents=True)
        (drafts_dir / "some-company" / "draft.txt").write_text("content")

        log_path = tmp_path / "purge.log"
        args = _make_args(all=True, confirm=True)
        run_purge(args, _log_path=log_path, _drafts_dir=drafts_dir)

        assert not drafts_dir.exists()
