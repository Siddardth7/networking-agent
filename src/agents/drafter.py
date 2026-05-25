"""
src/agents/drafter.py
Parallel fan-out draft generation for selected contacts.
Traceability: DESIGN.md §4 (Drafter phases), §6 (Drafting subsystem)
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.agents.achievement_matcher import load_resume_library, match_achievements
from src.agents.guardrails import check_draft
from src.core.config import HAIKU_MODEL
from src.core.db import get_connection, with_writer
from src.core.schemas import Channel, FocusArea, Persona

__all__ = ["Draft", "draft_for_contacts"]

# Cap workers to avoid hitting Anthropic Tier 1 rate limits (50 RPM)
_MAX_WORKERS = 6

_MODEL = HAIKU_MODEL

# Template directory relative to this file: src/agents/ → src/templates/personas/
_PERSONA_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "personas"

_VOICE_DOC_PATH = Path.home() / ".networking-agent" / "voice.md"
_LIBRARY_PATH = Path.home() / ".networking-agent" / "resume_library.yaml"

_CHANNEL_CONSTRAINTS = {
    Channel.LINKEDIN_CONNECTION: (
        "Write ONLY the LinkedIn connection request note. "
        "Hard limit: 300 characters total (including spaces). "
        "Do NOT include a subject line."
    ),
    Channel.LINKEDIN_POST_CONNECTION: (
        "Write the follow-up message sent AFTER the connection is accepted. "
        "Conversational tone; start the relationship, don't pitch directly. "
        "Do NOT include a subject line."
    ),
    Channel.COLD_EMAIL: (
        "Write a cold email. "
        "Hard limit: 150 words for the body (not counting subject line). "
        "Output format — first line: 'Subject: <subject text>', then a blank line, then the email body."
    ),
}


@dataclass
class Draft:
    draft_id: int
    contact_id: int
    channel: str
    body: str
    subject: Optional[str]
    version: int
    quality_flag: bool


def _load_persona_template(persona: Persona) -> str:
    template_map = {
        Persona.RECRUITER: "recruiter.md",
        Persona.SENIOR_MANAGER: "senior_manager.md",
        Persona.PEER_ENGINEER: "peer_engineer.md",
        Persona.ALUMNI: "alumni.md",
    }
    filename = template_map.get(persona, "peer_engineer.md")
    path = _PERSONA_TEMPLATE_DIR / filename
    if path.exists():
        return path.read_text()
    return f"Write outreach messages as Siddardth Pathipaka, MS Aerospace UIUC (Dec 2025)."


def _load_voice_doc() -> str:
    if _VOICE_DOC_PATH.exists():
        return _VOICE_DOC_PATH.read_text()
    return ""


def _load_contact(contact_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, company_id, full_name, title, persona, focus_area, linkedin_url, email, hook "
            "FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _build_prompt(
    contact: dict,
    channel: Channel,
    persona: Persona,
    bullets: list,
    persona_template: str,
    voice_doc: str,
    anti_phrase: Optional[str] = None,
) -> str:
    hook = contact.get("hook") or "GENERIC"
    achievement_text = "\n".join(f"- {b.text}" for b in bullets) if bullets else "(no achievements matched)"

    voice_section = f"\n\n## Voice & Style Rules\n{voice_doc}" if voice_doc else ""

    anti_phrase_section = (
        f"\n\n## CRITICAL: DO NOT USE THIS PHRASE OR ANYTHING SIMILAR\n"
        f'Avoid: "{anti_phrase}"\n'
        f"If you were going to write something like that, rephrase it entirely."
    ) if anti_phrase else ""

    return f"""{persona_template}{voice_section}

