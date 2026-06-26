"""
tests/test_network_check.py — Tests for src/cli/network_check.py
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

import src.core.config as config_module
import src.core.db as db_module
from src.cli import network_check
from src.core.db import init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_handler(anthropic=200, serper=200, hunter=200, apify=200):
    def handler(request):
        url = str(request.url)
        if "anthropic.com" in url:
            return httpx.Response(anthropic, json={"id": "msg"}, request=request)
        if "serper.dev" in url:
            return httpx.Response(serper, json={"organic": []}, request=request)
        if "hunter.io" in url:
            return httpx.Response(hunter, json={"data": {}}, request=request)
        if "apify.com" in url:
            return httpx.Response(apify, json={"data": {"username": "test"}}, request=request)
        return httpx.Response(404, request=request)

    return handler


def make_client(anthropic=200, serper=200, hunter=200, apify=200):
    transport = httpx.MockTransport(make_mock_handler(anthropic, serper, hunter, apify))
    return httpx.Client(transport=transport)


def setup_tmp_db(tmp_path: Path, monkeypatch) -> Path:
    """Create a fresh DB at tmp_path/state.db and monkeypatch paths."""
    db_path = tmp_path / "state.db"

    # Patch the module-level _DB_PATH in db module and network_check module
    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    monkeypatch.setattr(
        network_check,
        "_DB_PATH" if hasattr(network_check, "_DB_PATH") else "_DB_PATH",
        db_path,
        raising=False,
    )

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


class TestApify:
    def test_apify_valid_when_configured(self, tmp_path, monkeypatch, env_keys, capsys):
        """Apify key present + 200 from users/me → reported valid, still green."""
        # Isolate from the real ~/.networking-agent/config.yaml.
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(tmp_path / "none.yaml"))
        monkeypatch.setenv("APIFY_API_KEY", "apify_test_key")
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        (tmp_path / ".networking-agent").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".networking-agent" / "voice.md").write_text("voice\n")

        network_check.set_http_client(make_client(apify=200))
        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 0
        assert "Apify API key: valid" in captured.out

    def test_apify_invalid_key_is_error(self, tmp_path, monkeypatch, env_keys, capsys):
        """Apify key present but 401 → error (return 1)."""
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(tmp_path / "none.yaml"))
        monkeypatch.setenv("APIFY_API_KEY", "apify_bad")
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        network_check.set_http_client(make_client(apify=401))
        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 1
        assert "Apify API key: invalid" in captured.out

    def test_apify_absent_is_informational(self, tmp_path, monkeypatch, env_keys, capsys):
        """No Apify key → info line, not an error (Serper covers discovery)."""
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(tmp_path / "none.yaml"))
        monkeypatch.delenv("APIFY_API_KEY", raising=False)
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        (tmp_path / ".networking-agent").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".networking-agent" / "voice.md").write_text("voice\n")

        network_check.set_http_client(make_client())
        result = network_check.run_checks()

        captured = capsys.readouterr()
        assert result == 0
        assert "Apify (primary discovery): not configured" in captured.out


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

        import src.core.config as config_module

        real_load = config_module.load_config

        def _enrichment_on():
            cfg = real_load()
            cfg.enable_email_enrichment = True  # v0.2.1: opt back in
            return cfg

        monkeypatch.setattr(config_module, "load_config", _enrichment_on)

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

        import src.core.config as config_module

        real_load = config_module.load_config

        def _enrichment_on():
            cfg = real_load()
            cfg.enable_email_enrichment = True  # v0.2.1: opt back in
            return cfg

        monkeypatch.setattr(config_module, "load_config", _enrichment_on)

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


# ---------------------------------------------------------------------------
# Direct tests for _check_db_integrity branches
# ---------------------------------------------------------------------------


class TestCheckDbIntegrityDirect:
    def test_db_not_exists_creates_it(self, tmp_path, monkeypatch):
        """When DB doesn't exist, init_db() is called (line 77)."""
        db_path = tmp_path / "missing.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        assert not db_path.exists()
        line, is_err = network_check._check_db_integrity()
        assert not is_err
        assert db_path.exists()

    def test_integrity_fail(self, tmp_path, monkeypatch):
        """integrity_check != 'ok' returns error (line 87)."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("corrupt",),  # integrity_check
            ("wal",),  # journal_mode
        ]
        monkeypatch.setattr(db_module, "get_connection", lambda: mock_conn)

        line, is_err = network_check._check_db_integrity()
        assert is_err
        assert "FAILED" in line

    def test_wal_not_active(self, tmp_path, monkeypatch):
        """journal_mode != 'wal' returns error (line 89)."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("ok",),     # integrity_check
            ("delete",), # journal_mode — not WAL
        ]
        monkeypatch.setattr(db_module, "get_connection", lambda: mock_conn)

        line, is_err = network_check._check_db_integrity()
        assert is_err
        assert "WAL mode not active" in line

    def test_exception_returns_error(self, tmp_path, monkeypatch):
        """Exception inside the check returns error (lines 91-92)."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        def boom():
            raise RuntimeError("db exploded")

        monkeypatch.setattr(db_module, "get_connection", boom)

        line, is_err = network_check._check_db_integrity()
        assert is_err
        assert "db exploded" in line


# ---------------------------------------------------------------------------
# Direct tests for _check_schema_version branches
# ---------------------------------------------------------------------------


class TestCheckSchemaVersionDirect:
    def test_version_mismatch(self, tmp_path, monkeypatch):
        """DB user_version != LATEST_MIGRATION returns error (lines 109-111)."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        # Force wrong user_version
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()

        line, is_err = network_check._check_schema_version()
        assert is_err
        assert "mismatch" in line.lower()

    def test_exception_returns_error(self, tmp_path, monkeypatch):
        """Exception returns error (lines 112-113)."""
        db_path = tmp_path / "state.db"
        monkeypatch.setattr(db_module, "_DB_PATH", db_path)
        init_db()

        def boom():
            raise RuntimeError("schema exploded")

        monkeypatch.setattr(db_module, "get_connection", boom)

        line, is_err = network_check._check_schema_version()
        assert is_err
        assert "schema exploded" in line


