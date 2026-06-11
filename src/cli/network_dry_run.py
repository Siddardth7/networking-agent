"""
src/cli/network_dry_run.py — Dry-run preview for the Finder pipeline.

Traceability: DESIGN.md §3 (Dry-run output format)

Prints what the Finder WOULD query against Serper and how many Hunter calls
it would make, WITHOUT making any real API calls.  Surfaces estimated quota
burn so users can decide whether to proceed.

Run standalone:
    python -m src.cli.network_dry_run --company lockheed-martin --limit 5
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["run_dry_run"]

# Role keywords — must stay in sync with src/agents/finder.py _ROLE_KEYWORDS
_DRY_RUN_KEYWORDS = [
    "quality",
    "structures",
    "composites",
    "manufacturing",
    "materials",
    "additive",
]


def _build_serper_query(company_slug: str) -> str:
    """Return the Serper LinkedIn search query string for *company_slug*."""
    kw_expr = " OR ".join(_DRY_RUN_KEYWORDS)
    return f'site:linkedin.com/in "{company_slug}" ({kw_expr})'


def run_dry_run(
    args: argparse.Namespace,
    _quota_manager=None,
) -> int:
    """Print the dry-run plan for *args.company* and return an exit code.

    Parameters
    ----------
    args:
        Parsed CLI arguments.  Must have ``company: str`` and ``limit: int``.
    _quota_manager:
        Optional injected :class:`~src.providers.quota_manager.QuotaManager`
        instance.  When ``None`` the function attempts to load one from the
        real DB; if that also fails, quota values are shown as ``"N/A"``.

    Returns
    -------
    int
        0 on success, 1 on error (e.g. missing ``company`` argument).
    """
    company: str | None = getattr(args, "company", None)
    if not company:
        print("Error: --company is required", file=sys.stderr)
        return 1

    limit: int = getattr(args, "limit", 5)

    query = _build_serper_query(company)

    # Resolve quota manager — injected in tests, loaded from DB in production
    qm = _quota_manager
    if qm is None:
        try:
            from src.providers.quota_manager import QuotaManager  # noqa: PLC0415

            qm = QuotaManager()
        except Exception:
            qm = None

    # Read remaining quota (read-only; never calls can_query)
    def _remaining(provider: str) -> str:
        if qm is None:
            return "N/A"
        try:
            return str(qm.remaining(provider))
        except Exception:
            return "N/A"

    serper_remaining = _remaining("serper")
    hunter_remaining = _remaining("hunter")

    lines = [
        f"Dry-run for: {company}",
        f"Planned Serper query: {query}",
        f"Estimated API calls: {limit} Serper + {limit} Hunter",
        f"Quota remaining — Serper: {serper_remaining}, Hunter: {hunter_remaining}",
    ]

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Preview what network-find would query without making API calls."
    )
    parser.add_argument(
        "--company",
        required=True,
        help="Company slug (lowercase, hyphenated, e.g. lockheed-martin)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum contacts to retrieve (default: 5)",
    )
    parsed = parser.parse_args()
    sys.exit(run_dry_run(parsed))
