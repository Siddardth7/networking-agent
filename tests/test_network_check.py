"""
tests/test_network_check.py — Tests for src/cli/network_check.py
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

import src.core.db as db_module
from src.core.db import init_db
from src.core.migrations import run_migrations
from src.cli import network_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_handler(anthropic=200, serper=200, hunter=200):
    def handler(request):
        url = str(request.url)
        if "anthropic.com" in url:
            return httpx.Response(anthropic, json={"id": "msg"}, request=request)
        if "serper.dev" in url:
            return httpx.Response(serper, json={"organic": []}, request=request)
        if "hunter.io" in url:
            return httpx.Response(hunter, json={"data": {}}, request=request)
        return httpx.Response(404, request=request)
    return handler


def make_client(anthropic=200, serper=200, hunter=200):
    transport = httpx.MockTransport(make_mock_handler(anthropic, serper, hunter))
    return httpx.Client(transport=transport)


def setup_tmp_db(tmp_path: Path, monkeypatch) -> Path:
    """Create a fresh DB at tmp_path/state.db and monkeypatch paths."""
    db_path = tmp_path / "state.db"

    # Patch the module-level _DB_PATH in db module and network_check module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    monkeypatch.setattr(network_check, "_DB_PATH" if hasattr(network_check, "_DB_PATH") else "_DB_PATH",
                        db_path, raising=False)

    # Also patch the _db_path function result by patching the module attribute
    # init_db uses _db_path() which reads the module variable
    init_db()
    return db_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_http_client():
    """Ensure _http_client is reset after each test."""
    yield
    network_check.set_http_client(None)


@pytest.fixture
def env_keys(monkeypatch):
    """Set all three API keys in environment so config.yaml check is skipped."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    monkeypatch.setenv("HUNTER_API_KEY", "test-hunter-key")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Set up a temporary database and patch all relevant paths."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)

    # Patch _db_path function to use tmp path
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)

    # init_db reads _DB_PATH via _db_path()
    init_db()
    return db_path


@pytest.fixture
def no_voice_doc(tmp_path, monkeypatch):
    """Patch Path.home() to point to tmp_path so voice.md doesn't exist."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAllGreen:
    def test_all_green(self, tmp_path, monkeypatch, env_keys, capsys):
        """All 200s, keys in env, tmp DB → return 0, 'All checks passed'."""
        # Patch DB path
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        # Create a voice doc
        agent_dir = tmp_path / ".networking-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        voice_doc = agent_dir / "voice.md"
        voice_doc.write_text("My voice document content here.\n")

        # Patch home() to return tmp_path so voice doc path resolves correctly
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        client = make_client(anthropic=200, serper=200, hunter=200)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 0
        assert "All checks passed" in captured.out


class TestBadAnthropic:
    def test_bad_anthropic(self, tmp_path, monkeypatch, env_keys, capsys):
        """Anthropic returns 401 → return 1, ✗ in output, 'Anthropic' in output."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        client = make_client(anthropic=401, serper=200, hunter=200)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 1
        assert "✗" in captured.out
        assert "Anthropic" in captured.out


class TestBadSerper:
    def test_bad_serper(self, tmp_path, monkeypatch, env_keys, capsys):
        """Serper returns 401 → return 1."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        client = make_client(anthropic=200, serper=401, hunter=200)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 1
        assert "✗" in captured.out


class TestBadHunter:
    def test_bad_hunter(self, tmp_path, monkeypatch, env_keys, capsys):
        """Hunter returns 401 → return 1, ✗ in output, 'Hunter' in output."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        client = make_client(anthropic=200, serper=200, hunter=401)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 1
        assert "✗" in captured.out
        assert "Hunter" in captured.out


class TestHunterLowQuota:
    def test_hunter_low_quota(self, tmp_path, monkeypatch, env_keys, capsys):
        """Hunter 200, remaining=3 → ⚠ and 'nearly exhausted', return 0."""
        from src.providers.quota_manager import QuotaManager

        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        # Monkeypatch QuotaManager.remaining to return 3 for "hunter"
        original_remaining = QuotaManager.remaining

        def mock_remaining(self, provider):
            if provider == "hunter":
                return 3
            return original_remaining(self, provider)

        monkeypatch.setattr(QuotaManager, "remaining", mock_remaining)

        # Also patch get_limit for hunter
        original_get_limit = QuotaManager.get_limit

        def mock_get_limit(self, provider):
            if provider == "hunter":
                return 25
            return original_get_limit(self, provider)

        monkeypatch.setattr(QuotaManager, "get_limit", mock_get_limit)

        client = make_client(anthropic=200, serper=200, hunter=200)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 0
        assert "⚠" in captured.out
        assert "nearly exhausted" in captured.out


class TestMissingVoiceDoc:
    def test_missing_voice_doc(self, tmp_path, monkeypatch, env_keys, capsys):
        """Voice doc path doesn't exist → ⚠, 'Voice doc not found', return 0."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        # Point home to tmp_path where no voice.md exists
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        client = make_client(anthropic=200, serper=200, hunter=200)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 0
        assert "⚠" in captured.out
        assert "Voice doc not found" in captured.out


class TestSqliteVersionFail:
    def test_sqlite_version_fail(self, tmp_path, monkeypatch, env_keys, capsys):
        """sqlite_version_info = (3,38,0) → ✗ in output, 'SQLite 3.39+' in output, return 1."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        # Patch sqlite3.sqlite_version_info to old version
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 38, 0))
        monkeypatch.setattr(sqlite3, "sqlite_version", "3.38.0")

        client = make_client(anthropic=200, serper=200, hunter=200)
        network_check.set_http_client(client)

        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 1
        assert "✗" in captured.out
        assert "SQLite 3.39+" in captured.out
