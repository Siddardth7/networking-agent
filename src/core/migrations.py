"""Migration runner for the networking-agent SQLite database.

SQL migration files live in src/core/migrations/ and are named:
    <version>_<description>.sql   e.g. 001_initial_schema.sql

The runner reads PRAGMA user_version, applies any SQL files whose leading
numeric version is greater than the current version (sorted ascending), and
updates user_version after each file is applied.

Called by src.core.db.init_db() via:
    from src.core.migrations import run_migrations
    run_migrations(conn)
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# SQL files live in the migrations/ subdirectory next to this module file.
# __file__ is .../src/core/migrations.py  →  .parent is .../src/core/
_MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"

_VERSION_RE = re.compile(r"^(\d+)_")


def _parse_version(filename: str) -> int | None:
    """Return the leading integer version from a SQL filename, or None."""
    m = _VERSION_RE.match(filename)
    return int(m.group(1)) if m else None


def run_migrations(conn: sqlite3.Connection) -> int:
    """Apply any pending SQL migration files to *conn* and return the final user_version.

    Algorithm
    ---------
    1. Read ``PRAGMA user_version`` from *conn*.
    2. Collect ``*.sql`` files from the migrations directory whose numeric
       prefix is greater than the current version.
    3. Sort by version number (ascending).
    4. For each pending file: execute its contents via ``executescript()``,
       then set ``PRAGMA user_version = <new_version>`` and commit.
    5. Return the final ``user_version``.

    Calling this function on a database that is already at the latest version
    is a safe no-op — it returns the current version without executing any SQL.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.  The caller (``with_writer``) is
        responsible for the surrounding transaction and commit; this function
        issues its own per-file commits so that user_version is advanced
        atomically with each migration.

    Returns
    -------
    int
        The ``user_version`` value after all pending migrations have been run.
    """
    # Step 1: read current schema version
    current_version: int = conn.execute("PRAGMA user_version").fetchone()[0]

    # Step 2: discover pending migration files
    if not _MIGRATIONS_DIR.is_dir():
        return current_version

    pending: list[tuple[int, Path]] = []
    for sql_file in _MIGRATIONS_DIR.glob("*.sql"):
        version = _parse_version(sql_file.name)
        if version is not None and version > current_version:
            pending.append((version, sql_file))

    # Step 3: sort ascending by version number
    pending.sort(key=lambda t: t[0])

    # Step 4: apply each file
    for version, sql_file in pending:
        sql_text = sql_file.read_text(encoding="utf-8")
        # executescript() issues an implicit COMMIT first, so we call it
        # directly; it handles multiple statements cleanly.
        conn.executescript(sql_text)
        # Advance user_version atomically with this migration
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()

    # Step 5: return final version
    final_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return final_version


__all__ = ["run_migrations"]
