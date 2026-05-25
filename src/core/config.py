"""
src/core/config.py — Secrets-aware configuration loader.

Resolution order for API keys:
  1. Environment variable (ANTHROPIC_API_KEY, SERPER_API_KEY, HUNTER_API_KEY)
  2. ~/.networking-agent/config.yaml under keys:

Security: config.yaml must have mode 0o600. On first write, chmod is applied
automatically. On every read, mode is verified and ConfigSecurityError is raised
if permissions are too open.

Exports: load_config, Config, ConfigSecurityError
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Module-level path — tests monkeypatch this to use tmp_path.
_config_path: Path = Path.home() / ".networking-agent" / "config.yaml"

_SENTINEL = "REPLACE_ME"


class ConfigSecurityError(Exception):
    """Raised when config.yaml has unsafe file permissions."""


@dataclass
class Config:
    """All configuration fields for the networking-agent."""

    # API keys — Optional because callers that need them should validate.
    anthropic_api_key: Optional[str] = None
    serper_api_key: Optional[str] = None
    hunter_api_key: Optional[str] = None

    # Provider limits
    serper_monthly_limit: int = 100
    hunter_monthly_limit: int = 25

    # Pipeline settings
    finder_limit: int = 5


def _check_permissions(path: Path) -> None:
    """Raise ConfigSecurityError if path mode is not 0o600."""
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        raise ConfigSecurityError(
            f"Refusing to read ~/.networking-agent/config.yaml: "
            f"permissions are {oct(mode)}. "
            f"Run: chmod 600 ~/.networking-agent/config.yaml"
        )


def write_default_config(path: Path) -> None:
    """Write a skeleton config.yaml to *path* and lock it to 0o600.

    Creates parent directories as needed.
    Intended for first-time setup; does not overwrite existing files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return

    skeleton = {
        "keys": {
            "anthropic_api_key": _SENTINEL,
            "serper_api_key": _SENTINEL,
            "hunter_api_key": _SENTINEL,
        },
        "providers": {
            "serper_monthly_limit": 100,
            "hunter_monthly_limit": 25,
        },
        "pipeline": {
            "finder_limit": 5,
        },
    }
    path.write_text(yaml.safe_dump(skeleton, default_flow_style=False), encoding="utf-8")
    os.chmod(path, 0o600)


def _load_yaml(path: Path) -> dict:
    """Read and parse *path* after verifying its permissions."""
    _check_permissions(path)
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _key_or_none(value: Optional[str]) -> Optional[str]:
    """Return None if value is missing or is the placeholder sentinel."""
    if not value or value == _SENTINEL:
        return None
    return value


def load_config() -> Config:
    """Load configuration, merging env vars and YAML (env vars win).

    Returns a :class:`Config` instance. API key fields may be None if not
    configured — callers that require them must validate themselves.
    """
    path: Path = _config_path  # reads module-level so tests can monkeypatch

    # --- Load YAML if it exists (and verify permissions) ---
    yaml_data: dict = {}
    if path.exists():
        yaml_data = _load_yaml(path)

    yaml_keys: dict = yaml_data.get("keys", {})
    yaml_providers: dict = yaml_data.get("providers", {})
    yaml_pipeline: dict = yaml_data.get("pipeline", {})

    # --- Resolve API keys: env wins, then YAML ---
    anthropic_api_key = _key_or_none(
        os.environ.get("ANTHROPIC_API_KEY") or yaml_keys.get("anthropic_api_key")
    )
    serper_api_key = _key_or_none(
        os.environ.get("SERPER_API_KEY") or yaml_keys.get("serper_api_key")
    )
    hunter_api_key = _key_or_none(
        os.environ.get("HUNTER_API_KEY") or yaml_keys.get("hunter_api_key")
    )

    # --- Numeric settings (YAML only; no env override needed per spec) ---
    serper_monthly_limit = int(yaml_providers.get("serper_monthly_limit", 100))
    hunter_monthly_limit = int(yaml_providers.get("hunter_monthly_limit", 25))
    finder_limit = int(yaml_pipeline.get("finder_limit", 5))

    return Config(
        anthropic_api_key=anthropic_api_key,
        serper_api_key=serper_api_key,
        hunter_api_key=hunter_api_key,
        serper_monthly_limit=serper_monthly_limit,
        hunter_monthly_limit=hunter_monthly_limit,
        finder_limit=finder_limit,
    )
