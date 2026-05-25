"""Single-writer-multiple-reader contract: all writes must hold WRITE_LOCK. Readers use get_connection() directly without the lock."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

# ---------------------------------------------------------------------------
# Module-level path — override in tests via monkeypatch
# ---------------------------------------------------------------------------
_DB_PATH: Path = Path.home() / ".networking-agent" / "state.db"

# ---------------------------------------------------------------------------
# Single-writer lock: every write path must acquire this before touching the DB
# ---------------------------------------------------------------------------
WRITE_LOCK: threading.Lock = threading.Lock()

# ---------------------------------------------------------------------------
# Retry configuration for "database is locked" OperationalErrors
# ---------------------------------------------------------------------------
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.5, 1.5)  # waits between attempts 1-2, 2-3, 3-4


def _db_path() -> Path:
    """Return the current DB path (module-level variable, patchable in tests)."""
    return _DB_PATH


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply WAL-mode and safety PRAGMAs in the required order."""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")


def get_connection() -> sqlite3.Connection:
    """Open and return a sqlite3.Connection to the agent state database.

    Applies WAL-mode PRAGMAs on every new connection.  Retries up to 4
    attempts when the database signals a "locked" error; on the 4th failure
    the exception propagates to the caller.

    Callers that only read data use this directly without acquiring WRITE_LOCK.
    """
    path = _db_path()
    os.makedirs(path.parent, exist_ok=True)

    last_error: sqlite3.OperationalError | None = None
    for attempt in range(4):
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            _apply_pragmas(conn)
            return conn
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_error = exc
            if attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])

    # All attempts exhausted — re-raise the last locked error
    raise last_error  # type: ignore[misc]


@contextmanager
def with_writer() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for safe single-writer access to the database.

    Acquires WRITE_LOCK, opens a connection, yields it, then commits and
    closes.  On any exception the connection is rolled back before closing.

    Usage::

        with with_writer() as conn:
            conn.execute("INSERT INTO contacts ...")
    """
    with WRITE_LOCK:
        conn = get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    """Initialise the database: create the directory and run schema migrations.

    The migration runner (``src.core.migrations.run_migrations``) is imported
    lazily so that this module can be tested before Step 2.2 is implemented.
    """
    path = _db_path()
    os.makedirs(path.parent, exist_ok=True)

    with with_writer() as conn:
        try:
            from src.core.migrations import run_migrations  # noqa: PLC0415

            run_migrations(conn)
        except ImportError:
            # migrations module not yet available (pre-Step-2.2); skip silently
            pass


__all__ = ["init_db", "get_connection", "WRITE_LOCK", "with_writer"]