## Contact Information
- Name: {contact['full_name']}
- Title: {contact.get('title') or 'Unknown'}
- LinkedIn: {contact.get('linkedin_url') or 'N/A'}
- Email: {contact.get('email') or 'N/A'}
- Hook (why you're reaching out): {hook}

## Relevant Achievements to Reference
{achievement_text}

## Channel Constraints
{_CHANNEL_CONSTRAINTS[channel]}
{anti_phrase_section}

Now write the message. Output ONLY the message text (and subject line if applicable) — no preamble, no explanation."""


def _parse_email_body_subject(text: str) -> tuple[str, Optional[str]]:
    """Extract subject and body from a COLD_EMAIL response.

    Expected format: 'Subject: <text>\\n\\n<body>'
    Falls back to None subject if format not found.
    """
    lines = text.strip().split("\n")
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][len("subject:"):].strip()
        body = "\n".join(lines[1:]).lstrip("\n").strip()
        return body, subject
    return text.strip(), None


def _call_claude(prompt: str, anthropic_client) -> str:
    response = anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _draft_one_channel(
    contact: dict,
    channel: Channel,
    anthropic_client,
    persona_template: str,
    voice_doc: str,
    bullets: list,
) -> tuple[str, Optional[str], bool]:
    """Generate one draft for (contact, channel). Returns (body, subject, quality_flag)."""
    prompt = _build_prompt(contact, channel, Persona(contact["persona"]), bullets, persona_template, voice_doc)
    text = _call_claude(prompt, anthropic_client)

    # Guardrails pass 1
    bad_phrase = check_draft(text)
    if bad_phrase is None:
        body, subject = _parse_email_body_subject(text) if channel == Channel.COLD_EMAIL else (text, None)
        return body, subject, False

    # Regen once with anti-phrase nudge
    prompt2 = _build_prompt(
        contact, channel, Persona(contact["persona"]), bullets,
        persona_template, voice_doc, anti_phrase=bad_phrase
    )
    text2 = _call_claude(prompt2, anthropic_client)

    # Guardrails pass 2
    quality_flag = check_draft(text2) is not None
    body, subject = _parse_email_body_subject(text2) if channel == Channel.COLD_EMAIL else (text2, None)
    return body, subject, quality_flag


def _insert_draft(
    contact_id: int,
    channel: Channel,
    body: str,
    subject: Optional[str],
    quality_flag: bool,
    conn=None,
) -> int:
    """Insert a draft row and return its id.

    If ``conn`` is provided, the INSERT is executed on the supplied connection
    (the caller is responsible for the surrounding transaction / WRITE_LOCK).
    Otherwise a new ``with_writer()`` block is opened.
    """
    if conn is not None:
        cursor = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, subject, version, quality_flag) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (contact_id, channel.value, body, subject, int(quality_flag)),
        )
        return cursor.lastrowid

    with with_writer() as new_conn:
        cursor = new_conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, subject, version, quality_flag) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (contact_id, channel.value, body, subject, int(quality_flag)),
        )
        return cursor.lastrowid


def _mark_contact_drafted(contact_id: int, conn=None) -> None:
    """Mark a contact as DRAFTED.

    If ``conn`` is provided, the UPDATE runs on the supplied connection
    (caller manages the transaction). Otherwise a new ``with_writer()`` block
    is opened.
    """
    if conn is not None:
        conn.execute(
            "UPDATE contacts SET state = 'DRAFTED' WHERE id = ?",
            (contact_id,),
        )
        return

    with with_writer() as new_conn:
        new_conn.execute(
            "UPDATE contacts SET state = 'DRAFTED' WHERE id = ?",
            (contact_id,),
        )


def _draft_all_channels_for_contact(
    contact_id: int,
    anthropic_client,
    library_path: Optional[str],
) -> list[Draft]:
    contact = _load_contact(contact_id)
    if contact is None:
        return []

    try:
        persona = Persona(contact["persona"])
    except (ValueError, TypeError):
        persona = Persona.PEER_ENGINEER

    try:
        focus_area = FocusArea(contact["focus_area"])
    except (ValueError, TypeError):
        focus_area = FocusArea.PEER

    library = load_resume_library(library_path)
    bullets = match_achievements(
        focus_area,
        contact.get("title") or "",
        library,
        top_n=3,
    )

    persona_template = _load_persona_template(persona)
    voice_doc = _load_voice_doc()

    # Generate all drafts via the LLM BEFORE acquiring the writer lock.
    # Anthropic calls are slow (network) and would needlessly serialize
    # parallel workers if held inside with_writer().
    generated: list[tuple[Channel, str, Optional[str], bool]] = []
    for channel in Channel:
        body, subject, quality_flag = _draft_one_channel(
            contact, channel, anthropic_client, persona_template, voice_doc, bullets
        )
        generated.append((channel, body, subject, quality_flag))

    # Atomic per-contact write: delete prior v1 drafts (idempotency from P2),
    # insert all channel drafts, and transition the contact to DRAFTED in one
    # transaction. If any step raises, with_writer() rolls back the whole
    # sequence so we never end up in DRAFTED with missing drafts (P6).
    #
    # Note: with_writer() is NOT reentrant (WRITE_LOCK is a plain
    # threading.Lock). The inserts/state-transition helpers therefore take an
    # optional `conn` and reuse this connection rather than nesting locks.
    drafts: list[Draft] = []
    with with_writer() as conn:
        conn.execute(
            "DELETE FROM drafts WHERE contact_id = ? AND version = 1",
            (contact_id,),
        )

        for channel, body, subject, quality_flag in generated:
            draft_id = _insert_draft(
                contact_id, channel, body, subject, quality_flag, conn=conn
            )
            drafts.append(Draft(
                draft_id=draft_id,
                contact_id=contact_id,
                channel=channel.value,
                body=body,
                subject=subject,
                version=1,
                quality_flag=quality_flag,
            ))

        _mark_contact_drafted(contact_id, conn=conn)

    return drafts


def draft_for_contacts(
    contact_ids: list[int],
    anthropic_client=None,
    library_path: Optional[str] = None,
) -> dict[int, list[Draft]]:
    """Generate drafts for all selected contacts using parallel fan-out.

    Spawns up to _MAX_WORKERS threads (capped at 6 to respect Anthropic Tier 1 limits).
    Each contact's 3 channel drafts are generated sequentially within that contact's
    thread (to allow ordered guardrail logic), but contacts run in parallel.

    Parameters
    ----------
    contact_ids:
        DB ids of contacts to draft for (must be in SELECTED state).
    anthropic_client:
        Optional Anthropic client for DI in tests.
    library_path:
        Optional path override for resume_library.yaml (for tests).

    Returns
    -------
    dict mapping contact_id → list[Draft]
    """
    if anthropic_client is None:
        from src.core.config import get_anthropic_client
        anthropic_client = get_anthropic_client()

    workers = min(_MAX_WORKERS, max(1, len(contact_ids)))
    results: dict[int, list[Draft]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_id = {
            executor.submit(
                _draft_all_channels_for_contact,
                cid,
                anthropic_client,
                library_path,
            ): cid
            for cid in contact_ids
        }
        for future in concurrent.futures.as_completed(future_to_id):
            cid = future_to_id[future]
            try:
                results[cid] = future.result()
            except Exception as exc:
                results[cid] = []
                raise RuntimeError(f"Drafting failed for contact {cid}: {exc}") from exc

    return results
