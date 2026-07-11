"""Shared helpers for the networking-agent CLI bridges."""

from __future__ import annotations

import logging
import sys

_CLI_LOG_HANDLER = "networking_agent_cli_stderr"


def configure_cli_logging(level: int = logging.INFO) -> None:
    """Route ``networking_agent`` logs to stderr for a CLI run (idempotent).

    The library logs discovery diagnostics — per-provider counts, provider
    failures, shortfalls — on the ``networking_agent`` logger tree, but a CLI
    process installs no handler by default, so those messages vanish and a
    0-result run looks identical to "no such contacts exist" (issue #96).
    Install one stderr handler on the parent logger (children propagate up) so
    the diagnostics surface; JSON on stdout stays clean.
    """
    logger = logging.getLogger("networking_agent")
    if any(getattr(h, "name", None) == _CLI_LOG_HANDLER for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_CLI_LOG_HANDLER)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)


def read_stdin_text() -> str:
    """Read all of stdin as text, stripping a leading UTF-8 BOM if present.

    Windows PowerShell prepends a UTF-8 BOM when piping to a native process,
    which makes ``json.loads`` (and content validation) choke on the first
    char (U+FEFF). Reading in text mode (stdin is UTF-8 under the launchers'
    ``PYTHONUTF8=1``) and ``lstrip``-ing the BOM fixes it while staying
    compatible with the test suite's ``io.StringIO`` stdin stubs.
    """
    return sys.stdin.read().lstrip("\ufeff")
