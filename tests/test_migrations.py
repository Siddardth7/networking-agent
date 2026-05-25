"""Tests for src.core.migrations — schema creation, indexes, versioning, and idempotency."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.migrations import run_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_conn(db_path: Path) -> sqlite3.Connection:
    """Open a fresh sqlite3 connection with row_factory set."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ---------------------------------------------------------------------------
# 1. All 6 tables are created
# ---------------------------------------------------------------------------


def test_all_tables_created(tmp_path: Path) -> None:
    """Running migrations on a fresh DB must create all 6 expected tables."""
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {"companies", "contacts", "drafts", "outreach_log", "quota", "followups"}
        assert expected.issubset(tables), (
            f"Missing tables: {expected - tables}.  Found: {tables}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. All 4 indexes are created
# ---------------------------------------------------------------------------


def test_all_indexes_created(tmp_path: Path) -> None:
    """Running migrations must create all 4 declared indexes."""
    conn = _open_conn(tmp_path / "test.db")
    try:
        run_migrations(conn)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        expected_indexes = {
            "idx_companies_slug",
            "idx_contacts_company",
            "idx_drafts_contact",
        }
        assert expected_indexes.issubset(indexes), (
            f"Missing indexes: {expected_indexes - indexes}.  Found: {indexes}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. user_version = 1 after migration
# ---------------------------------------------------------------------------


def test_user_version_after_migration(tmp_path: Path) -> None:
    """user_version must equal 1 after the initial migration is applied."""
    conn = _open_conn(tmp_path / "test.db")
    try:
        result = run_migrations(conn)
        user_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == 1, f"Expected user_version=1, got {user_version}"
        assert result == 1, f"run_migrations() should return 1, got {result}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Idempotent: second run is a no-op
# ---------------------------------------------------------------------------


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """Running run_migrations() twice must not raise and must leave user_version=1."""
    db_path = tmp_path / "test.db"
    conn = _open_conn(db_path)
    try:
        # First run — applies migration
        first_result = run_migrations(conn)
        assert first_result == 1

        # Capture table count after first run
        table_count_after_first: int = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]

        # Second run — must be a no-op
        second_result = run_migrations(conn)
        assert second_result == 1, (
            f"Second run should return 1, got {second_result}"
        )

        table_count_after_second: int = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]

        assert table_count_after_first == table_count_after_second, (
            "Table count changed on second migration run — not idempotent"
        )

        user_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == 1, f"user_version should still be 1, got {user_version}"
    finally:
        conn.close()
