"""
src/agents/artifact_writer.py
Writes a human-readable Markdown artifact for a company's approved contacts and
their final drafted messages, then transitions the company state DRAFTED → APPROVED.

Traceability: PLAN.md Step 7.3
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from src.core.db import get_connection, with_writer
from src.core.schemas import Channel

__all__ = ["write_artifact"]

# Default output directory (overridable via _output_dir for tests)
_DEFAULT_OUTPUT_DIR: Path = Path.home() / ".networking-agent" / "drafts"

# Display-friendly channel labels
_CHANNEL_LABELS: dict[str, str] = {
    Channel.LINKEDIN_CONNECTION.value: "LinkedIn Connection Request",
    Channel.LINKEDIN_POST_CONNECTION.value: "LinkedIn Post-Connection Message",
    Channel.COLD_EMAIL.value: "Cold Email",
}


def _load_company(company_id: int) -> Optional[dict]:
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
            "SELECT id, full_name, title, linkedin_url, email, hook "
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
        {'LINKEDIN_CONNECTION': {id, channel, body, subject, version}, ...}
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, channel, body, subject, version
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
        if contact.get("title"):
            lines.append(f"**Title:** {contact['title']}  ")
        if contact.get("linkedin_url"):
            lines.append(f"**LinkedIn:** {contact['linkedin_url']}  ")
        if contact.get("email"):
            lines.append(f"**Email:** {contact['email']}  ")
        if contact.get("hook"):
            lines.append(f"**Hook:** {contact['hook']}  ")
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

            lines.append(f"_Version {draft['version']}_")
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
            "UPDATE companies SET state = 'APPROVED', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (company_id,),
        )


def write_artifact(
    company_id: int,
    _output_dir: Optional[Path] = None,
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
