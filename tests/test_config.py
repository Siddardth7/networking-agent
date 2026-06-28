"""
tests/test_config.py — Unit tests for src/core/config.py

All tests use tmp_path and monkeypatch so they never touch
~/.networking-agent/config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

import src.core.config as config_module
from src.core.config import (
    ConfigSecurityError,
    _load_dotenv,
    load_config,
    write_default_config,
)

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


def test_load_dotenv_parses_and_respects_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_load_dotenv populates os.environ but never overrides an existing var."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "APIFY_API_KEY=apify_from_file\n"
        'export APOLLO_API_KEY="apollo_quoted"\n'
        "ALREADY_SET=from_file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("APIFY_API_KEY", raising=False)
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from_env")  # must win over the file

    _load_dotenv(paths=[env_file])

    assert os.environ["APIFY_API_KEY"] == "apify_from_file"
    assert os.environ["APOLLO_API_KEY"] == "apollo_quoted"  # export + quotes stripped
    assert os.environ["ALREADY_SET"] == "from_env"  # not overwritten


def test_load_dotenv_default_gated_off_in_tests() -> None:
    """The conftest opt-out flag makes the default (no-arg) load a no-op."""
    # NETWORKING_AGENT_NO_DOTENV is set by conftest; a no-arg call must not read
    # the developer's real .env. Must simply return without raising.
    _load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict, mode: int = 0o600) -> None:
    """Write *data* as YAML to *path* and set file permissions to *mode*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
    os.chmod(path, mode)


_VALID_YAML_DATA = {
    "keys": {
        "anthropic_api_key": "yaml-anthro-key",
        "serper_api_key": "yaml-serper-key",
        "hunter_api_key": "yaml-hunter-key",
    },
    "providers": {
        "serper_monthly_limit": 50,
        "hunter_monthly_limit": 10,
    },
    "pipeline": {
        "finder_limit": 3,
    },
}


# ---------------------------------------------------------------------------
# Test 1: Environment variables take precedence over YAML / missing file
# ---------------------------------------------------------------------------


def test_env_vars_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars should be used even when no config.yaml exists."""
    # Point module at a non-existent file inside tmp_path
    nonexistent = tmp_path / "config.yaml"
    monkeypatch.setattr(config_module, "_config_path", nonexistent)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthro")
    monkeypatch.setenv("SERPER_API_KEY", "test-key-serper")
    monkeypatch.setenv("HUNTER_API_KEY", "test-key-hunter")

    cfg = load_config()

    assert cfg.anthropic_api_key == "test-key-anthro"
    assert cfg.serper_api_key == "test-key-serper"
    assert cfg.hunter_api_key == "test-key-hunter"
    # Defaults apply when no YAML present
    assert cfg.serper_monthly_limit == 100
    assert cfg.hunter_monthly_limit == 25
    assert cfg.finder_limit == 5


# ---------------------------------------------------------------------------
# Test 2: YAML with 0o600 is read correctly
# ---------------------------------------------------------------------------


def test_yaml_with_correct_permissions_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config.yaml with mode 0o600 should be read and values returned."""
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, _VALID_YAML_DATA, mode=0o600)

    monkeypatch.setattr(config_module, "_config_path", cfg_path)
    # Unset any env vars that might shadow YAML values
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)

    cfg = load_config()

    assert cfg.anthropic_api_key == "yaml-anthro-key"
    assert cfg.serper_api_key == "yaml-serper-key"
    assert cfg.hunter_api_key == "yaml-hunter-key"
    assert cfg.serper_monthly_limit == 50
    assert cfg.hunter_monthly_limit == 10
    assert cfg.finder_limit == 3


# ---------------------------------------------------------------------------
# Test 3: YAML with 0o644 raises ConfigSecurityError
# ---------------------------------------------------------------------------


