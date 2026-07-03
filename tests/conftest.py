"""
tests/conftest.py
Shared pytest setup.

Disable the .env auto-loader for the whole test session so a developer's real
`.env` (with live API keys) can never leak into hermetic config tests. Unit
tests that exercise the loader call ``_load_dotenv(paths=[...])`` explicitly,
which bypasses this gate.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("NETWORKING_AGENT_NO_DOTENV", "1")
# A developer's active named profile must not leak into hermetic tests
# (profile-selection tests set it explicitly via monkeypatch).
os.environ.pop("NETWORKING_AGENT_PROFILE", None)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    """Point every test at a fresh, empty ``~/.networking-agent`` under a temp HOME.

    Several module-level paths are captured from ``Path.home()`` at import time
    (``_DB_PATH``, ``_config_path``, drafts/purge defaults) and read at call time
    (voice.md, ``.env``). Without isolation a developer's real home-dir state —
    config.yaml, state.db, voice.md, persona config — leaks in and makes tests
    non-deterministic (e.g. finder/hook tests that branch on real config).

    This fixture redirects HOME and every home-derived module path to a per-test
    temp dir. Tests that need specific state still create it under this temp home
    or monkeypatch a path explicitly; because those setattrs run after this
    fixture, they win.
    """
    home = tmp_path_factory.mktemp("home")
    base = home / ".networking-agent"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows
    for target, value in (
        ("src.core.db._DB_PATH", base / "state.db"),
        ("src.providers.quota_manager._DB_PATH", base / "state.db"),
        ("src.core.config._config_path", base / "config.yaml"),
        ("src.agents.artifact_writer._DEFAULT_OUTPUT_DIR", base / "drafts"),
        ("src.cli.network_purge._DEFAULT_LOG_PATH", base / "purge.log"),
        ("src.cli.network_purge._DEFAULT_DRAFTS_DIR", base / "drafts"),
    ):
        monkeypatch.setattr(target, value, raising=False)
