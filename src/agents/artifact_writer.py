"""
src/agents/artifact_writer.py
Writes a human-readable Markdown artifact for a company's approved contacts and
their final drafted messages, then transitions the company state DRAFTED → APPROVED.

Traceability: PLAN.md Step 7.3
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from src.core.db import get_connection, with_writer
from src.core.schemas import Channel

__all__ = ["write_artifact"]


def _format_critic_trace(trace_json: str | None) -> str | None:
    """Render a critic_trace JSON blob as a compact markdown summary.

    Returns None when there's nothing useful to show (no trace, parse
    error, or the verdict was OK with no scores).  The output is two
    lines: per-dimension scores and a bullet list of issues.
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

    lines: list[str] = []
    if reason:
        # The one-line explanation of WHY the draft was held — present for
        # every HARD_FAIL (hard_check reason) and CRITIC_HOLD (critic
        # summary) draft. AUDIT-A9.
        lines.append(f"**Held because:** {reason}")
    if scores:
        score_str = " ".join(f"{dim}={val}" for dim, val in scores.items())
        lines.append(f"**Critic scores:** {score_str}")
    if issues:
        lines.append("**Critic issues:**")
        for issue in issues:
            lines.append(f"  - {issue}")
    return "\n".join(lines)


# Default output directory (overridable via _output_dir for tests)
_DEFAULT_OUTPUT_DIR: Path = Path.home() / ".networking-agent" / "drafts"

# Display-friendly channel labels
_CHANNEL_LABELS: dict[str, str] = {
    Channel.LINKEDIN_CONNECTION.value: "LinkedIn Connection Request",
    Channel.LINKEDIN_POST_CONNECTION.value: "LinkedIn Post-Connection Message",
    Channel.COLD_EMAIL.value: "Cold Email",
}


def _load_company(company_id: int) -> dict | None:
    """Return the companies row for *company_id*, or None if not found."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, slug, name, domain, state FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _load_approved_contacts(company_id: int) -> list[dict]:
    """Return all contacts for *company_id* whose state is DRAFTED or APPROVED.

    'Approved' in context means the contacts were selected and drafted; we
    include contacts in DRAFTED state (the post-drafter state) as well as any
    already marked APPROVED.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, full_name, title, persona, focus_area, "
            "linkedin_url, email, hook, shared_signals "
            "FROM contacts "
            "WHERE company_id = ? AND state IN ('DRAFTED', 'APPROVED', 'SELECTED') "
            "ORDER BY id",
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_latest_drafts_for_contact(contact_id: int) -> dict[str, dict]:
    """Return the latest (highest version) draft per channel for *contact_id*.

    Returns a dict keyed by channel value, e.g.:
        {'LINKEDIN_CONNECTION': {id, channel, body, subject, version,
                                  quality_flag, quality_code}, ...}
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, channel, body, subject, version, quality_flag,
                   quality_code, critic_trace
            FROM drafts
            WHERE contact_id = ?
            ORDER BY channel, version DESC
            """,
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()

    # Keep only the highest version per channel
    latest: dict[str, dict] = {}
    for row in rows:
        channel = row["channel"]
        if channel not in latest:
            latest[channel] = dict(row)
    return latest


