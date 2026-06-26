"""
tests/test_network_providers.py
Unit tests for src/cli/network_providers.py (Step 8.4).

All tests are hermetic: the DB path is redirected to a tmp file via
monkeypatch so the real ~/.networking-agent/state.db is never touched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from src.core.db import init_db
from src.providers.quota_manager import QuotaManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(add: str | None = None) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for run_providers."""
    ns = argparse.Namespace()
    ns.add = add
    return ns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_db(tmp_path: Path, monkeypatch) -> Path:
    """Redirect DB path to a fresh tmp DB and return its Path."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    return db_path


@pytest.fixture()
def qm(isolated_db: Path) -> QuotaManager:
    """QuotaManager wired to the isolated tmp DB."""
    return QuotaManager(db_path=str(isolated_db))


# ---------------------------------------------------------------------------
# Test 1 — No flags: both providers appear in output
# ---------------------------------------------------------------------------


def test_list_shows_both_providers(isolated_db, qm, capsys) -> None:
    """Without flags, output must contain 'serper' and 'hunter'."""
    from src.cli.network_providers import run_providers  # noqa: PLC0415

    rc = run_providers(_make_args(), _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert "serper" in captured.out
    assert "hunter" in captured.out


# ---------------------------------------------------------------------------
# Test 2 — No flags: quota lines present for both providers
# ---------------------------------------------------------------------------


def test_list_shows_quota_remaining(isolated_db, qm, capsys) -> None:
    """Without flags, output must contain 'Quota remaining' for both providers."""
    from src.cli.network_providers import run_providers  # noqa: PLC0415

    rc = run_providers(_make_args(), _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    # "Quota remaining" should appear at least twice (once per provider).
    assert captured.out.count("Quota remaining") >= 2


# ---------------------------------------------------------------------------
# Test 3 — --add flag prints v0.1.1 notice and returns 0
# ---------------------------------------------------------------------------


def test_add_flag_prints_stub_message(isolated_db, qm, capsys) -> None:
    """--add <name> must print the v0.1.1 notice and return 0."""
    from src.cli.network_providers import run_providers  # noqa: PLC0415

    rc = run_providers(_make_args(add="foo"), _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert "v0.1.1" in captured.out


# ---------------------------------------------------------------------------
# Test 4 — --add flag makes no DB changes or API calls
# ---------------------------------------------------------------------------


def test_add_flag_no_db_writes(isolated_db, qm, capsys, monkeypatch) -> None:
    """--add must not write to the DB and must not call any provider API."""
    from src.cli.network_providers import run_providers  # noqa: PLC0415

    # Record any DB writes by patching with_writer.
    write_calls: list[str] = []

    import src.core.db as _db_mod  # noqa: PLC0415

    original_with_writer = _db_mod.with_writer

    from contextlib import contextmanager  # noqa: PLC0415

    @contextmanager
    def _spy_writer():
        write_calls.append("write_called")
        with original_with_writer() as conn:
            yield conn

    monkeypatch.setattr("src.core.db.with_writer", _spy_writer)

    # Also ensure no network calls happen by patching httpx at the top level.
    import unittest.mock as mock  # noqa: PLC0415

    with mock.patch("httpx.Client") as mock_client:
        rc = run_providers(_make_args(add="foo"), _quota_manager=qm)

    assert rc == 0
    assert write_calls == [], "--add must not trigger any DB writes"
    mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5 — Non-zero limit skips default override (branch 69->75 False branch)
# ---------------------------------------------------------------------------


def test_list_with_nonzero_limit_skips_default(isolated_db, capsys) -> None:
    """When limit > 0 in DB, fallback to _DEFAULT_LIMITS is skipped (branch 69->75)."""
    from src.cli.network_providers import run_providers

    # Seed quota rows with non-zero limits so get_limit() returns > 0
    from src.core.db import with_writer

    with with_writer() as conn:
        from datetime import datetime

        month_key = datetime.now().strftime("%Y-%m")
        conn.execute(
            "INSERT OR REPLACE INTO quota (provider, month_key, used, limit_val) "
            "VALUES ('serper', ?, 10, 100)",
            (month_key,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO quota (provider, month_key, used, limit_val) "
            "VALUES ('hunter', ?, 5, 25)",
            (month_key,),
        )

    qm = QuotaManager(db_path=str(isolated_db))
    rc = run_providers(_make_args(), _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    # Remaining = limit - used: serper 90/100, hunter 20/25
    assert "90 / 100" in captured.out
    assert "20 / 25" in captured.out