def test_yaml_with_bad_permissions_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config.yaml with mode 0o644 must raise ConfigSecurityError."""
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, _VALID_YAML_DATA, mode=0o644)

    monkeypatch.setattr(config_module, "_config_path", cfg_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)

    with pytest.raises(ConfigSecurityError) as exc_info:
        load_config()

    error_msg = str(exc_info.value)
    assert "0o644" in error_msg
    assert "chmod 600" in error_msg


# ---------------------------------------------------------------------------
# Test 4: write_default_config creates a file with mode 0o600
# ---------------------------------------------------------------------------


def test_write_default_config_creates_with_0o600(tmp_path: Path) -> None:
    """write_default_config should create config.yaml and chmod it to 0o600."""
    cfg_path = tmp_path / "subdir" / "config.yaml"

    assert not cfg_path.exists(), "file should not exist before the call"
    write_default_config(cfg_path)

    assert cfg_path.exists(), "write_default_config must create the file"

    actual_mode = os.stat(cfg_path).st_mode & 0o777
    assert actual_mode == 0o600, f"Expected 0o600 but got {oct(actual_mode)}"

    # Verify the written YAML is parseable and contains sentinel values
    data = yaml.safe_load(cfg_path.read_text())
    assert data["keys"]["anthropic_api_key"] == "REPLACE_ME"
    assert data["keys"]["serper_api_key"] == "REPLACE_ME"
    assert data["keys"]["hunter_api_key"] == "REPLACE_ME"


# ---------------------------------------------------------------------------
# NETWORKING_AGENT_CONFIG env override
# ---------------------------------------------------------------------------


def test_networking_agent_config_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NETWORKING_AGENT_CONFIG should redirect load_config to a custom path."""
    custom_path = tmp_path / "custom" / "alt.yaml"
    _write_yaml(
        custom_path,
        {
            "keys": {
                "anthropic_api_key": "override-anthro",
                "serper_api_key": "override-serper",
                "hunter_api_key": "override-hunter",
            }
        },
    )

    # Point the default at a nonexistent file so we can prove the override wins
    monkeypatch.setattr(config_module, "_config_path", tmp_path / "default.yaml")
    monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(custom_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)

    cfg = load_config()
    assert cfg.anthropic_api_key == "override-anthro"
    assert cfg.serper_api_key == "override-serper"
    assert cfg.hunter_api_key == "override-hunter"


def test_get_anthropic_client_raises_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_anthropic_client should raise ValueError when no key is configured."""
    from src.core.config import get_anthropic_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NETWORKING_AGENT_CONFIG", raising=False)
    # Point at a nonexistent default to force a None key
    monkeypatch.setattr(config_module, "_config_path", Path("/nonexistent/x.yaml"))

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY not configured"):
        get_anthropic_client()


def test_drafter_max_workers_defaults_to_three() -> None:
    """Finding A: the out-of-the-box drafter concurrency must stay low enough
    to keep a full batch under the Anthropic Tier-1 ITPM ceiling (50k/min).
    A regression that raises this default would reintroduce batch 429s."""
    from src.core.config import Config

    assert Config().drafter_max_workers == 3


# ---------------------------------------------------------------------------
# _check_permissions direct test (dead code — never called internally)
# ---------------------------------------------------------------------------


def test_check_permissions_raises_on_bad_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_check_permissions raises ConfigSecurityError when mode != 0o600 (lines 165-167)."""
    from src.core.config import _check_permissions

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("keys: {}")
    os.chmod(cfg_file, 0o644)

    with pytest.raises(ConfigSecurityError):
        _check_permissions(cfg_file)


def test_check_permissions_ok_on_correct_mode(tmp_path: Path) -> None:
    """_check_permissions does NOT raise when mode is 0o600."""
    from src.core.config import _check_permissions

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("keys: {}")
    os.chmod(cfg_file, 0o600)

    _check_permissions(cfg_file)  # should not raise


# ---------------------------------------------------------------------------
# write_default_config skips existing file (line 182)
# ---------------------------------------------------------------------------


def test_write_default_config_skips_if_file_exists(tmp_path: Path) -> None:
    """write_default_config is a no-op when the file already exists (line 182)."""
    cfg_path = tmp_path / "config.yaml"
    original_content = "original: true\n"
    cfg_path.write_text(original_content)
    os.chmod(cfg_path, 0o600)

    write_default_config(cfg_path)

    # File must be unchanged
    assert cfg_path.read_text() == original_content


# ---------------------------------------------------------------------------
# _load_dotenv: default-path branch when NO_DOTENV is not set (line 243)
# ---------------------------------------------------------------------------


