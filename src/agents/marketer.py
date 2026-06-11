"""
src/agents/marketer.py
Marketer Agent: interactive approval loop for reviewing and approving contact drafts.
Traceability: DESIGN.md §7, §8.13
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field

from src.core.db import get_connection, with_writer

__all__ = ["ApprovalResult", "run_approval_loop"]


def _format_critic_for_reviewer(trace_json: str | None) -> str | None:
    """Render a critic_trace JSON as one or two compact lines for the marketer
    approval loop. Returns None when the trace is missing / unparseable / empty.

    The reviewer's decision on REVISE vs APPROVE vs SKIP depends on knowing
    *why* a draft was held, not just *that* it was held.
    """
    if not trace_json:
        return None
    try:
        trace = json.loads(trace_json)
    except (json.JSONDecodeError, TypeError):
        return None
    scores: dict = trace.get("scores") or {}
    issues: list = trace.get("issues") or []
    reason = trace.get("reason")
    if not scores and not issues and not reason:
        return None
    parts: list[str] = []
    if reason:
        parts.append(f"  Held because: {reason}")
    if scores:
        parts.append("  Critic scores: " + " ".join(f"{dim}={val}" for dim, val in scores.items()))
    if issues:
        parts.append("  Critic issues:")
        for issue in issues:
            parts.append(f"    - {issue}")
    return "\n".join(parts)


@dataclass
class ApprovalResult:
    approved_contact_ids: list[int] = field(default_factory=list)
    skipped_contact_ids: list[int] = field(default_factory=list)
    outreach_log_ids: list[int] = field(default_factory=list)
    quit_early: bool = False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_company(company_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, slug, name, state FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_contacts_with_drafts(company_id: int) -> list[dict]:
    """Return contacts in DRAFTED state with their latest drafts per channel."""
    conn = get_connection()
    try:
        contacts = conn.execute(
            "SELECT id, full_name, title, persona, focus_area, linkedin_url, "
            "email, email_verified, hook, shared_signals, state "
            "FROM contacts WHERE company_id = ? AND state = 'DRAFTED' ORDER BY id",
            (company_id,),
        ).fetchall()
        result = []
        for c in contacts:
            cd = dict(c)
            # Latest draft per channel (highest version)
            drafts = conn.execute(
                """
                SELECT id, channel, body, subject, version, quality_flag,
                       quality_code, critic_trace, approved
                FROM drafts
                WHERE contact_id = ?
                ORDER BY channel, version DESC
                """,
                (cd["id"],),
            ).fetchall()
            # Keep only the latest version per channel
            seen_channels: set[str] = set()
            latest_drafts = []
            for d in drafts:
                dd = dict(d)
                if dd["channel"] not in seen_channels:
                    seen_channels.add(dd["channel"])
                    latest_drafts.append(dd)
            cd["drafts"] = latest_drafts
            result.append(cd)
        return result
    finally:
        conn.close()


def _approve_drafts(contact_id: int, draft_ids: list[int]) -> list[int]:
    """Mark drafts approved=1 and write outreach_log rows. Returns new log ids."""
    log_ids = []
    with with_writer() as conn:
        for draft_id in draft_ids:
            row = conn.execute(
                "SELECT channel FROM drafts WHERE id = ? AND contact_id = ?",
                (draft_id, contact_id),
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                "UPDATE drafts SET approved = 1 WHERE id = ?",
                (draft_id,),
            )
            cursor = conn.execute(
                "INSERT INTO outreach_log (contact_id, draft_id, channel, sent_at) "
                "VALUES (?, ?, ?, NULL)",
                (contact_id, draft_id, row["channel"]),
            )
            log_ids.append(cursor.lastrowid)
        conn.execute(
            "UPDATE contacts SET state = 'APPROVED' WHERE id = ?",
            (contact_id,),
        )
    return log_ids


# Quality codes that block approval without --force override. HARD_FAIL
# means the deterministic guardrails refused; CRITIC_HOLD means the Sonnet
# critic refused. Both must clear before unattended send.
_BLOCKING_QUALITY_CODES: set[str] = {"HARD_FAIL", "CRITIC_HOLD"}


def _contact_has_hard_fail(contact: dict) -> bool:
    """True if any of *contact*'s latest drafts is in a BLOCKING quality state.

    Despite the legacy name, this now also catches CRITIC_HOLD — both
    states require --force to approve. Drafts predating migration 002
    have NULL quality_code which sqlite surfaces here as the column
    default ('OK'); they are treated as OK.
    """
    for d in contact.get("drafts", []):
        if (d.get("quality_code") or "OK") in _BLOCKING_QUALITY_CODES:
            return True
    return False


def _mark_company_approved(company_id: int) -> None:
    with with_writer() as conn:
        conn.execute(
            "UPDATE companies SET state = 'APPROVED' WHERE id = ?",
            (company_id,),
        )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _char_word_count(text: str) -> str:
    chars = len(text)
    words = len(text.split()) if text.strip() else 0
    return f"{chars} chars / {words} words"


def _render_contact_block(contact: dict, index: int) -> str:
    lines = []
    flagged_drafts = [d for d in contact["drafts"] if d.get("quality_flag")]
    hard_fails = [
        d for d in contact["drafts"] if (d.get("quality_code") or "OK") in _BLOCKING_QUALITY_CODES
    ]
    n_flagged = len(flagged_drafts)
    n_hard = len(hard_fails)

    lines.append(f"\n{'=' * 60}")
    lines.append(f"Contact [{index}] — {contact['full_name']}")
    lines.append(f"{'=' * 60}")

    if n_hard > 0:
        lines.append(
            f"  ⛔ {n_hard} draft{'s' if n_hard > 1 else ''} HARD_FAIL "
            f"— approval blocked (use --force to override)."
        )
    elif n_flagged > 0:
        lines.append(
            f"  ⚠️  {n_flagged} draft{'s' if n_flagged > 1 else ''} flagged for quality "
            f"— review highlighted blocks carefully."
        )

    lines.append(f"  Persona:    {contact.get('persona') or 'N/A'}")
    lines.append(f"  Focus:      {contact.get('focus_area') or 'N/A'}")
    lines.append(f"  LinkedIn:   {contact.get('linkedin_url') or 'N/A'}")
    email_str = contact.get("email") or "N/A"
    verified_str = "✓ verified" if contact.get("email_verified") else "unverified"
    lines.append(f"  Email:      {email_str} ({verified_str})")
    lines.append(f"  Hook:       {contact.get('hook') or 'N/A'}")
    if contact.get("shared_signals"):
        lines.append(f"  Signals:    {contact['shared_signals']}")

    lines.append("")
    for draft in contact["drafts"]:
        channel = draft["channel"]
        body = draft["body"] or ""
        subject = draft.get("subject") or ""
        version = draft.get("version", 1)
        qcode = draft.get("quality_code") or "OK"
        qflag = draft.get("quality_flag", False)
        if qcode == "HARD_FAIL":
            flag_str = "  ⛔ HARD_FAIL"
        elif qcode == "CRITIC_HOLD":
            flag_str = "  ⛔ CRITIC_HOLD"
        elif qcode == "SOFT_FLAG" or qflag:
            flag_str = "  ⚠️  QUALITY FLAG"
        else:
            flag_str = ""

        lines.append(f"  ── {channel} (v{version}){flag_str} ──")
        if subject:
            lines.append(f"  Subject: {subject}")
        lines.append(f"  {_char_word_count(body)}")
        critic_block = _format_critic_for_reviewer(draft.get("critic_trace"))
        if critic_block:
            for cb_line in critic_block.splitlines():
                lines.append(cb_line)
        lines.append("  " + "-" * 40)
        for line in body.splitlines():
            lines.append(f"    {line}")
        lines.append("")

    return "\n".join(lines)


def _render_all_contacts(contacts: list[dict]) -> None:
    for i, contact in enumerate(contacts, start=1):
        print(_render_contact_block(contact, i))


def _print_help() -> None:
    print("\nCommands:")
    print("  APPROVE <id>          — approve a specific contact (uses all channel drafts)")
    print("  APPROVE <id> --force  — approve even if drafts are HARD_FAIL (manual override)")
    print("  APPROVE all           — approve all remaining contacts (HARD_FAIL blocked)")
    print("  APPROVE all --force   — approve all incl. HARD_FAIL drafts (use with care)")
    print('  REVISE <id> <channel> "<feedback>" — request a revision')
    print("  SKIP <id>             — skip this contact")
    print("  SHOW <id> raw         — show raw draft text for a contact")
    print("  quit / q              — exit the approval loop\n")


# ---------------------------------------------------------------------------
# Verb parsing
# ---------------------------------------------------------------------------

_APPROVE_ALL_RE = re.compile(r"^approve\s+all(\s+--force)?\s*$", re.IGNORECASE)
_APPROVE_ID_RE = re.compile(r"^approve\s+(\d+)(\s+--force)?\s*$", re.IGNORECASE)
_REVISE_RE = re.compile(
    r'^revise\s+(\d+)\s+(\S+)\s+"([^"]*)"',
    re.IGNORECASE,
)
_SKIP_RE = re.compile(r"^skip\s+(\d+)\s*$", re.IGNORECASE)
_SHOW_RE = re.compile(r"^show\s+(\d+)\s+raw\s*$", re.IGNORECASE)


def parse_verb(line: str) -> tuple | None:
    """Parse a user command line into a (verb, ...) tuple.

    Returns one of:
      ("APPROVE_ALL", force: bool)
      ("APPROVE", contact_index: int, force: bool)
      ("REVISE", contact_index: int, channel: str, feedback: str)
      ("SKIP", contact_index: int)
      ("SHOW", contact_index: int)
      ("QUIT",)
      None  — unrecognized input

    --force suffix on APPROVE / APPROVE all overrides the HARD_FAIL gate
    (used after explicit human review of a flagged draft).
    """
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.lower() in ("quit", "q", "exit"):
        return ("QUIT",)
    m = _APPROVE_ALL_RE.match(stripped)
    if m:
        return ("APPROVE_ALL", bool(m.group(1)))
    m = _APPROVE_ID_RE.match(stripped)
    if m:
        return ("APPROVE", int(m.group(1)), bool(m.group(2)))
    m = _REVISE_RE.match(stripped)
    if m:
        return ("REVISE", int(m.group(1)), m.group(2).upper(), m.group(3))
    m = _SKIP_RE.match(stripped)
    if m:
        return ("SKIP", int(m.group(1)))
    m = _SHOW_RE.match(stripped)
    if m:
        return ("SHOW", int(m.group(1)))
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_approval_loop(
    company_id: int,
    _input_fn=None,
    _dispatch_fn=None,
) -> ApprovalResult:
    """Interactive approval loop for reviewing and approving drafted contacts.

    Parameters
    ----------
    company_id:
        DB id of the company being reviewed.
    _input_fn:
        Callable for reading user input (default: input()). Injected in tests.
    _dispatch_fn:
        Callable for dispatching revisions (default: dispatch_revision from dispatch.py).
        Injected in tests.
    """
    if _input_fn is None:
        _input_fn = input
    # _dispatch_fn resolved lazily at first REVISE call to avoid circular import
    # and to allow tests to run without dispatch module installed

    company = _load_company(company_id)
    if company is None:
        print(f"Error: company id={company_id} not found.", file=sys.stderr)
        return ApprovalResult(quit_early=True)

    result = ApprovalResult()
    approved_indices: set[int] = set()
    skipped_indices: set[int] = set()

    print(f"\n{'#' * 60}")
    print(f"  Approval loop — {company['name']} ({company['slug']})")
    print(f"{'#' * 60}")
    _print_help()

    while True:
        # Re-load contacts so revisions are reflected
        contacts = _load_contacts_with_drafts(company_id)

        # Filter out already approved/skipped by index
        pending = [
            (i + 1, c)
            for i, c in enumerate(contacts)
            if (i + 1) not in approved_indices and (i + 1) not in skipped_indices
        ]

        if not pending:
            print("\nAll contacts processed. Finalizing...")
            break

        # Render pending contacts
        for idx, contact in pending:
            print(_render_contact_block(contact, idx))

        try:
            raw = _input_fn("\nEnter command (or 'help'): ").strip()
        except EOFError:
            result.quit_early = True
            break

        if raw.lower() == "help":
            _print_help()
            continue

        verb = parse_verb(raw)
        if verb is None:
            print(f"  Unrecognized command: '{raw}'. Type 'help' for options.")
            continue

        if verb[0] == "QUIT":
            result.quit_early = True
            break

        if verb[0] == "APPROVE_ALL":
            force = verb[1] if len(verb) > 1 else False
            blocked_any = False
            for idx, contact in pending:
                hard = _contact_has_hard_fail(contact)
                if hard and not force:
                    print(
                        f"  ⛔ Contact [{idx}] {contact['full_name']} has HARD_FAIL "
                        f"draft(s) — refusing to approve. Use 'APPROVE {idx} --force' "
                        f"to override after manual review."
                    )
                    blocked_any = True
                    continue
                if hard and force:
                    print(
                        f"  ⚠️  --force override: approving HARD_FAIL draft(s) for "
                        f"contact [{idx}] {contact['full_name']}. "
                        f"You have manually accepted responsibility for this content."
                    )
                draft_ids = [d["id"] for d in contact["drafts"]]
                log_ids = _approve_drafts(contact["id"], draft_ids)
                result.approved_contact_ids.append(contact["id"])
                result.outreach_log_ids.extend(log_ids)
                approved_indices.add(idx)
                print(f"  ✓ Contact [{idx}] {contact['full_name']} approved.")
            if blocked_any:
                # Keep the loop alive so the user can act on blocked items
                # (REVISE / SKIP / APPROVE <id> --force).
                continue
            break

        if verb[0] == "APPROVE":
            target_idx = verb[1]
            force = verb[2] if len(verb) > 2 else False
            match = next(((idx, c) for idx, c in pending if idx == target_idx), None)
            if match is None:
                print(f"  Contact [{target_idx}] not found in pending list.")
                continue
            idx, contact = match
            hard = _contact_has_hard_fail(contact)
            if hard and not force:
                print(
                    f"  ⛔ Contact [{idx}] has HARD_FAIL draft(s) — refusing to "
                    f"approve. Re-run as 'APPROVE {idx} --force' after manual review."
                )
                continue
            if hard and force:
                print(
                    "  ⚠️  --force override: approving HARD_FAIL draft(s). "
                    "You have manually accepted responsibility for this content."
                )
            draft_ids = [d["id"] for d in contact["drafts"]]
            log_ids = _approve_drafts(contact["id"], draft_ids)
            result.approved_contact_ids.append(contact["id"])
            result.outreach_log_ids.extend(log_ids)
            approved_indices.add(idx)
            print(f"  ✓ Contact [{idx}] {contact['full_name']} approved.")
            continue

        if verb[0] == "SKIP":
            target_idx = verb[1]
            match = next(((idx, c) for idx, c in pending if idx == target_idx), None)
            if match is None:
                print(f"  Contact [{target_idx}] not found in pending list.")
                continue
            idx, contact = match
            skipped_indices.add(idx)
            result.skipped_contact_ids.append(contact["id"])
            print(f"  Skipped contact [{idx}] {contact['full_name']}.")
            continue

        if verb[0] == "SHOW":
            target_idx = verb[1]
            match = next(((idx, c) for idx, c in pending if idx == target_idx), None)
            if match is None:
                print(f"  Contact [{target_idx}] not found in pending list.")
                continue
            _, contact = match
            print(f"\n── RAW DRAFTS for {contact['full_name']} ──")
            for draft in contact["drafts"]:
                print(f"\n[{draft['channel']}] v{draft['version']}:")
                if draft.get("subject"):
                    print(f"Subject: {draft['subject']}")
                print(draft.get("body") or "")
            continue

        if verb[0] == "REVISE":
            _, target_idx, channel_str, feedback = verb
            match = next(((idx, c) for idx, c in pending if idx == target_idx), None)
            if match is None:
                print(f"  Contact [{target_idx}] not found in pending list.")
                continue
            idx, contact = match

            # Find the current draft for this channel
            draft = next(
                (d for d in contact["drafts"] if d["channel"] == channel_str),
                None,
            )
            if draft is None:
                print(f"  No draft found for channel '{channel_str}' on contact [{idx}].")
                continue

            from src.core.schemas import Channel, DraftDispatchRequest

            try:
                channel_enum = Channel(channel_str)
            except ValueError:
                print(f"  Unknown channel: '{channel_str}'. Valid: {[c.value for c in Channel]}")
                continue

            req = DraftDispatchRequest(
                contact_id=contact["id"],
                channel=channel_enum,
                prior_draft_id=draft["id"],
                feedback=feedback,
            )

            # Resolve dispatch lazily (allows tests without dispatch module)
            if _dispatch_fn is None:
                from src.agents.dispatch import dispatch_revision as _dr

                _resolved = _dr
            else:
                _resolved = _dispatch_fn

            print(f"  Regenerating {channel_str} draft for {contact['full_name']}...")
            resp = _resolved(req)

            if resp.status == "OK":
                print(f"  ✓ New version v{resp.new_version} ready.")
            elif resp.status == "GUARDRAIL_FLAGGED":
                print(f"  ⚠️  Revision flagged by quality guardrail (v{resp.new_version} saved).")
            else:
                print(f"  Revision failed: {resp.error_message}. Original draft retained.")
            continue

    # Mark company APPROVED if any contacts were approved
    if result.approved_contact_ids:
        _mark_company_approved(company_id)

    return result
