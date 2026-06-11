"""
src/cli/network_providers.py — List configured search/email providers + quota.

Traceability: DESIGN.md §3, §8.13

v0.1.0 ships read-only: lists the 2 hardcoded providers (serper, hunter) with
remaining quota.  The --add and --test flags are reserved for v0.1.1.

Entry point
-----------
    run_providers(args, _quota_manager=None) -> int

    ``_quota_manager`` is a test-injection hook.  Pass a QuotaManager instance
    built against a tmp DB to keep tests hermetic.
"""

from __future__ import annotations

import argparse

__all__ = ["run_providers"]

# Hardcoded provider list for v0.1.0.
_PROVIDERS: list[str] = ["serper", "hunter"]


def run_providers(
    args: argparse.Namespace,
    _quota_manager=None,  # noqa: ANN001 — test injection hook
) -> int:
    """List providers and remaining quota, or print stub message for --add.

    Parameters
    ----------
    args:
        Parsed argparse namespace.  Recognised attributes:

        ``add`` (Optional[str])
            Provider name passed to ``--add``.  When set, prints the v0.1.1
            stub message and returns 0 without touching the DB.

    _quota_manager:
        Optional QuotaManager instance injected by tests.  When ``None`` a
        fresh default instance is created.

    Returns
    -------
    int
        Always 0 (no error conditions in v0.1.0).
    """
    # --add stub — no DB access, no API calls.
    add_name: str | None = getattr(args, "add", None)
    if add_name is not None:
        print("--add lands in v0.1.1; configure providers via env vars or config.yaml for now.")
        return 0

    # Lazy import so the module loads even when the DB is absent during import.
    from src.providers.quota_manager import QuotaManager  # noqa: PLC0415

    qm = _quota_manager if _quota_manager is not None else QuotaManager()

    lines: list[str] = []
    for provider in _PROVIDERS:
        remaining = qm.remaining(provider)
        limit = qm.get_limit(provider)

        # When the DB has no row yet (fresh install, month not seeded), fall
        # back to the known free-tier defaults so the output is still useful.
        if limit == 0:
            from src.providers.quota_manager import _DEFAULT_LIMITS  # noqa: PLC0415

            limit = _DEFAULT_LIMITS.get(provider, 0)
            remaining = limit

        lines.append(f"Provider: {provider}")
        lines.append("  Status: active")
        lines.append(f"  Quota remaining: {remaining} / {limit}")
        lines.append("")  # blank separator between providers

    # Remove trailing blank line for a clean last line.
    if lines and lines[-1] == "":
        lines.pop()

    print("\n".join(lines))
    return 0