# ---------------------------------------------------------------------------
# Direct tests for _check_config_permissions branches (bypassed by env_keys)
# ---------------------------------------------------------------------------


class TestCheckConfigPermissionsDirect:
    def test_no_env_keys_file_missing(self, tmp_path, monkeypatch):
        """All three keys absent, config.yaml missing → error (lines 126-131)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.delenv("HUNTER_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        line, is_err = network_check._check_config_permissions()
        assert is_err
        assert "not found" in line

    def test_no_env_keys_wrong_perms(self, tmp_path, monkeypatch):
        """Config file exists but has bad permissions → error (lines 133-138)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.delenv("HUNTER_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        agent_dir = tmp_path / ".networking-agent"
        agent_dir.mkdir()
        cfg_file = agent_dir / "config.yaml"
        cfg_file.write_text("keys: {}")
        os.chmod(cfg_file, 0o644)

        line, is_err = network_check._check_config_permissions()
        assert is_err
        assert "chmod 600" in line

    def test_no_env_keys_correct_perms(self, tmp_path, monkeypatch):
        """Config file exists with mode 0o600 → ok (line 139)."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.delenv("HUNTER_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        agent_dir = tmp_path / ".networking-agent"
        agent_dir.mkdir()
        cfg_file = agent_dir / "config.yaml"
        cfg_file.write_text("keys: {}")
        os.chmod(cfg_file, 0o600)

        line, is_err = network_check._check_config_permissions()
        assert not is_err
        assert "0600" in line

    def test_only_one_env_key_missing_checks_file(self, tmp_path, monkeypatch):
        """Missing one env key means config.yaml is needed (lines 126+)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("SERPER_API_KEY", "y")
        monkeypatch.delenv("HUNTER_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        line, is_err = network_check._check_config_permissions()
        assert is_err
        assert "not found" in line


# ---------------------------------------------------------------------------
# Direct test for _get_http_client when no client is injected
# ---------------------------------------------------------------------------


class TestGetHttpClientDirect:
    def test_no_injected_client_returns_fresh(self, monkeypatch):
        """When _http_client is None, creates a new httpx.Client (lines 146-148)."""
        network_check.set_http_client(None)
        sentinel = MagicMock()
        monkeypatch.setattr("httpx.Client", lambda **kw: sentinel)

        client, should_close = network_check._get_http_client()
        assert client is sentinel
        assert should_close is True


# ---------------------------------------------------------------------------
# Direct tests for _check_anthropic branches
# ---------------------------------------------------------------------------


class TestCheckAnthropicDirect:
    def test_no_key_returns_error(self, monkeypatch):
        """Missing Anthropic key returns error (line 159)."""
        mock_cfg = MagicMock()
        mock_cfg.anthropic_api_key = None
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        line, is_err = network_check._check_anthropic()
        assert is_err
        assert "not configured" in line

    def test_should_close_client(self, monkeypatch):
        """should_close=True triggers client.close() (line 181)."""
        mock_cfg = MagicMock()
        mock_cfg.anthropic_api_key = "test-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp

        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, True))

        line, is_err = network_check._check_anthropic()
        assert not is_err
        mock_client.close.assert_called_once()

    def test_other_http_error(self, monkeypatch):
        """HTTP 500 returns error (lines 191-193)."""
        mock_cfg = MagicMock()
        mock_cfg.anthropic_api_key = "test-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.post.return_value = mock_resp

        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        line, is_err = network_check._check_anthropic()
        assert is_err
        assert "500" in line

    def test_exception_returns_error(self, monkeypatch):
        """Exception returns error (line 193)."""
        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(config_module, "load_config", _boom)

        line, is_err = network_check._check_anthropic()
        assert is_err
        assert "boom" in line