def _render_artifact(
    company: dict,
    contacts: list[dict],
    drafts_by_contact: dict[int, dict[str, dict]],
    run_date: str,
) -> str:
    """Render the full Markdown artifact string."""
    lines: list[str] = []

    # --- Company header ---
    lines.append(f"# {company['name']}")
    lines.append(f"**Slug:** `{company['slug']}`  ")
    lines.append(f"**Run date:** {run_date}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not contacts:
        lines.append("_No approved contacts found for this company._")
        return "\n".join(lines)

    for contact in contacts:
        cid = contact["id"]
        lines.append(f"## {contact['full_name']}")
        # Persona + focus_area surface the classifier verdict so the
        # reviewer can diagnose misclassification at a glance
        # (root-cause audit §2.8 — artifact was QA-blind without this).
        if contact.get("title"):
            lines.append(f"**Title:** {contact['title']}  ")
        if contact.get("persona"):
            lines.append(f"**Persona:** {contact['persona']}  ")
        if contact.get("focus_area"):
            lines.append(f"**Focus area:** {contact['focus_area']}  ")
        if contact.get("linkedin_url"):
            lines.append(f"**LinkedIn:** {contact['linkedin_url']}  ")
        if contact.get("email"):
            lines.append(f"**Email:** {contact['email']}  ")
        if contact.get("hook"):
            lines.append(f"**Hook:** {contact['hook']}  ")
        if contact.get("shared_signals"):
            lines.append(f"**Shared signals:** {contact['shared_signals']}  ")
        lines.append("")

        contact_drafts = drafts_by_contact.get(cid, {})

        # Emit all 3 channels in canonical order
        for channel_enum in Channel:
            channel_val = channel_enum.value
            label = _CHANNEL_LABELS.get(channel_val, channel_val)
            lines.append(f"### {label}")

            draft = contact_drafts.get(channel_val)
            if draft is None:
                lines.append("_No draft found for this channel._")
                lines.append("")
                continue

            quality_code = draft.get("quality_code") or "OK"
            # Quality code badge — OK is silent, anything else is loud
            # so the reviewer cannot miss a blocked or flagged draft.
            badge = {
                "OK": "",
                "SOFT_FLAG": " ⚠️ SOFT_FLAG",
                "HARD_FAIL": " ⛔ HARD_FAIL",
                "CRITIC_HOLD": " ⛔ CRITIC_HOLD",
            }.get(quality_code, f" ⚠️ {quality_code}")
            lines.append(f"_Version {draft['version']}_{badge}")
            lines.append("")

            # Surface critic reasons — addresses the §7 verification gap
            # "drafts.quality_code = CRITIC_HOLD is opaque."
            critic_block = _format_critic_trace(draft.get("critic_trace"))
            if critic_block:
                for line in critic_block.splitlines():
                    lines.append(line)
                lines.append("")

            if channel_enum == Channel.COLD_EMAIL and draft.get("subject"):
                lines.append(f"**Subject:** {draft['subject']}")
                lines.append("")

            lines.append("```")
            lines.append(draft["body"] or "")
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _update_company_state_to_approved(company_id: int) -> None:
    """Transition the company state from DRAFTED → APPROVED."""
    with with_writer() as conn:
        conn.execute(
            "UPDATE companies SET state = 'APPROVED', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (company_id,),
        )


def write_artifact(
    company_id: int,
    _output_dir: Path | None = None,
) -> Path:
    """Write a Markdown artifact for *company_id* and mark the company APPROVED.

    The file is written to::

        <output_dir>/<company-slug>/<YYYY-MM-DD>-run.md

    where *output_dir* defaults to ``~/.networking-agent/drafts/``.

    Parameters
    ----------
    company_id:
        Primary key of the company in the ``companies`` table.
    _output_dir:
        Override the output directory (used for test injection).

    Returns
    -------
    Path
        Absolute path of the written Markdown file.

    Raises
    ------
    ValueError
        If no company with *company_id* exists in the database.
    """
    company = _load_company(company_id)
    if company is None:
        raise ValueError(f"No company found with id={company_id}")

    contacts = _load_approved_contacts(company_id)

    drafts_by_contact: dict[int, dict[str, dict]] = {}
    for contact in contacts:
        drafts_by_contact[contact["id"]] = _load_latest_drafts_for_contact(contact["id"])

    run_date = datetime.date.today().isoformat()

    artifact_text = _render_artifact(company, contacts, drafts_by_contact, run_date)

    # Resolve output path
    output_dir = _output_dir if _output_dir is not None else _DEFAULT_OUTPUT_DIR
    company_dir = output_dir / company["slug"]
    company_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = company_dir / f"{run_date}-run.md"
    artifact_path.write_text(artifact_text, encoding="utf-8")

    # Transition company state DRAFTED → APPROVED
    _update_company_state_to_approved(company_id)

    print(artifact_path)
    return artifact_path
