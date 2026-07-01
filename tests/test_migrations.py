"""Tests for src/core/migrations.py — version-based migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.migrations import run_migrations


def _open_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# Latest applied migration number. Update when adding a new
# src/core/migrations/NNN_*.sql file.
LATEST_VERSION = 9  # 009_applications (#58)


# ---------------------------------------------------------------------------
# 1. Tables created
# ---------------------------------------------------------------------------


def test_migration_creates_expected_tables(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        names = {r["name"] for r in rows}
        expected = {
            "companies",
            "contacts",
            "drafts",
            "outreach_log",
            "quota",
            "followups",
            "applications",
            "application_contacts",
        }
        assert expected.issubset(names), f"Missing tables: {expected - names}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Indexes created
# ---------------------------------------------------------------------------


def test_migration_creates_expected_indexes(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        indexes = {r["name"] for r in rows}
        expected_indexes = {
            "idx_companies_slug",
            "idx_contacts_company",
            "idx_drafts_contact",
            "idx_contacts_company_linkedin",
            "idx_applications_company",
            "idx_appcontacts_contact",
        }
        assert expected_indexes.issubset(indexes), (
            f"Missing indexes: {expected_indexes - indexes}.  Found: {indexes}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. user_version after all migrations
# ---------------------------------------------------------------------------


def test_user_version_after_migration(tmp_path: Path) -> None:
    """user_version must equal the latest migration number after run."""
    conn = _open_conn(tmp_path / "test.db")
    try:
        result = run_migrations(conn)
        user_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == LATEST_VERSION
        assert result == LATEST_VERSION
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Idempotent: second run is a no-op
# ---------------------------------------------------------------------------


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """Running run_migrations() twice must not raise and must leave user_version stable."""
    db_path = tmp_path / "test.db"
    conn = _open_conn(db_path)
    try:
        first_result = run_migrations(conn)
        assert first_result == LATEST_VERSION

        table_count_after_first: int = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]

        second_result = run_migrations(conn)
        assert second_result == LATEST_VERSION

        table_count_after_second: int = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]

        assert table_count_after_first == table_count_after_second, (
            "Table count changed on second migration run — not idempotent"
        )

        user_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == LATEST_VERSION
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Migration 002 adds quality_code column to drafts
# ---------------------------------------------------------------------------


def test_migration_002_adds_quality_code_column(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        cols = conn.execute("PRAGMA table_info(drafts)").fetchall()
        col_names = {c["name"] for c in cols}
        assert "quality_code" in col_names
        # Default should be 'OK'
        conn.execute("INSERT INTO companies (slug, name) VALUES ('x', 'X')")
        conn.execute("INSERT INTO contacts (company_id, full_name) VALUES (1, 'A')")
        conn.execute(
            "INSERT INTO drafts (contact_id, channel, body) VALUES (1, 'LINKEDIN_CONNECTION', 'hi')"
        )
        row = conn.execute("SELECT quality_code FROM drafts").fetchone()
        assert row["quality_code"] == "OK"
    finally:
        conn.close()


def test_migration_006_adds_rank_columns(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(contacts)").fetchall()}
        assert {"rank_score", "rank_reasons"} <= cols
        # rank_score defaults to 0 so pre-#11 rows sort last, stable by id.
        conn.execute("INSERT INTO companies (slug, name) VALUES ('x', 'X')")
        conn.execute("INSERT INTO contacts (company_id, full_name) VALUES (1, 'A')")
        row = conn.execute("SELECT rank_score, rank_reasons FROM contacts").fetchone()
        assert row["rank_score"] == 0
        assert row["rank_reasons"] is None
    finally:
        conn.close()


def test_migration_007_adds_outcome_columns(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(contacts)").fetchall()}
        assert {"outcome", "outcome_notes", "outcome_at"} <= cols
        # outcome defaults to 'NONE' so pre-#15 rows read as "nothing recorded".
        conn.execute("INSERT INTO companies (slug, name) VALUES ('x', 'X')")
        conn.execute("INSERT INTO contacts (company_id, full_name) VALUES (1, 'A')")
        row = conn.execute("SELECT outcome, outcome_notes, outcome_at FROM contacts").fetchone()
        assert row["outcome"] == "NONE"
        assert row["outcome_notes"] is None
        assert row["outcome_at"] is None
    finally:
        conn.close()


def test_migration_008_adds_location_column(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(contacts)").fetchall()}
        assert "location" in cols
        # location defaults to NULL → recommender falls back to UTC (#18).
        conn.execute("INSERT INTO companies (slug, name) VALUES ('x', 'X')")
        conn.execute("INSERT INTO contacts (company_id, full_name) VALUES (1, 'A')")
        row = conn.execute("SELECT location FROM contacts").fetchone()
        assert row["location"] is None
    finally:
        conn.close()


def test_migration_009_adds_application_tables(tmp_path: Path) -> None:
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        # applications: posting entity, job_id PK; status defaults to 'NEW'.
        app_cols = {c["name"] for c in conn.execute("PRAGMA table_info(applications)").fetchall()}
        assert {
            "job_id", "company", "company_slug", "role_title", "function",
            "job_url", "score", "deadline", "status", "created_at",
        } <= app_cols
        conn.execute(
            "INSERT INTO applications (job_id, company, role_title) "
            "VALUES ('ja-1', 'Joby Aviation', 'Quality Engineer')"
        )
        row = conn.execute("SELECT status FROM applications WHERE job_id = 'ja-1'").fetchone()
        assert row["status"] == "NEW"

        # application_contacts: many-to-many join, (job_id, contact_id) PK →
        # re-linking is idempotent (INSERT OR IGNORE succeeds on the dupe).
        link_cols = {
            c["name"] for c in conn.execute("PRAGMA table_info(application_contacts)").fetchall()
        }
        assert {"job_id", "contact_id", "created_at"} <= link_cols
        conn.execute("INSERT INTO companies (slug, name) VALUES ('joby-aviation', 'Joby')")
        conn.execute("INSERT INTO contacts (company_id, full_name) VALUES (1, 'Jane')")
        conn.execute("INSERT INTO application_contacts (job_id, contact_id) VALUES ('ja-1', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO application_contacts (job_id, contact_id) VALUES ('ja-1', 1)"
        )
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM application_contacts WHERE job_id = 'ja-1'"
        ).fetchone()["n"]
        assert count == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Missing migrations dir → no-op returns current version (line 68)
# ---------------------------------------------------------------------------


def test_missing_migrations_dir_is_noop(tmp_path: Path, monkeypatch) -> None:
    """If _MIGRATIONS_DIR does not exist, run_migrations returns current version unchanged."""
    import src.core.migrations as mig_module

    conn = _open_conn(tmp_path / "test.db")
    try:
        # Point _MIGRATIONS_DIR at a non-existent directory
        nonexistent = tmp_path / "no_migrations_here"
        monkeypatch.setattr(mig_module, "_MIGRATIONS_DIR", nonexistent)

        # Pre-set user_version to something specific
        conn.execute("PRAGMA user_version = 7")
        conn.commit()

        result = run_migrations(conn)
        assert result == 7  # unchanged, returned as-is
    finally:
        conn.close()