# ---------------------------------------------------------------------------
# Direct tests for _check_serper branches
# ---------------------------------------------------------------------------


class TestCheckSerperDirect:
    def test_no_serper_key_no_apify_key(self, monkeypatch):
        """No serper + no apify key → error (lines 207-209)."""
        mock_cfg = MagicMock()
        mock_cfg.serper_api_key = None
        mock_cfg.apify_api_key = None
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        line, is_err = network_check._check_serper()
        assert is_err
        assert "SERPER_API_KEY" in line or "not configured" in line

    def test_no_serper_key_but_apify_present(self, monkeypatch):
        """No serper key but apify present → ok (informational, no error)."""
        mock_cfg = MagicMock()
        mock_cfg.serper_api_key = None
        mock_cfg.apify_api_key = "apify-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        line, is_err = network_check._check_serper()
        assert not is_err
        assert "Apify is primary" in line

    def test_should_close_client(self, tmp_path, monkeypatch):
        """should_close=True triggers client.close() (line 223)."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

        mock_cfg = MagicMock()
        mock_cfg.serper_api_key = "serper-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp

        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, True))

        line, is_err = network_check._check_serper()
        mock_client.close.assert_called_once()

    def test_limit_nonzero_branch(self, tmp_path, monkeypatch):
        """Serper 200 with non-zero limit skips default override (branch 229->232)."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

        mock_cfg = MagicMock()
        mock_cfg.serper_api_key = "serper-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 100 if p == "serper" else 0)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 80 if p == "serper" else 0)

        line, is_err = network_check._check_serper()
        assert not is_err
        assert "80 / 100" in line

    def test_other_http_error(self, monkeypatch):
        """HTTP 503 returns error (lines 241-243)."""
        mock_cfg = MagicMock()
        mock_cfg.serper_api_key = "serper-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client.post.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        line, is_err = network_check._check_serper()
        assert is_err
        assert "503" in line


# ---------------------------------------------------------------------------
# Direct tests for _check_apify branches
# ---------------------------------------------------------------------------


