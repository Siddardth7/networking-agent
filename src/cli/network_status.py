"""
src/cli/network_status.py — Pipeline status view for networking-agent.

Traceability: DESIGN.md §3

Behaviour
---------
- No args:   per-company table (slug, state, contact count, draft count,
             outreach_log count) + provider quotas remaining.
- <slug>:    detailed view of that company: each contact with name, state,
             drafts per channel, outreach_log entries.
- --update <log-id> --response <VALUE> [--notes "..."]:
             update outreach_log row; valid response values are
             PENDING / NO_RESPONSE / POSITIVE / NEGATIVE / IRRELEVANT.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.core.db import get_connection, with_writer, init_db

__all__ = ["run_status"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RESPONSES = {"PENDING", "NO_RESPONSE", "POSITIVE", "NEGATIVE", "IRRELEVANT"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_row(cols: list[str], widths: list[int]) -> str:
    """Format one table row with fixed column widths."""
    parts = [str(c).ljust(w) for c, w in zip(cols, widths)]
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Summary view (no args)
# ---------------------------------------------------------------------------

def _summary_view() -> int:
    """Print per-company table + quota lines. Returns exit code."""
    conn = get_connection()
    try:
        companies = conn.execute(
            "SELECT id, slug, name, state FROM companies ORDER BY slug"
        ).fetchall()

        if not companies:
            print("No companies found. Run /network-find <slug> to get started.")
        else:
            header = ["SLUG", "STATE", "CONTACTS", "DRAFTS", "OUTREACH"]
            widths = [24, 12, 10, 8, 10]
            print(_fmt_row(header, widths))
            print(_fmt_row(["-" * w for w in widths], widths))

            for co in companies:
                co_id = co["id"]
                contact_count = conn.execute(
                    "SELECT COUNT(*) FROM contacts WHERE company_id = ?", (co_id,)
                ).fetchone()[0]
                draft_count = conn.execute(
                    """SELECT COUNT(*) FROM drafts d
                       JOIN contacts c ON d.contact_id = c.id
                       WHERE c.company_id = ?""",
                    (co_id,),
                ).fetchone()[0]
                log_count = conn.execute(
                    """SELECT COUNT(*) FROM outreach_log ol
                       JOIN contacts c ON ol.contact_id = c.id
                       WHERE c.company_id = ?""",
                    (co_id,),
                ).fetchone()[0]

                print(
                    _fmt_row(
                        [co["slug"], co["state"], contact_count, draft_count, log_count],
                        widths,
                    )
                )

        # Quota section
        print()
        quotas = conn.execute(
            "SELECT provider, used, limit_val, month_key FROM quota ORDER BY provider, month_key DESC"
        ).fetchall()

        seen: set[str] = set()
        printed_header = False
        for q in quotas:
            p = q["provider"]
            if p in seen:
                continue
            seen.add(p)
            if not printed_header:
                print("Provider quotas (current month):")
                printed_header = True
            remaining = max(0, q["limit_val"] - q["used"])
            print(
                f"  {p:<12}  {remaining} / {q['limit_val']} remaining  ({q['month_key']})"
            )

        if not printed_header:
            print("Provider quotas: no quota data recorded yet.")

    finally:
        conn.close()

    return 0


# ---------------------------------------------------------------------------
# Detailed company view
# ---------------------------------------------------------------------------

def _company_view(slug: str) -> int:
    """Print detailed view for one company. Returns exit code."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, slug, name, state FROM companies WHERE slug = ?", (slug,)
        ).fetchone()

        if row is None:
            print(f"Company not found: {slug!r}")
            return 1

        print(f"Company: {row['name'] or slug}  [slug={row['slug']}  state={row['state']}]")
        print()

        contacts = conn.execute(
            "SELECT id, full_name, title, state FROM contacts WHERE company_id = ? ORDER BY full_name",
            (row["id"],),
        ).fetchall()

        if not contacts:
            print("  No contacts found.")
            return 0

        for ct in contacts:
            ct_id = ct["id"]
            print(
                f"  {ct['full_name']}  |  {ct['title'] or '—'}  |  state={ct['state']}"
            )

            # Drafts per channel
            drafts = conn.execute(
                "SELECT channel, version, quality_flag, approved FROM drafts WHERE contact_id = ? ORDER BY channel, version",
                (ct_id,),
            ).fetchall()
            if drafts:
                for d in drafts:
                    approved = "approved" if d["approved"] else "pending"
                    print(
                        f"    draft  channel={d['channel']}  v{d['version']}  quality={d['quality_flag'] or '—'}  [{approved}]"
                    )
            else:
                print("    (no drafts)")

            # Outreach log entries
            logs = conn.execute(
                """SELECT ol.id, ol.channel, ol.sent_at, ol.response, ol.notes, ol.draft_id
                   FROM outreach_log ol
                   WHERE ol.contact_id = ?
                   ORDER BY ol.sent_at""",
                (ct_id,),
            ).fetchall()
            if logs:
                for lg in logs:
                    notes_str = f"  notes={lg['notes']!r}" if lg["notes"] else ""
                    print(
                        f"    log[{lg['id']}]  channel={lg['channel']}  sent={lg['sent_at'] or '—'}"
                        f"  response={lg['response'] or '—'}{notes_str}"
                    )
            else:
                print("    (no outreach log entries)")

            print()

    finally:
        conn.close()

    return 0


# ---------------------------------------------------------------------------
# Update outreach_log
# ---------------------------------------------------------------------------

def _update_log(log_id: int, response: str, notes: Optional[str]) -> int:
    """Update outreach_log row. Returns exit code."""
    resp_upper = response.upper()
    if resp_upper not in VALID_RESPONSES:
        valid = ", ".join(sorted(VALID_RESPONSES))
        print(f"Invalid response value: {response!r}. Must be one of: {valid}")
        return 1

    with with_writer() as conn:
        row = conn.execute(
            "SELECT id FROM outreach_log WHERE id = ?", (log_id,)
        ).fetchone()
        if row is None:
            print(f"Outreach log entry not found: id={log_id}")
            return 1

        conn.execute(
            "UPDATE outreach_log SET response = ?, notes = ? WHERE id = ?",
            (resp_upper, notes, log_id),
        )

    print(f"Updated outreach_log id={log_id}: response={resp_upper}" + (f", notes={notes!r}" if notes else ""))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_status(args: argparse.Namespace) -> int:
    """Main entry point.

    Parameters
    ----------
    args:
        args.company  — Optional[str]  company slug for detailed view
        args.update   — Optional[int]  outreach_log id to update
        args.response — Optional[str]  new response value
        args.notes    — Optional[str]  optional notes string
    """
    # --update mode
    if getattr(args, "update", None) is not None:
        response = getattr(args, "response", None)
        if not response:
            print("--response is required when using --update.")
            return 1
        notes = getattr(args, "notes", None)
        return _update_log(args.update, response, notes)

    # Detailed company view
    company = getattr(args, "company", None)
    if company:
        return _company_view(company)

    # Summary view
    return _summary_view()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description="Show networking-agent pipeline status.")
    parser.add_argument("company", nargs="?", default=None, help="Company slug for detailed view")
    parser.add_argument("--update", type=int, default=None, help="Outreach log ID to update")
    parser.add_argument("--response", default=None, help="New response value")
    parser.add_argument("--notes", default=None, help="Optional notes")
    sys.exit(run_status(parser.parse_args()))
