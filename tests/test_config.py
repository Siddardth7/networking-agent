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
from src.core.config import ConfigSecurityError, load_config, write_default_config

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