class TestCheckApifyDirect:
    def test_200_with_nonzero_limit(self, tmp_path, monkeypatch):
        """Apify 200 with existing quota → line 274 covered, 280->283 False branch."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

        mock_cfg = MagicMock()
        mock_cfg.apify_api_key = "apify-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 40 if p == "apify" else 0)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 35 if p == "apify" else 0)

        line, is_err, is_warn = network_check._check_apify()
        assert not is_err
        assert "35 / 40" in line

    def test_200_with_zero_limit_uses_default(self, tmp_path, monkeypatch):
        """Apify 200 with limit=0 → defaults applied (lines 280-283 True branch)."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

        mock_cfg = MagicMock()
        mock_cfg.apify_api_key = "apify-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 0)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 0)

        line, is_err, is_warn = network_check._check_apify()
        assert not is_err
        assert "40 / 40" in line

    def test_other_http_error(self, monkeypatch):
        """HTTP 500 returns error (lines 301-303)."""
        mock_cfg = MagicMock()
        mock_cfg.apify_api_key = "apify-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        line, is_err, is_warn = network_check._check_apify()
        assert is_err
        assert "500" in line

    def test_exception_returns_error(self, monkeypatch):
        """Exception inside apify check returns error (line 303)."""
        monkeypatch.setattr(config_module, "load_config",
                            lambda: (_ for _ in ()).throw(RuntimeError("apify boom")))

        line, is_err, is_warn = network_check._check_apify()
        assert is_err
        assert "apify boom" in line


# ---------------------------------------------------------------------------
# Direct tests for _check_apollo branches
# ---------------------------------------------------------------------------


class TestCheckApolloDirect:
    def test_email_enrichment_off_returns_skip(self, monkeypatch):
        """Email enrichment disabled → skip message (line 319)."""
        mock_cfg = MagicMock()
        mock_cfg.enable_email_enrichment = False
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        line, is_err, is_warn = network_check._check_apollo()
        assert not is_err
        assert "skipped" in line

    def test_no_apollo_key_enrichment_enabled(self, monkeypatch):
        """Enrichment enabled, no key → hunter-only message (lines 322-329)."""
        mock_cfg = MagicMock()
        mock_cfg.enable_email_enrichment = True
        mock_cfg.apollo_api_key = None
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        line, is_err, is_warn = network_check._check_apollo()
        assert not is_err
        assert "Hunter only" in line or "not configured" in line.lower()

    def test_with_key_limit_zero_uses_default(self, tmp_path, monkeypatch):
        """Apollo key present, limit=0 → default 50/50 (lines 333-335)."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

        mock_cfg = MagicMock()
        mock_cfg.enable_email_enrichment = True
        mock_cfg.apollo_api_key = "apollo-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 0)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 0)

        line, is_err, is_warn = network_check._check_apollo()
        assert not is_err
        assert "50 / 50" in line

    def test_with_key_limit_nonzero(self, tmp_path, monkeypatch):
        """Apollo key present, limit != 0 → quota shown (branch 333->336)."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

        mock_cfg = MagicMock()
        mock_cfg.enable_email_enrichment = True
        mock_cfg.apollo_api_key = "apollo-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 50)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 30)

        line, is_err, is_warn = network_check._check_apollo()
        assert not is_err
        assert "30 / 50" in line

    def test_exception_returns_error(self, monkeypatch):
        """Exception inside apollo → error line returned (lines 344-345)."""
        monkeypatch.setattr(config_module, "load_config",
                            lambda: (_ for _ in ()).throw(RuntimeError("apollo boom")))

        line, is_err, is_warn = network_check._check_apollo()
        assert is_err
        assert "apollo boom" in line


# ---------------------------------------------------------------------------
# Direct tests for _check_hunter additional branches
# ---------------------------------------------------------------------------


