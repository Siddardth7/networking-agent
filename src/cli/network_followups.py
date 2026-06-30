"""
src/cli/network_followups.py
Schedule timed, capped, value-add follow-ups for no-reply outreach (issue #17, A7).

A sent outreach that hasn't drawn a reply earns a follow-up touch scheduled
``followup_gap_days`` after the last touch (the 4-7 day sweet spot), capped at
``followup_max_touches`` follow-ups so the cadence stays non-spammy. Research:
2-3 touches lift reply rate 20-30%+ over a single send.

Scheduling is gated by the marketer artifact: only outreach whose company
reached APPROVED (the state the artifact write transitions to) is eligible —
nothing is scheduled for contacts that never cleared the approval loop. The cap
is enforced at schedule time, so a follow-up is *never* scheduled past it.

The pure :func:`plan_followups` decides what's due; the CLI materializes the
plans as rows in the ``followups`` table (created in 001, used here for the
first time).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.core.config import load_config
from src.core.db import get_connection, with_writer
from src.core.schemas import Outcome

__all__ = [
    "FollowupPlan",
    "plan_followups",
    "schedule_followups",
    "list_followups",
    "run_followups",
]

# A company is "gated by the marketer artifact" once the artifact write has
# transitioned it out of DRAFTED. SENT is included so a later per-company send
# state doesn't silently drop eligibility.
_GATED_STATES: frozenset[str] = frozenset({"APPROVED", "SENT"})


@dataclass(frozen=True)
class FollowupPlan:
    """A follow-up the scheduler decided to queue for one prior outreach."""

    outreach_log_id: int
    scheduled_at: datetime
    touch_number: int  # 1-based: 1 = first follow-up after the original send


def _parse_ts(value: str | None) -> datetime | None:
    """Parse a SQLite timestamp ('YYYY-MM-DD HH:MM:SS' or ISO) to a datetime.

    Returns None for a missing or unparseable value so a malformed row is
    skipped rather than crashing the whole scheduling pass.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def plan_followups(
    rows: list[dict],
    *,
    max_touches: int,
    gap_days: int,
) -> list[FollowupPlan]:
    """Decide which outreach rows are due for a follow-up. Pure — no I/O.

    Each row is a dict with: ``outreach_log_id``, ``last_touch_at`` (datetime of
    the most recent touch, or None), ``sent_followups`` (already-sent count),
    ``pending_followups`` (scheduled-but-unsent count), ``responded`` (bool),
    and ``gated`` (bool — company cleared the marketer artifact).

    A follow-up is due iff the outreach is gated, drew no reply, has an actual
    last touch, has no follow-up already queued (so re-runs don't duplicate),
    and is still under the cap. The new touch is scheduled ``gap_days`` after the
    last touch; the cap guard guarantees it never exceeds ``max_touches``.
    """
    plans: list[FollowupPlan] = []
    for r in rows:
        if not r["gated"]:
            continue
        if r["responded"]:
            continue
        if r["pending_followups"] > 0:
            continue  # an unsent follow-up is already queued — don't double-book
        if r["sent_followups"] >= max_touches:
            continue  # cap reached — never schedule past it
        last_touch = r["last_touch_at"]
        if last_touch is None:
            continue  # nothing actually sent yet — nothing to follow up on
        plans.append(
            FollowupPlan(
                outreach_log_id=r["outreach_log_id"],
                scheduled_at=last_touch + timedelta(days=gap_days),
                touch_number=r["sent_followups"] + 1,
            )
        )
    return plans


def _collect_rows() -> list[dict]:
    """Read every prior outreach with the fields :func:`plan_followups` needs."""
    conn = get_connection()
    try:
        raw = conn.execute(
            """
            SELECT
              ol.id            AS outreach_log_id,
              ol.sent_at       AS original_sent_at,
              ol.response      AS response,
              c.outcome        AS outcome,
              co.state         AS company_state,
              (SELECT COUNT(*) FROM followups f
                 WHERE f.outreach_log_id = ol.id AND f.sent_at IS NOT NULL)
                               AS sent_followups,
              (SELECT COUNT(*) FROM followups f
                 WHERE f.outreach_log_id = ol.id AND f.sent_at IS NULL)
                               AS pending_followups,
              (SELECT MAX(f.sent_at) FROM followups f
                 WHERE f.outreach_log_id = ol.id AND f.sent_at IS NOT NULL)
                               AS last_followup_sent_at
            FROM outreach_log ol
            JOIN contacts c ON c.id = ol.contact_id
            LEFT JOIN companies co ON co.id = c.company_id
            """
        ).fetchall()
    finally:
        conn.close()

    rows: list[dict] = []
    for r in raw:
        original = _parse_ts(r["original_sent_at"])
        last_fu = _parse_ts(r["last_followup_sent_at"])
        # The last touch is whichever happened later: the original send or the
        # most recent sent follow-up.
        last_touch = max((t for t in (original, last_fu) if t is not None), default=None)
        outcome = r["outcome"]
        responded = (r["response"] or "PENDING") != "PENDING" or (
            outcome not in (None, Outcome.NONE.value)
        )
        rows.append(
            {
                "outreach_log_id": r["outreach_log_id"],
                "last_touch_at": last_touch,
                "sent_followups": r["sent_followups"],
                "pending_followups": r["pending_followups"],
                "responded": responded,
                "gated": (r["company_state"] or "") in _GATED_STATES,
            }
        )
    return rows


def schedule_followups() -> int:
    """Queue every due follow-up into the ``followups`` table. Returns exit code."""
    cfg = load_config()
    plans = plan_followups(
        _collect_rows(),
        max_touches=cfg.followup_max_touches,
        gap_days=cfg.followup_gap_days,
    )
    if not plans:
        print("No follow-ups due.")
        return 0

    with with_writer() as conn:
        for p in plans:
            conn.execute(
                "INSERT INTO followups (outreach_log_id, scheduled_at) VALUES (?, ?)",
                (p.outreach_log_id, p.scheduled_at.isoformat(sep=" ", timespec="seconds")),
            )

    print(f"Scheduled {len(plans)} follow-up(s):")
    for p in plans:
        print(
            f"  outreach #{p.outreach_log_id} → follow-up #{p.touch_number} "
            f"on {p.scheduled_at:%Y-%m-%d}"
        )
    return 0


def list_followups() -> int:
    """Print scheduled follow-ups (pending first, then sent), newest schedule first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.scheduled_at, f.sent_at, c.full_name,
                   co.slug AS company_slug
            FROM followups f
            JOIN outreach_log ol ON ol.id = f.outreach_log_id
            JOIN contacts c ON c.id = ol.contact_id
            LEFT JOIN companies co ON co.id = c.company_id
            ORDER BY (f.sent_at IS NOT NULL), f.scheduled_at DESC, f.id
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No follow-ups scheduled yet.")
        return 0

    for r in rows:
        status = "SENT" if r["sent_at"] else "PENDING"
        company = r["company_slug"] or "?"
        print(
            f"[{status}] {r['full_name']} @ {company} — scheduled {r['scheduled_at']} "
            f"(id={r['id']})"
        )
    return 0


def run_followups(args: argparse.Namespace) -> int:
    """Dispatch: ``--list`` queries; otherwise schedule due follow-ups."""
    if getattr(args, "list", False):
        return list_followups()
    return schedule_followups()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Schedule capped, value-add follow-ups for no-reply outreach (#17)."
    )
    parser.add_argument(
        "--list", action="store_true", help="List scheduled follow-ups instead of scheduling"
    )
    sys.exit(run_followups(parser.parse_args()))
