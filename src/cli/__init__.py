"""Shared helpers for the networking-agent CLI bridges."""

from __future__ import annotations

import sys


def read_stdin_text() -> str:
    """Read all of stdin as text, stripping a leading UTF-8 BOM if present.

    Windows PowerShell prepends a UTF-8 BOM when piping to a native process,
    which makes ``json.loads`` (and content validation) choke on the first
    char (U+FEFF). Reading in text mode (stdin is UTF-8 under the launchers'
    ``PYTHONUTF8=1``) and ``lstrip``-ing the BOM fixes it while staying
    compatible with the test suite's ``io.StringIO`` stdin stubs.
    """
    return sys.stdin.read().lstrip("\ufeff")