class TestCheckHunterDirect:
    def _setup(self, tmp_path, monkeypatch, mock_cfg=None):
        """Common setup: tmp DB + mock config with enrichment enabled."""
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()
        if mock_cfg is None:
            mock_cfg = MagicMock()
            mock_cfg.enable_email_enrichment = True
            mock_cfg.hunter_api_key = "hunter-key"
        monkeypatch.setattr(config_module, "load_config", lambda: mock_cfg)
        return mock_cfg

    def test_502_then_200_retries(self, tmp_path, monkeypatch):
        """502 on attempt 0 retries (branch 385->380); succeeds on attempt 1."""
        self._setup(tmp_path, monkeypatch)

        responses = [MagicMock(status_code=502), MagicMock(status_code=200)]
        call_count = [0]

        mock_client = MagicMock()

        def _get(*a, **kw):
            r = responses[call_count[0]]
            call_count[0] += 1
            return r

        mock_client.get.side_effect = _get
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 25)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 20)

        lines_out, is_err = network_check._check_hunter()
        assert not is_err
        assert call_count[0] == 2  # both attempts used

    def test_should_close_client(self, tmp_path, monkeypatch):
        """should_close=True closes the client (line 389)."""
        self._setup(tmp_path, monkeypatch)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, True))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 25)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 20)

        lines_out, is_err = network_check._check_hunter()
        mock_client.close.assert_called_once()

    def test_limit_zero_uses_default(self, tmp_path, monkeypatch):
        """Hunter 200 + limit=0 → defaults 25/25 (lines 396-397)."""
        self._setup(tmp_path, monkeypatch)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 0)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 0)

        lines_out, is_err = network_check._check_hunter()
        assert not is_err
        assert any("25 / 25" in ln for ln in lines_out)

    def test_remaining_gte_5_no_warning(self, tmp_path, monkeypatch):
        """remaining >= 5 → no warning line (branch 406->429 False)."""
        self._setup(tmp_path, monkeypatch)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        from src.providers.quota_manager import QuotaManager
        monkeypatch.setattr(QuotaManager, "get_limit", lambda self, p: 25)
        monkeypatch.setattr(QuotaManager, "remaining", lambda self, p: 10)

        lines_out, is_err = network_check._check_hunter()
        assert not is_err
        # No warning line (⚠) in output
        assert not any("⚠" in ln for ln in lines_out)

    def test_other_http_error(self, tmp_path, monkeypatch):
        """HTTP 503 returns error (lines 423-424)."""
        self._setup(tmp_path, monkeypatch)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client.get.return_value = mock_resp
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        lines_out, is_err = network_check._check_hunter()
        assert is_err
        assert any("503" in ln for ln in lines_out)

    def test_exception_returns_error(self, tmp_path, monkeypatch):
        """Exception inside hunter → error line (lines 425-427)."""
        self._setup(tmp_path, monkeypatch)

        mock_client = MagicMock()
        mock_client.get.side_effect = RuntimeError("hunter crashed")
        monkeypatch.setattr(network_check, "_get_http_client", lambda: (mock_client, False))

        lines_out, is_err = network_check._check_hunter()
        assert is_err
        assert any("hunter crashed" in ln for ln in lines_out)

    def test_no_hunter_key_enrichment_enabled(self, tmp_path, monkeypatch):
        """Enrichment on, no hunter key → error (lines 370-376)."""
        mock_cfg = MagicMock()
        mock_cfg.enable_email_enrichment = True
        mock_cfg.hunter_api_key = None
        self._setup(tmp_path, monkeypatch, mock_cfg=mock_cfg)

        lines_out, is_err = network_check._check_hunter()
        assert is_err
        assert any("HUNTER_API_KEY" in ln for ln in lines_out)


# ---------------------------------------------------------------------------
# Direct tests for _check_voice_doc branches
# ---------------------------------------------------------------------------


