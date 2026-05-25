"""Tests for src.core.db — WAL mode, concurrent access, retry logic, with_writer serialization."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.core.db as db_module
from src.core.db import WRITE_LOCK, get_connection, with_writer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_db_path(tmp_path: Path) -> Path:
    """Return a temp DB path scoped to the current test."""
    return tmp_path / "test_state.db"


# ---------------------------------------------------------------------------
# 1. WAL mode
# ---------------------------------------------------------------------------


def test_wal_mode_is_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRAGMAs must set journal_mode to WAL on every new connection."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    conn = get_connection()
    try:
        row = conn.execute("PRAGMA journal_mode;").fetchone()
        assert row[0].lower() == "wal", f"Expected WAL, got {row[0]!r}"
    finally:
        conn.close()


def test_foreign_keys_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRAGMA foreign_keys must be ON."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    conn = get_connection()
    try:
        row = conn.execute("PRAGMA foreign_keys;").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Concurrent read / write
# ---------------------------------------------------------------------------


def test_concurrent_readers_and_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4 reader threads + 1 writer thread must not produce 'database is locked' errors."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    # Bootstrap schema
    with with_writer() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT);"
        )

    errors: list[Exception] = []
    insert_count = 20
    inserts_done = threading.Event()

    def writer() -> None:
        try:
            for i in range(insert_count):
                with with_writer() as conn:
                    conn.execute("INSERT INTO items (val) VALUES (?);", (f"item-{i}",))
                time.sleep(0.05)  # ~1 second total for all inserts
        except Exception as exc:
            errors.append(exc)
        finally:
            inserts_done.set()

    def reader(reader_id: int) -> None:
        try:
            for _ in range(5):
                conn = get_connection()
                try:
                    conn.execute("SELECT COUNT(*) FROM items;").fetchone()
                finally:
                    conn.close()
                time.sleep(0.06)
        except Exception as exc:
            errors.append(exc)

    threads: list[threading.Thread] = []
    threads.append(threading.Thread(target=writer, daemon=True))
    for i in range(4):
        threads.append(threading.Thread(target=reader, args=(i,), daemon=True))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Assert no exceptions during concurrent access
    lock_errors = [e for e in errors if "locked" in str(e).lower()]
    assert not lock_errors, f"Got lock errors during concurrent access: {lock_errors}"
    assert not [e for e in errors if "locked" not in str(e).lower()], (
        f"Other errors: {errors}"
    )

    # Verify all inserts completed
    inserts_done.wait(timeout=5)
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM items;").fetchone()
        assert row[0] == insert_count, f"Expected {insert_count} rows, got {row[0]}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Retry on "database is locked"
# ---------------------------------------------------------------------------


def test_get_connection_retries_on_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_connection() must retry when sqlite3.connect raises OperationalError('database is locked')."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    locked_error = sqlite3.OperationalError("database is locked")
    real_connect = sqlite3.connect
    call_count = 0

    def mock_connect(path: str, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise locked_error
        return real_connect(path, **kwargs)

    # Patch sleep so the test doesn't actually wait
    with (
        patch.object(db_module, "sqlite3") as mock_sqlite,
        patch("src.core.db.time.sleep") as mock_sleep,
    ):
        mock_sqlite.connect.side_effect = mock_connect
        mock_sqlite.OperationalError = sqlite3.OperationalError
        mock_sqlite.Row = sqlite3.Row

        conn = db_module.get_connection()
        assert conn is not None
        assert call_count == 3, f"Expected 3 connect calls, got {call_count}"
        assert mock_sleep.call_count == 2, (
            f"Expected 2 sleeps for 2 failures, got {mock_sleep.call_count}"
        )
        # Verify correct backoff delays were used
        sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_calls == [0.1, 0.5], f"Unexpected sleep delays: {sleep_calls}"


def test_get_connection_propagates_after_max_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After 4 failed attempts, get_connection() must re-raise the OperationalError."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    locked_error = sqlite3.OperationalError("database is locked")

    with (
        patch.object(db_module, "sqlite3") as mock_sqlite,
        patch("src.core.db.time.sleep"),
    ):
        mock_sqlite.connect.side_effect = locked_error
        mock_sqlite.OperationalError = sqlite3.OperationalError
        mock_sqlite.Row = sqlite3.Row

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            db_module.get_connection()

        assert mock_sqlite.connect.call_count == 4


def test_get_connection_does_not_retry_non_lock_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-lock OperationalErrors must propagate immediately without retry."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    other_error = sqlite3.OperationalError("no such table: foo")

    with (
        patch.object(db_module, "sqlite3") as mock_sqlite,
        patch("src.core.db.time.sleep") as mock_sleep,
    ):
        mock_sqlite.connect.side_effect = other_error
        mock_sqlite.OperationalError = sqlite3.OperationalError

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db_module.get_connection()

        assert mock_sqlite.connect.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# 4. with_writer serialization
# ---------------------------------------------------------------------------


def test_with_writer_serializes_concurrent_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two threads calling with_writer() concurrently must be serialized — only one holds the lock at a time."""
    monkeypatch.setattr(db_module, "_DB_PATH", _patch_db_path(tmp_path))

    # Bootstrap schema
    with with_writer() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS counter (val INTEGER);"
        )
        conn.execute("INSERT INTO counter VALUES (0);")

    overlap_detected = threading.Event()
    inside_lock = threading.Event()
    errors: list[str] = []

    barrier = threading.Barrier(2)

    def writer_a() -> None:
        barrier.wait()
        with with_writer() as conn:
            # Signal that writer_a holds the lock
            inside_lock.set()
            # Give writer_b time to try to acquire
            time.sleep(0.15)
            conn.execute("UPDATE counter SET val = val + 1;")
        inside_lock.clear()

    def writer_b() -> None:
        barrier.wait()
        # Wait a tiny bit so writer_a is likely first
        time.sleep(0.01)
        # If inside_lock is set here, we'd be overlapping — but WRITE_LOCK prevents that
        if inside_lock.is_set() and WRITE_LOCK.locked():
            # This is expected: writer_a holds lock, writer_b must block
            pass
        with with_writer() as conn:
            # If we got here while inside_lock is still set, that's a problem
            if inside_lock.is_set():
                errors.append("OVERLAP: two writers held lock simultaneously")
            conn.execute("UPDATE counter SET val = val + 1;")

    t_a = threading.Thread(target=writer_a, daemon=True)
    t_b = threading.Thread(target=writer_b, daemon=True)

    t_a.start()
    t_b.start()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert not errors, f"Serialization failure: {errors}"

    # Both increments must have been applied
    conn = get_connection()
    try:
        row = conn.execute("SELECT val FROM counter;").fetchone()
        assert row[0] == 2, f"Expected counter=2 (both writes), got {row[0]}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. init_db smoke test (migrations module absent is OK)
# ---------------------------------------------------------------------------


def test_init_db_creates_directory_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init_db() must create the parent directory and the database file."""
    db_path = tmp_path / "nested" / "dir" / "state.db"
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)

    db_module.init_db()

    assert db_path.parent.exists(), "Parent directory was not created"
    assert db_path.exists(), "Database file was not created"
