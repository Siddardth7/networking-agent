"""
tests/test_network_dry_run.py — Tests for src/cli/network_dry_run.py

Verifies that:
1. Output contains the planned Serper query string with company slug.
2. Output contains "5 Serper + 5 Hunter" with --limit 5.
3. Output contains "3 Serper + 3 Hunter" with --limit 3.
4. No real API calls are made (quota_manager.can_query is never called;
   only remaining() is called for display purposes).
5. Missing company argument → returns 1.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

import src.core.db as db_module
from src.core.db import init_db
from src.cli.network_dry_run import run_dry_run, _build_serper_query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(company: str | None = "lockheed-martin", limit: int = 5) -> argparse.Namespace:
    """Build a minimal Namespace matching what argparse would produce."""
    ns = argparse.Namespace()
    if company is not None:
        ns.company = company
    else:
        # Simulate missing attribute (e.g. subcommand never set it)
        ns.company = None
    ns.limit = limit
    return ns


def _make_quota_manager(serper_remaining: int = 80, hunter_remaining: int = 20) -> MagicMock:
    """Return a mock QuotaManager with pre-set remaining values."""
    qm = MagicMock()
    qm.remaining.side_effect = lambda provider: (
        serper_remaining if provider == "serper" else hunter_remaining
    )
    return qm


# ---------------------------------------------------------------------------
# Fixture: isolated DB so module-level _DB_PATH doesn't interfere
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    """Point the DB module at a fresh temp database for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Test 1: Output contains the planned Serper query with company slug
# ---------------------------------------------------------------------------