class TestCheckVoiceDocDirect:
    def test_empty_file_returns_warning(self, tmp_path, monkeypatch):
        """Empty voice.md returns warning (lines 450-451)."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        agent_dir = tmp_path / ".networking-agent"
        agent_dir.mkdir()
        voice_file = agent_dir / "voice.md"
        voice_file.write_text("")  # empty

        line, is_err, is_warn = network_check._check_voice_doc()
        assert not is_err
        assert is_warn
        assert "empty" in line.lower()


# ---------------------------------------------------------------------------
# run_checks exception handlers — all check functions forced to raise
# ---------------------------------------------------------------------------


class TestRunChecksExceptionHandlers:
    def _setup_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "_DB_PATH", tmp_path / "state.db")
        init_db()

    def test_all_checks_raise_returns_1(self, tmp_path, monkeypatch, capsys):
        """All check functions raise → all exception handlers fire, returns 1."""
        self._setup_db(tmp_path, monkeypatch)

        def boom():
            raise RuntimeError("forced")

        def boom_apify():
            raise RuntimeError("forced")

        def boom_hunter():
            raise RuntimeError("forced")

        def boom_apollo():
            raise RuntimeError("forced")

        def boom_voice():
            raise RuntimeError("forced")

        monkeypatch.setattr(network_check, "_check_sqlite_version", boom)
        monkeypatch.setattr(network_check, "_check_db_integrity", boom)
        monkeypatch.setattr(network_check, "_check_schema_version", boom)
        monkeypatch.setattr(network_check, "_check_config_permissions", boom)
        monkeypatch.setattr(network_check, "_check_anthropic", boom)
        monkeypatch.setattr(network_check, "_check_serper", boom)
        monkeypatch.setattr(network_check, "_check_apify", boom_apify)
        monkeypatch.setattr(network_check, "_check_hunter", boom_hunter)
        monkeypatch.setattr(network_check, "_check_apollo", boom_apollo)
        monkeypatch.setattr(network_check, "_check_voice_doc", boom_voice)

        result = network_check.run_checks()
        out = capsys.readouterr().out
        assert result == 1
        assert "✗" in out
        # Voice doc exception is a warning, not error — but there are 9 errors
        assert "errors" in out or "error" in out

    def test_apollo_returns_error_covers_line_558(self, tmp_path, monkeypatch, capsys):
        """Apollo returning is_err=True covers line 558."""
        self._setup_db(tmp_path, monkeypatch)

        monkeypatch.setattr(network_check, "_check_sqlite_version",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_db_integrity",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_schema_version",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_config_permissions",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_anthropic",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_serper",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_apify",
                            lambda: (network_check._ok("ok"), False, False))
        monkeypatch.setattr(network_check, "_check_hunter",
                            lambda: ([network_check._ok("ok")], False))
        # Apollo returns is_err=True (simulates internal exception caught by apollo)
        monkeypatch.setattr(network_check, "_check_apollo",
                            lambda: (network_check._err("apollo failed"), True, False))
        monkeypatch.setattr(network_check, "_check_voice_doc",
                            lambda: (network_check._ok("voice ok"), False, False))

        result = network_check.run_checks()
        assert result == 1
        out = capsys.readouterr().out
        assert "apollo failed" in out

    def test_voice_doc_exception_is_warning_not_error(self, tmp_path, monkeypatch, capsys):
        """Voice doc exception increments warning_count, not error_count (lines 573-575)."""
        self._setup_db(tmp_path, monkeypatch)

        monkeypatch.setattr(network_check, "_check_sqlite_version",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_db_integrity",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_schema_version",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_config_permissions",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_anthropic",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_serper",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_apify",
                            lambda: (network_check._ok("ok"), False, False))
        monkeypatch.setattr(network_check, "_check_hunter",
                            lambda: ([network_check._ok("ok")], False))
        monkeypatch.setattr(network_check, "_check_apollo",
                            lambda: (network_check._ok("ok"), False, False))
        monkeypatch.setattr(network_check, "_check_voice_doc",
                            lambda: (_ for _ in ()).throw(RuntimeError("voice boom")))

        result = network_check.run_checks()
        # Voice exception → warning only, so result=0 (no errors)
        assert result == 0
        out = capsys.readouterr().out
        assert "1 warning" in out

    def test_error_no_warning_summary_branch(self, tmp_path, monkeypatch, capsys):
        """error_count > 0, warning_count == 0 → '1 error. Fix errors...' (branch 585->587)."""
        self._setup_db(tmp_path, monkeypatch)

        monkeypatch.setattr(network_check, "_check_sqlite_version",
                            lambda: (network_check._err("sqlite bad"), True))
        monkeypatch.setattr(network_check, "_check_db_integrity",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_schema_version",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_config_permissions",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_anthropic",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_serper",
                            lambda: (network_check._ok("ok"), False))
        monkeypatch.setattr(network_check, "_check_apify",
                            lambda: (network_check._ok("ok"), False, False))
        monkeypatch.setattr(network_check, "_check_hunter",
                            lambda: ([network_check._ok("ok")], False))
        monkeypatch.setattr(network_check, "_check_apollo",
                            lambda: (network_check._ok("ok"), False, False))
        monkeypatch.setattr(network_check, "_check_voice_doc",
                            lambda: (network_check._ok("voice ok"), False, False))

        result = network_check.run_checks()
        assert result == 1
        out = capsys.readouterr().out
        assert "Fix errors" in out
        assert "warning" not in out.lower()
