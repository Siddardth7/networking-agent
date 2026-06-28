"""
src/cli/network_outcome.py
Record and query per-contact outreach outcomes (issue #15, A6).

The outcome is the relationship-level feedback signal — replied / yielded a
point of contact / gave a sponsorship answer — distinct from the per-message
``outreach_log.response``. It's persisted on the contact and queryable, and is
the data that later tunes the referral-ranking weights (#12).
"""

from __future__ import annotations

import argparse
import sys

from src.core.db import get_connection, with_writer
from src.core.schemas import Outcome

__all__ = [
    "set_contact_outcome",
    "list_outcomes",
    "report_outcomes",
    "aggregate_outcomes",
    "run_outcome",
    "VALID_OUTCOMES",
]

VALID_OUTCOMES: set[str] = {o.value for o in Outcome}


def set_contact_outcome(contact_id: int, outcome: str, notes: str | None = None) -> int:
    """Record *outcome* (+ optional *notes*) for a contact. Returns an exit code.

    Validates the outcome against the :class:`~src.core.schemas.Outcome` enum,
    confirms the contact exists, then stamps ``outcome``/``outcome_notes`` and
    ``outcome_at = CURRENT_TIMESTAMP``. Returns 1 (and prints why) on an invalid
    outcome or unknown contact, else 0.
    """
    outcome_upper = outcome.upper()
    if outcome_upper not in VALID_OUTCOMES:
        valid = ", ".join(sorted(VALID_OUTCOMES))
        print(f"Invalid outcome: {outcome!r}. Must be one of: {valid}")
        return 1

    with with_writer() as conn:
        row = conn.execute(
            "SELECT full_name FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        if row is None:
            print(f"Contact not found: id={contact_id}")
            return 1
        conn.execute(
            "UPDATE contacts SET outcome = ?, outcome_notes = ?, "
            "outcome_at = CURRENT_TIMESTAMP WHERE id = ?",
            (outcome_upper, notes, contact_id),
        )

    print(f"Recorded outcome {outcome_upper} for {row['full_name']} (id={contact_id}).")
    return 0


def list_outcomes() -> int:
    """Print every contact with a recorded outcome (most recent first)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.full_name, c.outcome, c.outcome_notes, c.outcome_at,
                   co.slug AS company_slug
            FROM contacts c
            LEFT JOIN companies co ON co.id = c.company_id
            WHERE c.outcome IS NOT NULL AND c.outcome != 'NONE'
            ORDER BY c.outcome_at DESC, c.id
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No outcomes recorded yet.")
        return 0

    for r in rows:
        notes = f" — {r['outcome_notes']}" if r["outcome_notes"] else ""
        company = r["company_slug"] or "?"
        print(f"[{r['outcome']}] {r['full_name']} @ {company} (id={r['id']}){notes}")
    return 0


def aggregate_outcomes(rows: list[tuple[str | None, str | None]]) -> dict:
    """Aggregate ``(company_slug, outcome)`` rows into a report summary. Pure.

    Returns ``{"total", "responded", "by_outcome", "by_company"}`` where
    ``responded`` counts contacts with any outcome other than ``NONE``,
    ``by_outcome`` maps each non-NONE outcome to its count, and ``by_company``
    maps each slug to ``{"total", "responded"}``.
    """
    by_outcome: dict[str, int] = {}
    by_company: dict[str, dict[str, int]] = {}
    total = responded = 0
    for slug, outcome in rows:
        total += 1
        company = slug or "?"
        bucket = by_company.setdefault(company, {"total": 0, "responded": 0})
        bucket["total"] += 1
        if outcome and outcome != Outcome.NONE.value:
            responded += 1
            bucket["responded"] += 1
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
    return {
        "total": total,
        "responded": responded,
        "by_outcome": by_outcome,
        "by_company": by_company,
    }


def report_outcomes() -> int:
    """Print an outcomes rollup — overall response rate + per-company breakdown."""
    conn = get_connection()
    try:
        rows = [
            (r["company_slug"], r["outcome"])
            for r in conn.execute(
                """
                SELECT co.slug AS company_slug, c.outcome
                FROM contacts c
                LEFT JOIN companies co ON co.id = c.company_id
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    summary = aggregate_outcomes(rows)
    if summary["total"] == 0:
        print("No contacts yet.")
        return 0

    rate = 100 * summary["responded"] / summary["total"]
    print(
        f"Outcome report — {summary['responded']}/{summary['total']} contacts "
        f"responded ({rate:.0f}%)"
    )
    if summary["by_outcome"]:
        breakdown = ", ".join(f"{k} {v}" for k, v in sorted(summary["by_outcome"].items()))
        print(f"By outcome: {breakdown}")
    print("By company:")
    for slug, b in sorted(summary["by_company"].items()):
        print(f"  {slug}: {b['responded']}/{b['total']} responded")
    return 0


def run_outcome(args: argparse.Namespace) -> int:
    """Dispatch: ``--report`` rolls up, ``--list`` queries, else record an outcome."""
    if getattr(args, "report", False):
        return report_outcomes()
    if getattr(args, "list", False):
        return list_outcomes()
    if args.contact_id is None or args.outcome is None:
        print("Provide <contact_id> and <outcome>, or use --list.")
        return 1
    return set_contact_outcome(args.contact_id, args.outcome, getattr(args, "notes", None))


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Record/query per-contact outreach outcomes (#15)."
    )
    parser.add_argument("contact_id", nargs="?", type=int, default=None, help="Contact DB id")
    parser.add_argument(
        "outcome",
        nargs="?",
        default=None,
        help="One of: " + ", ".join(sorted(VALID_OUTCOMES)),
    )
    parser.add_argument("--notes", default=None, help="Optional free-text notes")
    parser.add_argument("--list", action="store_true", help="List all recorded outcomes")
    parser.add_argument("--report", action="store_true", help="Rollup report per company")
    sys.exit(run_outcome(parser.parse_args()))