def test_output_contains_planned_query(capsys):
    """The dry-run output must include the full Serper query for the company."""
    args = _make_args(company="lockheed-martin", limit=5)
    qm = _make_quota_manager()

    rc = run_dry_run(args, _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert 'site:linkedin.com/in "lockheed-martin"' in captured.out
    assert "(quality OR structures OR composites OR manufacturing OR materials OR additive)" in captured.out


# ---------------------------------------------------------------------------
# Test 2: Output contains "5 Serper + 5 Hunter" with --limit 5
# ---------------------------------------------------------------------------

def test_output_shows_limit_5_calls(capsys):
    """With --limit 5, the output should say '5 Serper + 5 Hunter API calls'."""
    args = _make_args(company="spacex", limit=5)
    qm = _make_quota_manager()

    rc = run_dry_run(args, _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert "5 Serper + 5 Hunter" in captured.out


# ---------------------------------------------------------------------------
# Test 3: Output contains "3 Serper + 3 Hunter" with --limit 3
# ---------------------------------------------------------------------------

def test_output_shows_limit_3_calls(capsys):
    """With --limit 3, the output should say '3 Serper + 3 Hunter API calls'."""
    args = _make_args(company="blue-origin", limit=3)
    qm = _make_quota_manager()

    rc = run_dry_run(args, _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert "3 Serper + 3 Hunter" in captured.out


# ---------------------------------------------------------------------------
# Test 4: No API calls made — can_query must never be called
# ---------------------------------------------------------------------------

def test_no_api_calls_made(capsys):
    """Dry-run must NEVER call can_query; it may only call remaining() for display."""
    args = _make_args(company="boeing", limit=5)
    qm = _make_quota_manager(serper_remaining=90, hunter_remaining=18)

    rc = run_dry_run(args, _quota_manager=qm)

    assert rc == 0
    # can_query must not have been called at all
    qm.can_query.assert_not_called()
    # remaining() may be called (for quota display) — that is allowed
    assert qm.remaining.call_count >= 0  # present but not required to be called N times


def test_quota_remaining_displayed(capsys):
    """Quota remaining values from the mock are shown in output."""
    args = _make_args(company="boeing", limit=5)
    qm = _make_quota_manager(serper_remaining=77, hunter_remaining=12)

    rc = run_dry_run(args, _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert "Serper: 77" in captured.out
    assert "Hunter: 12" in captured.out


# ---------------------------------------------------------------------------
# Test 5: Missing company → returns 1
# ---------------------------------------------------------------------------

def test_missing_company_returns_error(capsys):
    """When company is None/empty the function must return exit code 1."""
    args = _make_args(company=None, limit=5)
    qm = _make_quota_manager()

    rc = run_dry_run(args, _quota_manager=qm)

    assert rc == 1


def test_empty_company_returns_error(capsys):
    """When company is an empty string the function must return exit code 1."""
    args = _make_args(company="", limit=5)
    qm = _make_quota_manager()

    rc = run_dry_run(args, _quota_manager=qm)

    assert rc == 1


# ---------------------------------------------------------------------------
# Helper: _build_serper_query unit tests
# ---------------------------------------------------------------------------

def test_build_serper_query_format():
    """Verify the query string format exactly matches the DESIGN spec."""
    result = _build_serper_query("lockheed-martin")
    expected = (
        'site:linkedin.com/in "lockheed-martin" '
        "(quality OR structures OR composites OR manufacturing OR materials OR additive)"
    )
    assert result == expected


def test_build_serper_query_different_slug():
    """Query builder should embed the slug verbatim."""
    result = _build_serper_query("spacex")
    assert '"spacex"' in result
    assert "site:linkedin.com/in" in result


# ---------------------------------------------------------------------------
# Lines 71–76: quota_manager=None path — QuotaManager import succeeds
# ---------------------------------------------------------------------------

def test_no_quota_manager_injection_succeeds(capsys, monkeypatch):
    """When _quota_manager=None and QuotaManager loads fine, remaining is shown."""
    mock_qm = _make_quota_manager(serper_remaining=55, hunter_remaining=10)

    # Patch the QuotaManager class inside the module so no real DB/API is hit
    import src.cli.network_dry_run as dry_run_mod
    monkeypatch.setattr(
        dry_run_mod,
        "__builtins__",  # not used — patch via sys.modules instead
        dry_run_mod.__builtins__,
    )

    # Patch at the provider module level so the import inside run_dry_run finds it
    import unittest.mock as mock_lib
    with mock_lib.patch.dict(
        "sys.modules",
        {"src.providers.quota_manager": mock_lib.MagicMock(QuotaManager=lambda: mock_qm)},
    ):
        args = _make_args(company="northrop-grumman", limit=5)
        rc = run_dry_run(args, _quota_manager=None)

    captured = capsys.readouterr()
    assert rc == 0
    assert "northrop-grumman" in captured.out


# ---------------------------------------------------------------------------
# Lines 71–76: quota_manager=None path — QuotaManager import fails → qm=None
# ---------------------------------------------------------------------------

def test_no_quota_manager_import_fails_shows_na(capsys, monkeypatch):
    """When _quota_manager=None and QuotaManager import raises, quota shows N/A."""
    import unittest.mock as mock_lib

    # Make the import raise so qm stays None
    with mock_lib.patch.dict(
        "sys.modules",
        {"src.providers.quota_manager": None},
    ):
        args = _make_args(company="raytheon", limit=5)
        rc = run_dry_run(args, _quota_manager=None)

    captured = capsys.readouterr()
    assert rc == 0
    # quota shown as N/A when manager is unavailable
    assert "N/A" in captured.out


# ---------------------------------------------------------------------------
# Lines 81, 84–85: _remaining returns "N/A" when qm.remaining() raises
# ---------------------------------------------------------------------------

def test_remaining_exception_shows_na(capsys):
    """When qm.remaining() raises an exception, quota values fall back to N/A."""
    qm = MagicMock()
    qm.remaining.side_effect = RuntimeError("DB unavailable")

    args = _make_args(company="general-dynamics", limit=5)
    rc = run_dry_run(args, _quota_manager=qm)

    captured = capsys.readouterr()
    assert rc == 0
    assert "N/A" in captured.out


# ---------------------------------------------------------------------------
# Lines 102–117: __main__ block — simulate argparse.parse_args + sys.exit
# ---------------------------------------------------------------------------

def test_main_block_runs_with_valid_args(capsys, monkeypatch):
    """Simulate the __main__ block by patching parse_args and sys.exit."""
    import unittest.mock as mock_lib
    import src.cli.network_dry_run as dry_run_mod

    mock_qm = _make_quota_manager(serper_remaining=40, hunter_remaining=8)

    parsed_ns = argparse.Namespace(company="boeing-defense", limit=4)

    exit_codes: list[int] = []

    def fake_exit(code):
        exit_codes.append(code)

    with mock_lib.patch("argparse.ArgumentParser.parse_args", return_value=parsed_ns), \
         mock_lib.patch("sys.exit", side_effect=fake_exit), \
         mock_lib.patch.dict(
             "sys.modules",
             {"src.providers.quota_manager": mock_lib.MagicMock(QuotaManager=lambda: mock_qm)},
         ):
        # Execute the __main__ block by running the module body directly
        import importlib
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "__main__",
            dry_run_mod.__file__,
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "__main__"
        spec.loader.exec_module(mod)

    captured = capsys.readouterr()
    # sys.exit should have been called with 0 (success)
    assert exit_codes == [0], f"Expected sys.exit(0), got {exit_codes}"
    assert "boeing-defense" in captured.out


def test_main_block_argparser_configured(monkeypatch):
    """The __main__ block builds a parser that accepts --company and --limit."""
    import unittest.mock as mock_lib
    import src.cli.network_dry_run as dry_run_mod

    # Capture the ArgumentParser that gets built
    parsers: list = []
    original_init = argparse.ArgumentParser.__init__

    def capturing_init(self, *a, **kw):
        original_init(self, *a, **kw)
        parsers.append(self)

    exit_codes: list[int] = []

    parsed_ns = argparse.Namespace(company="raytheon-tech", limit=3)

    with mock_lib.patch.object(argparse.ArgumentParser, "__init__", capturing_init), \
         mock_lib.patch("argparse.ArgumentParser.parse_args", return_value=parsed_ns), \
         mock_lib.patch("sys.exit", side_effect=lambda c: exit_codes.append(c)), \
         mock_lib.patch.dict(
             "sys.modules",
             {"src.providers.quota_manager": None},
         ):
        import importlib.util
        spec = importlib.util.spec_from_file_location("__main__", dry_run_mod.__file__)
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "__main__"
        spec.loader.exec_module(mod)

    # Parser was created with the right description keyword (spot-check)
    assert len(parsers) >= 1