def test_load_dotenv_uses_default_paths_when_no_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With NO_DOTENV unset, _load_dotenv sets paths from CWD/.env etc. (line 243)."""
    monkeypatch.delenv("NETWORKING_AGENT_NO_DOTENV", raising=False)
    # No .env file in CWD or repo root → no-op but line 243 is reached
    _load_dotenv()  # must not raise; just resolves default paths


# ---------------------------------------------------------------------------
# _load_dotenv: skip non-existent paths (line 249 - continue)
# ---------------------------------------------------------------------------


def test_load_dotenv_skips_nonexistent_path(tmp_path: Path) -> None:
    """Passing a non-existent file path silently skips it (line 249)."""
    nonexistent = tmp_path / "does_not_exist.env"
    _load_dotenv(paths=[nonexistent])  # must not raise; the continue fires


def test_load_dotenv_skips_duplicate_paths(tmp_path: Path) -> None:
    """Duplicate path in list is only processed once (line 249 - already seen)."""
    env_file = tmp_path / ".env"
    env_file.write_text("DUP_KEY=value\n")

    import os as _os
    _os.environ.pop("DUP_KEY", None)
    _load_dotenv(paths=[env_file, env_file])  # same file twice
    assert _os.environ.get("DUP_KEY") == "value"
    # cleanup
    _os.environ.pop("DUP_KEY", None)


# ---------------------------------------------------------------------------
# _load_dotenv: OSError on unreadable file (lines 253-254)
# ---------------------------------------------------------------------------


def test_load_dotenv_handles_oserror_on_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError while reading the file is silently ignored (lines 253-254)."""
    env_file = tmp_path / ".env"
    env_file.write_text("SHOULD_NOT_SET=1\n")

    original_read = Path.read_text

    def boom(self, *args, **kwargs):
        if self == env_file:
            raise OSError("permission denied")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)

    _load_dotenv(paths=[env_file])  # must not raise
    import os as _os
    assert "SHOULD_NOT_SET" not in _os.environ


# ---------------------------------------------------------------------------
# _load_dotenv: line without '=' separator (line 263 - continue)
# ---------------------------------------------------------------------------


def test_load_dotenv_skips_line_without_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lines without '=' are skipped (line 263)."""
    env_file = tmp_path / ".env"
    env_file.write_text("NOSEPARATORHERE\nGOOD_KEY=good_value\n")
    monkeypatch.delenv("GOOD_KEY", raising=False)

    _load_dotenv(paths=[env_file])
    import os as _os
    assert _os.environ.get("GOOD_KEY") == "good_value"
    _os.environ.pop("GOOD_KEY", None)


# ---------------------------------------------------------------------------
# get_anthropic_client with explicit key (lines 358->361, 364-370)
# ---------------------------------------------------------------------------


def test_get_anthropic_client_with_explicit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing api_key directly skips load_config (branch 358->361) and builds client (364-370)."""
    from unittest.mock import MagicMock, patch

    from src.core.config import get_anthropic_client

    mock_client = MagicMock()
    with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
        result = get_anthropic_client(api_key="explicit-key")

    mock_cls.assert_called_once_with(api_key="explicit-key", max_retries=8)
    assert result is mock_client


def test_finder_role_keywords_default() -> None:
    """Config defaults to the aerospace keyword set (issue #8 / D2)."""
    cfg = config_module.Config()
    assert cfg.finder_role_keywords == config_module.DEFAULT_ROLE_KEYWORDS
    # default_factory yields a fresh copy — no shared-mutable-default leak
    assert cfg.finder_role_keywords is not config_module.DEFAULT_ROLE_KEYWORDS


def test_finder_role_keywords_yaml_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pipeline.finder_role_keywords in YAML overrides the default (config-driven)."""
    custom = tmp_path / "alt.yaml"
    _write_yaml(custom, {"pipeline": {"finder_role_keywords": ["data scientist", "ml engineer"]}})
    monkeypatch.setattr(config_module, "_config_path", tmp_path / "default.yaml")
    monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(custom))
    cfg = load_config()
    assert cfg.finder_role_keywords == ["data scientist", "ml engineer"]
