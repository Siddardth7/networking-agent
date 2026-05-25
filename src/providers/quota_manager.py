"""
src/providers/quota_manager.py
Read/write access to the ``quota`` table in the agent state SQLite database.
Traceability: DESIGN.md §8.12 (Hard-stop quota enforcement)

Thread safety
-------------
All writes are routed through :func:`src.core.db.with_writer`, which holds
``WRITE_LOCK`` for the duration of the write.  Reads use
:func:`src.core.db.get_connection` directly (no lock needed under WAL mode).

Month rollover
--------------
Each call uses the *current* ``month_key()``.  When a calendar month turns,
``_ensure_row`` inserts a fresh row; old rows are kept for auditing.

Free-tier defaults (seeded on first use)
-----------------------------------------
- ``serper``:  100 queries / month
- ``hunter``:   25 queries / month
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.db import _DB_PATH, get_connection, with_writer
from src.providers.retry import QuotaExhausted

__all__ = ["QuotaManager"]

# Free-tier monthly limits used when inserting a brand-new provider row.
_DEFAULT_LIMITS: dict[str, int] = {
    "serper": 100,
    "hunter": 25,
}


class QuotaManager:
    """Manages per-provider monthly quota state in the SQLite ``quota`` table.

    Parameters
    ----------
    db_path:
        Optional override for the database path.  When ``None`` the module-
        level ``_DB_PATH`` from :mod:`src.core.db` is used.  Pass an explicit
        path in tests to keep them hermetic.

    Example
    -------
    >>> qm = QuotaManager()
    >>> qm.can_query("serper")
    True
    >>> qm.increment("serper", n=1)
    >>> qm.remaining("serper")
    99
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path: Path = Path(db_path) if db_path is not None else _DB_PATH

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return a connection to *this instance's* database path."""
        import os

        os.makedirs(self._db_path.parent, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _get_read_conn(self) -> sqlite3.Connection:
        """Return a read connection, respecting the db_path override."""
        # If no override was set, use the shared helper so tests that patch
        # the module-level _DB_PATH still work.
        if self._db_path == _DB_PATH:
            return get_connection()
        return self._connect()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def month_key(self) -> str:
        """Return the current month as a ``'YYYY-MM'`` string.

        Extracted into its own method so tests can monkeypatch it to simulate
        month rollovers without touching the system clock.
        """
        return datetime.now().strftime("%Y-%m")

    def _ensure_row(self, provider: str, default_limit: int) -> None:
        """Insert a quota row for *provider* + current month if absent.

        Uses ``INSERT OR IGNORE`` so it is safe to call before every read or
        write — it is a no-op when the row already exists.

        Parameters
        ----------
        provider:
            Provider name (e.g. ``"serper"``).
        default_limit:
            The ``limit_val`` to use when creating a brand-new row.
            Has no effect if the row already exists.
        """
        mk = self.month_key()
        if self._db_path == _DB_PATH:
            # Use the module-level writer (holds WRITE_LOCK)
            with with_writer() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO quota (provider, month_key, used, limit_val)
                    VALUES (?, ?, 0, ?)
                    """,
                    (provider, mk, default_limit),
                )
        else:
            # Test path: open a direct connection to the overridden path
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO quota (provider, month_key, used, limit_val)
                    VALUES (?, ?, 0, ?)
                    """,
                    (provider, mk, default_limit),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _default_limit(self, provider: str) -> int:
        """Return the free-tier default limit for *provider*, falling back to 0."""
        return _DEFAULT_LIMITS.get(provider, 0)

    def can_query(self, provider: str) -> bool:
        """Return ``True`` if the provider has remaining quota this month.

        Ensures the quota row exists (seeding with the free-tier default if
        necessary) before checking.

        Parameters
        ----------
        provider:
            Provider name.
        """
        self._ensure_row(provider, self._default_limit(provider))
        mk = self.month_key()
        conn = self._get_read_conn()
        try:
            row = conn.execute(
                "SELECT used, limit_val FROM quota WHERE provider = ? AND month_key = ?",
                (provider, mk),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return False
        return int(row["used"]) < int(row["limit_val"])

    def increment(self, provider: str, n: int = 1) -> None:
        """Increment the provider's usage counter by *n*.

        Checks **before** writing whether the increment would exceed
        ``limit_val``.  Raises :class:`~src.providers.retry.QuotaExhausted`
        if so; the counter is left unchanged in that case.

        Parameters
        ----------
        provider:
            Provider name.
        n:
            Number of units to add (default ``1``).

        Raises
        ------
        QuotaExhausted
            When ``used + n > limit_val``.
        """
        self._ensure_row(provider, self._default_limit(provider))
        mk = self.month_key()

        def _do_increment(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT used, limit_val FROM quota WHERE provider = ? AND month_key = ?",
                (provider, mk),
            ).fetchone()
            if row is None:
                # Row was just ensured; if it's gone something is very wrong.
                raise RuntimeError(
                    f"Quota row for provider='{provider}' month='{mk}' disappeared unexpectedly."
                )
            used = int(row["used"])
            limit_val = int(row["limit_val"])
            if used + n > limit_val:
                raise QuotaExhausted(provider, used, limit_val)
            conn.execute(
                "UPDATE quota SET used = used + ? WHERE provider = ? AND month_key = ?",
                (n, provider, mk),
            )

        if self._db_path == _DB_PATH:
            with with_writer() as conn:
                _do_increment(conn)
        else:
            conn = self._connect()
            try:
                _do_increment(conn)
                conn.commit()
            except QuotaExhausted:
                conn.rollback()
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def remaining(self, provider: str) -> int:
        """Return the number of queries remaining for *provider* this month.

        Returns ``0`` if no row exists (rather than a negative number).

        Parameters
        ----------
        provider:
            Provider name.
        """
        mk = self.month_key()
        conn = self._get_read_conn()
        try:
            row = conn.execute(
                "SELECT used, limit_val FROM quota WHERE provider = ? AND month_key = ?",
                (provider, mk),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return 0
        return max(0, int(row["limit_val"]) - int(row["used"]))

    def get_limit(self, provider: str) -> int:
        """Return the configured monthly limit for *provider* this month.

        Returns ``0`` if no row exists yet.

        Parameters
        ----------
        provider:
            Provider name.
        """
        mk = self.month_key()
        conn = self._get_read_conn()
        try:
            row = conn.execute(
                "SELECT limit_val FROM quota WHERE provider = ? AND month_key = ?",
                (provider, mk),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return 0
        return int(row["limit_val"])
