"""
src/cli/network_check.py — Preflight setup checks for networking-agent.

Traceability: DESIGN.md §8.9

Run standalone:
    python -m src.cli.network_check
    # or
    python src/cli/network_check.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from src.core.config import HAIKU_MODEL
from src.providers.hunter import scrubbed_hunter_call

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LATEST_MIGRATION = 4  # 004_search_cache (v0.2.1)

# ---------------------------------------------------------------------------
# Test injection hook — set before calling run_checks()
# ---------------------------------------------------------------------------
_http_client = None


def set_http_client(client) -> None:  # noqa: ANN001
    global _http_client
    _http_client = client


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _ok(msg: str) -> str:
    return f"  ✓ {msg}"


def _err(msg: str) -> str:
    return f"  ✗ {msg}"


def _warn(msg: str) -> str:
    return f"  ⚠ {msg}"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_sqlite_version() -> tuple[str, bool]:
    """Check 1: SQLite version >= 3.39.0"""
    info = sqlite3.sqlite_version_info
    version_str = sqlite3.sqlite_version
    if info >= (3, 39, 0):
        return _ok(f"SQLite version 3.39+ ({version_str})"), False
    else:
        return _err(f"SQLite 3.39+ required (found {version_str})"), True


def _check_db_integrity() -> tuple[str, bool]:
    """Check 2: DB integrity check + WAL mode."""
    try:
        from src.core.db import _DB_PATH, get_connection, init_db  # noqa: PLC0415

        db_path = _DB_PATH
        if not db_path.exists():
            init_db()

        conn = get_connection()
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()

        if integrity != "ok":
            return _err(f"DB integrity: state.db FAILED ({integrity})"), True
        if journal != "wal":
            return _err(f"DB integrity: WAL mode not active (got {journal!r})"), True
        return _ok("DB integrity: state.db OK (WAL mode active)"), False
    except Exception as exc:
        return _err(f"DB integrity check failed: {exc}"), True


def _check_schema_version() -> tuple[str, bool]:
    """Check 3: Schema user_version matches LATEST_MIGRATION."""
    try:
        from src.core.db import get_connection  # noqa: PLC0415

        conn = get_connection()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()

        if version == LATEST_MIGRATION:
            return _ok(f"Schema version: {version} (latest)"), False
        else:
            return _err(
                f"Schema version mismatch: DB has {version}, expected {LATEST_MIGRATION}"
            ), True
    except Exception as exc:
        return _err(f"Schema version check failed: {exc}"), True


def _check_config_permissions() -> tuple[str, bool]:
    """Check 4: config.yaml file permissions."""
    # If all three keys are in env, skip file check
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_serper = bool(os.environ.get("SERPER_API_KEY"))
    has_hunter = bool(os.environ.get("HUNTER_API_KEY"))

    if has_anthropic and has_serper and has_hunter:
        return _ok("config.yaml permissions: using env vars (file check skipped)"), False

    config_path = Path.home() / ".networking-agent" / "config.yaml"
    if not config_path.exists():
        return _err(
            "config.yaml not found — run: networking-agent setup to create "
            "~/.networking-agent/config.yaml"
        ), True

    mode = os.stat(config_path).st_mode & 0o777
    if mode != 0o600:
        return _err(
            f"config.yaml permissions: {oct(mode)} — run: chmod 600 ~/.networking-agent/config.yaml"
        ), True

    return _ok("config.yaml permissions: 0600"), False


def _get_http_client():
    """Return module-level client if set, else a fresh httpx.Client."""
    if _http_client is not None:
        return _http_client, False  # (client, should_close)
    import httpx  # noqa: PLC0415

    return httpx.Client(timeout=10), True


def _check_anthropic() -> tuple[str, bool]:
    """Check 5: Anthropic API live ping."""
    try:
        from src.core.config import load_config  # noqa: PLC0415

        cfg = load_config()
        key = cfg.anthropic_api_key
        if not key:
            return _err(
                "Anthropic API key: not configured — set ANTHROPIC_API_KEY "
                "env var or update config.yaml"
            ), True

        client, should_close = _get_http_client()
        try:
            resp = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": HAIKU_MODEL,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
        finally:
            if should_close:
                client.close()

        if resp.status_code == 200:
            return _ok("Anthropic API key: valid (live ping 200)"), False
        elif resp.status_code in (401, 403):
            return _err(
                f"Anthropic API key: invalid (HTTP {resp.status_code}) — "
                f"set ANTHROPIC_API_KEY env var or update config.yaml"
            ), True
        else:
            return _err(f"Anthropic API key: ping failed (HTTP {resp.status_code})"), True
    except Exception as exc:
        return _err(f"Anthropic API key: check failed ({exc})"), True


def _check_serper() -> tuple[str, bool]:
    """Check 6: Serper API live ping + quota."""
    try:
        from src.core.config import load_config  # noqa: PLC0415
        from src.providers.quota_manager import QuotaManager  # noqa: PLC0415

        cfg = load_config()
        key = cfg.serper_api_key
        if not key:
            return _err(
                "Serper API key: not configured — set SERPER_API_KEY env var or update config.yaml"
            ), True

        client, should_close = _get_http_client()
        try:
            resp = client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "content-type": "application/json"},
                json={"q": "test", "num": 1},
            )
        finally:
            if should_close:
                client.close()

        if resp.status_code == 200:
            qm = QuotaManager()
            remaining = qm.remaining("serper")
            limit = qm.get_limit("serper")
            if limit == 0:
                limit = 100
                remaining = 100
            return _ok(
                f"Serper API key: valid ({remaining} / {limit} free queries remaining this month)"
            ), False
        elif resp.status_code in (401, 403):
            return _err(
                f"Serper API key: invalid (HTTP {resp.status_code}) — "
                f"set SERPER_API_KEY env var or update config.yaml"
            ), True
        else:
            return _err(f"Serper API key: ping failed (HTTP {resp.status_code})"), True
    except Exception as exc:
        return _err(f"Serper API key: check failed ({exc})"), True


def _check_hunter() -> tuple[list[str], bool]:
    """Check 7: Hunter API live ping + quota. Returns multiple lines."""
    lines: list[str] = []
    is_error = False
    try:
        from src.core.config import load_config  # noqa: PLC0415
        from src.providers.quota_manager import QuotaManager  # noqa: PLC0415

        cfg = load_config()
        if not cfg.enable_email_enrichment:
            # v0.2.1: email enrichment is opt-in; a missing Hunter key must
            # not fail preflight when the pipeline will never call Hunter.
            lines.append(
                _ok(
                    "Hunter: skipped — email enrichment disabled "
                    "(pipeline.enable_email_enrichment: false)"
                )
            )
            return lines, False

        key = cfg.hunter_api_key
        if not key:
            lines.append(
                _err(
                    "Hunter API key: not configured — set HUNTER_API_KEY "
                    "env var or update config.yaml"
                )
            )
            return lines, True

        client, should_close = _get_http_client()
        try:
            for attempt in range(2):
                with scrubbed_hunter_call(key):
                    resp = client.get(
                        f"https://api.hunter.io/v2/account?api_key={key}",
                    )
                if resp.status_code != 502 or attempt == 1:
                    break
        finally:
            if should_close:
                client.close()

        if resp.status_code == 200:
            qm = QuotaManager()
            remaining = qm.remaining("hunter")
            limit = qm.get_limit("hunter")
            if limit == 0:
                limit = 25
                remaining = 25

            lines.append(
                _ok(
                    f"Hunter API key: valid ({remaining} / {limit} free "
                    f"searches remaining this month)"
                )
            )

            if remaining < 5:
                companies = remaining / 5
                lines.append(
                    _warn(
                        f"Hunter free tier nearly exhausted: {remaining} / "
                        f"{limit} searches remaining (≈{companies:.1f} companies)"
                    )
                )
        elif resp.status_code in (401, 403):
            lines.append(
                _err(
                    f"Hunter API key: invalid (HTTP {resp.status_code}) — "
                    f"set HUNTER_API_KEY env var or update config.yaml"
                )
            )
            is_error = True
        else:
            lines.append(_err(f"Hunter API key: ping failed (HTTP {resp.status_code})"))
            is_error = True
    except Exception as exc:
        lines.append(_err(f"Hunter API key: check failed ({exc})"))
        is_error = True

    return lines, is_error


def _check_voice_doc() -> tuple[str, bool, bool]:
    """Check 8: voice.md. Returns (line, is_error, is_warning)."""
    voice_path = Path.home() / ".networking-agent" / "voice.md"
    if not voice_path.exists():
        return _warn("Voice doc not found: create ~/.networking-agent/voice.md"), False, True

    try:
        size = voice_path.stat().st_size
        if size == 0:
            return _warn("Voice doc: ~/.networking-agent/voice.md is empty"), False, True
        # Try reading to confirm it's readable
        voice_path.read_text(encoding="utf-8")
        kb = size / 1024
        return (
            _ok(f"Voice doc: ~/.networking-agent/voice.md ({kb:.1f} KB, parsed OK)"),
            False,
            False,
        )
    except Exception as exc:
        return _warn(f"Voice doc: unreadable ({exc})"), False, True


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_checks() -> int:
    """Run all preflight checks. Returns 0 (all green) or 1 (any error)."""
    lines: list[str] = []
    error_count = 0
    warning_count = 0

    lines.append("Networking Agent — Setup Check")

    # Check 1: SQLite version
    try:
        line, is_err = _check_sqlite_version()
        lines.append(line)
        if is_err:
            error_count += 1
    except Exception as exc:
        lines.append(_err(f"SQLite version check failed: {exc}"))
        error_count += 1

    # Check 2: DB integrity + WAL
    try:
        line, is_err = _check_db_integrity()
        lines.append(line)
        if is_err:
            error_count += 1
    except Exception as exc:
        lines.append(_err(f"DB integrity check failed: {exc}"))
        error_count += 1

    # Check 3: Schema version
    try:
        line, is_err = _check_schema_version()
        lines.append(line)
        if is_err:
            error_count += 1
    except Exception as exc:
        lines.append(_err(f"Schema version check failed: {exc}"))
        error_count += 1

    # Check 4: config.yaml permissions
    try:
        line, is_err = _check_config_permissions()
        lines.append(line)
        if is_err:
            error_count += 1
    except Exception as exc:
        lines.append(_err(f"Config permissions check failed: {exc}"))
        error_count += 1

    # Check 5: Anthropic live ping
    try:
        line, is_err = _check_anthropic()
        lines.append(line)
        if is_err:
            error_count += 1
    except Exception as exc:
        lines.append(_err(f"Anthropic check failed: {exc}"))
        error_count += 1

    # Check 6: Serper live ping + quota
    try:
        line, is_err = _check_serper()
        lines.append(line)
        if is_err:
            error_count += 1
    except Exception as exc:
        lines.append(_err(f"Serper check failed: {exc}"))
        error_count += 1

    # Check 7: Hunter live ping + quota (may emit multiple lines)
    try:
        hunter_lines, is_err = _check_hunter()
        lines.extend(hunter_lines)
        if is_err:
            error_count += 1
        # Count warning lines separately
        for hl in hunter_lines:
            if hl.startswith("  ⚠"):
                warning_count += 1
    except Exception as exc:
        lines.append(_err(f"Hunter check failed: {exc}"))
        error_count += 1

    # Check 8: Voice doc
    try:
        line, is_err, is_warn = _check_voice_doc()
        lines.append(line)
        if is_err:
            error_count += 1
        if is_warn:
            warning_count += 1
    except Exception as exc:
        lines.append(_warn(f"Voice doc check failed: {exc}"))
        warning_count += 1

    # Summary
    lines.append("")
    if error_count == 0 and warning_count == 0:
        lines.append("All checks passed. You're ready to run /network-run.")
    else:
        parts: list[str] = []
        if error_count > 0:
            parts.append(f"{error_count} {'error' if error_count == 1 else 'errors'}")
        if warning_count > 0:
            parts.append(f"{warning_count} {'warning' if warning_count == 1 else 'warnings'}")
        summary = ", ".join(parts) + "."
        if error_count > 0:
            summary += " Fix errors before running /network-run."
        lines.append(summary)

    output = "\n".join(lines)
    print(output)

    return 1 if error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(run_checks())
