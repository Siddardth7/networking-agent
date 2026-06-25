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

os.environ.setdefault("NETWORKING_AGENT_NO_DOTENV", "1")
