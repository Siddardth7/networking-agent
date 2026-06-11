"""
src/core/config.py — Secrets-aware configuration loader.

Resolution order for API keys:
  1. Environment variable (ANTHROPIC_API_KEY, SERPER_API_KEY, HUNTER_API_KEY)
  2. ~/.networking-agent/config.yaml under keys.anthropic_api_key,
     keys.serper_api_key, keys.hunter_api_key

The config file path itself can be overridden by setting the
``NETWORKING_AGENT_CONFIG`` environment variable.

Security: config.yaml must have mode 0o600. On first write, chmod is applied
automatically. On every read, mode is verified and ConfigSecurityError is raised
if permissions are too open.

Exports: load_config, Config, ConfigSecurityError, HAIKU_MODEL,
get_anthropic_client
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# The cheap, fast Claude model used for high-volume generation paths
# (Finder classification, Drafter first-pass writing, dispatch REVISE,
# network_check live ping). Update here when bumping model versions.
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Stronger model reserved for the critic pass (Layer 4). Worth the extra
# tokens because it is the final automated gate before send.
SONNET_MODEL = "claude-sonnet-4-6"

# Module-level path — tests monkeypatch this to use tmp_path. At runtime,
# _resolve_config_path() may override it via NETWORKING_AGENT_CONFIG env var.
_config_path: Path = Path.home() / ".networking-agent" / "config.yaml"

_SENTINEL = "REPLACE_ME"


def _resolve_config_path() -> Path:
    """Return the effective config path.

    Resolution order:
      1. NETWORKING_AGENT_CONFIG env var (if set, expand ~ and return)
      2. Module-level _config_path (monkeypatched by tests, else the default)
    """
    override = os.environ.get("NETWORKING_AGENT_CONFIG")
    if override:
        return Path(override).expanduser()
    return _config_path


def config_dir() -> Path:
    """Return the directory holding the agent's user files.

    Inputs: none (reads the resolved config path). Output: the parent
    directory of config.yaml — by default ``~/.networking-agent/``, or
    wherever ``NETWORKING_AGENT_CONFIG`` points. Sibling files
    (voice.md, resume_library.yaml) are resolved relative to this so an
    env-relocated config relocates them too (AUDIT-A26).
    """
    return _resolve_config_path().parent


def voice_doc_path() -> Path:
    """Return the path of the user's voice/style document."""
    return config_dir() / "voice.md"


def resume_library_path() -> Path:
    """Return the path of the user's resume achievement library."""
    return config_dir() / "resume_library.yaml"


class ConfigSecurityError(Exception):
    """Raised when config.yaml has unsafe file permissions."""


@dataclass
class Config:
    """All configuration fields for the networking-agent."""

    # API keys — Optional because callers that need them should validate.
    anthropic_api_key: str | None = None
    serper_api_key: str | None = None
    hunter_api_key: str | None = None

    # Provider limits
    serper_monthly_limit: int = 100
    hunter_monthly_limit: int = 25

    # Pipeline settings
    finder_limit: int = 5

    # Quality / channel constraints (Layer 3+5). These are the hard limits
    # enforced in code by `guardrails.hard_check` — keep in sync with the
    # prompt text in `drafter._CHANNEL_CONSTRAINTS`.
    linkedin_char_limit: int = 200  # free LinkedIn account cap
    email_word_limit: int = 150  # cold-email body word cap

    # Batch-quality checkpoint between Drafter and Marketer.
    # batch_hard_fail_threshold = max fraction of HARD_FAIL drafts tolerated
    # before the orchestrator warns the user (warn-and-continue; never aborts).
    batch_hard_fail_threshold: float = 0.0

    # Layer 4: enable the automated critic pass. When False, drafts skip
    # critic review and only pass through guardrails.
    enable_critic: bool = True

    # Layer 1-A (variety): max contacts per run that may share the same
    # normalized opener per channel before the drafter forces a rewrite.
    opener_max_repeats: int = 2


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
    """Read and parse *path*, verifying permissions on the OPEN descriptor.

    fstat-after-open closes the TOCTOU window between a stat-based check
    and the read (AUDIT-A18): the mode is checked on the exact file the
    process is about to parse, so a swap between check and open cannot
    smuggle in a world-readable file.
    """
    with path.open("r", encoding="utf-8") as fh:
        mode = os.fstat(fh.fileno()).st_mode & 0o777
        if mode != 0o600:
            raise ConfigSecurityError(
                f"Refusing to read {path}: permissions are {oct(mode)}. Run: chmod 600 {path}"
            )
        return yaml.safe_load(fh) or {}


def _key_or_none(value: str | None) -> str | None:
    """Return None if value is missing or is the placeholder sentinel."""
    if not value or value == _SENTINEL:
        return None
    return value


def load_config() -> Config:
    """Load configuration, merging env vars and YAML (env vars win).

    Returns a :class:`Config` instance. API key fields may be None if not
    configured — callers that require them must validate themselves.
    """
    path: Path = _resolve_config_path()  # env override → module-level → default

    # --- Load YAML if it exists (and verify permissions) ---
    yaml_data: dict = {}
    if path.exists():
        yaml_data = _load_yaml(path)

    yaml_keys: dict = yaml_data.get("keys", {})
    yaml_providers: dict = yaml_data.get("providers", {})
    yaml_pipeline: dict = yaml_data.get("pipeline", {})
    yaml_quality: dict = yaml_data.get("quality", {})

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

    linkedin_char_limit = int(yaml_quality.get("linkedin_char_limit", 200))
    email_word_limit = int(yaml_quality.get("email_word_limit", 150))
    batch_hard_fail_threshold = float(yaml_quality.get("batch_hard_fail_threshold", 0.0))
    enable_critic = bool(yaml_quality.get("enable_critic", True))
    opener_max_repeats = int(yaml_quality.get("opener_max_repeats", 2))

    return Config(
        anthropic_api_key=anthropic_api_key,
        serper_api_key=serper_api_key,
        hunter_api_key=hunter_api_key,
        serper_monthly_limit=serper_monthly_limit,
        hunter_monthly_limit=hunter_monthly_limit,
        finder_limit=finder_limit,
        linkedin_char_limit=linkedin_char_limit,
        email_word_limit=email_word_limit,
        batch_hard_fail_threshold=batch_hard_fail_threshold,
        enable_critic=enable_critic,
        opener_max_repeats=opener_max_repeats,
    )


def get_anthropic_client(api_key: str | None = None):
    """Return a fresh ``anthropic.Anthropic`` client.

    Centralizes the lazy-import + key-resolution pattern previously duplicated
    across Finder, Drafter, and dispatch. Pass ``api_key`` to override the
    configured key (mainly for tests).

    Raises ``ValueError`` if no key is configured and none is provided.
    """
    if api_key is None:
        cfg = load_config()
        api_key = cfg.anthropic_api_key
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    from anthropic import Anthropic  # local import keeps module import-light

    return Anthropic(api_key=api_key)
