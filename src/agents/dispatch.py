"""
src/agents/dispatch.py
REVISE dispatch protocol: regenerate a single draft with user feedback.
Traceability: DESIGN.md §8.3 · VALIDATION Skeptic #2, User Advocate U5 (BLOCKING)
"""

from __future__ import annotations

import concurrent.futures
from typing import Optional

from src.agents.guardrails import check_draft
from src.core.config import HAIKU_MODEL
from src.core.db import get_connection, with_writer
from src.core.schemas import Channel, DraftDispatchRequest, DraftDispatchResponse

__all__ = ["dispatch_revision"]

_MODEL = HAIKU_MODEL
_TIMEOUT_SECONDS = 90


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _load_contact(contact_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, full_name, title, persona, focus_area, linkedin_url, email, hook "
            "FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_prior_draft(draft_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, contact_id, channel, body, subject, version FROM drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _latest_version_for_channel(contact_id: int, channel: Channel) -> int:
    """Return the highest version number for (contact, channel), defaulting to 0."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM drafts WHERE contact_id = ? AND channel = ?",
            (contact_id, channel.value),
        ).fetchone()
        return (row[0] or 0)
    finally:
        conn.close()


def _insert_revised_draft(
    contact_id: int,
    channel: Channel,
    body: str,
    subject: Optional[str],
    version: int,
    quality_flag: bool,
) -> int:
    """Insert a new draft row and return its id."""
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, subject, version, quality_flag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (contact_id, channel.value, body, subject, version, int(quality_flag)),
        )
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

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


def _build_revision_prompt(
    contact: dict,
    channel: Channel,
    prior_body: str,
    feedback: str,
    voice_doc: str = "",
    anti_phrase: Optional[str] = None,
) -> str:
    voice_section = f"\n\n## Voice & Style Rules\n{voice_doc}" if voice_doc else ""
    anti_phrase_section = (
        f"\n\n## CRITICAL: DO NOT USE THIS PHRASE OR ANYTHING SIMILAR\n"
        f'Avoid: "{anti_phrase}"\n'
        f"If you were going to write something like that, rephrase it entirely."
    ) if anti_phrase else ""

    return f"""You are revising an outreach draft for Siddardth Pathipaka, MS Aerospace UIUC (Dec 2025).{voice_section}

## Contact
- Name: {contact['full_name']}
- Title: {contact.get('title') or 'Unknown'}
- LinkedIn: {contact.get('linkedin_url') or 'N/A'}
- Hook: {contact.get('hook') or 'GENERIC'}

## Previous Draft
{prior_body}

## User Feedback
{feedback}

## Channel Constraints
{_CHANNEL_CONSTRAINTS[channel]}
{anti_phrase_section}

Revise the draft incorporating the feedback. Output ONLY the message text (and subject line if applicable) — no preamble, no explanation."""


def _parse_email_body_subject(text: str) -> tuple[str, Optional[str]]:
    lines = text.strip().split("\n")
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][len("subject:"):].strip()
        body = "\n".join(lines[1:]).lstrip("\n").strip()
        return body, subject
    return text.strip(), None


# ---------------------------------------------------------------------------
# LLM call (wrapped for timeout)
# ---------------------------------------------------------------------------

def _call_claude_with_timeout(
    prompt: str,
    anthropic_client,
    timeout: float = _TIMEOUT_SECONDS,
) -> str:
    """Call the LLM in a thread and raise TimeoutError if it exceeds *timeout* seconds."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _call_claude_raw, prompt, anthropic_client
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"LLM call exceeded {timeout}s timeout")


def _call_claude_raw(prompt: str, anthropic_client) -> str:
    response = anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------

def dispatch_revision(
    req: DraftDispatchRequest,
    anthropic_client=None,
    _timeout: float = _TIMEOUT_SECONDS,
) -> DraftDispatchResponse:
    """Regenerate a single draft with user feedback.

    Parameters
    ----------
    req:
        DraftDispatchRequest with contact_id, channel, prior_draft_id, feedback.
    anthropic_client:
        Optional Anthropic client for DI in tests.
    _timeout:
        LLM call timeout in seconds (injected in tests to trigger fast failure).

    Returns
    -------
    DraftDispatchResponse with status=OK|GUARDRAIL_FLAGGED|ERROR.
    """
    if anthropic_client is None:
        try:
            from src.core.config import get_anthropic_client
            anthropic_client = get_anthropic_client()
        except ValueError as exc:
            return DraftDispatchResponse(
                status="ERROR",
                error_message=str(exc),
            )
        except Exception as exc:
            return DraftDispatchResponse(
                status="ERROR",
                error_message=f"Client init failed: {exc}",
            )

    # Load contact and prior draft
    contact = _load_contact(req.contact_id)
    if contact is None:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=f"Contact id={req.contact_id} not found",
        )

    prior_body = ""
    if req.prior_draft_id is not None:
        prior = _load_prior_draft(req.prior_draft_id)
        if prior:
            prior_body = prior.get("body") or ""

    # Determine next version (idempotency: always max+1)
    current_max = _latest_version_for_channel(req.contact_id, req.channel)
    next_version = current_max + 1

    # Voice doc (optional)
    from pathlib import Path
    voice_path = Path.home() / ".networking-agent" / "voice.md"
    voice_doc = voice_path.read_text() if voice_path.exists() else ""

    feedback = req.feedback or ""

    # --- Attempt 1 ---
    try:
        prompt1 = _build_revision_prompt(
            contact, req.channel, prior_body, feedback, voice_doc
        )
        text1 = _call_claude_with_timeout(prompt1, anthropic_client, _timeout)
    except TimeoutError as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=str(exc),
        )
    except Exception as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=f"LLM call failed: {exc}",
        )

    bad_phrase = check_draft(text1)
    if bad_phrase is None:
        # Clean draft — parse and insert
        if req.channel == Channel.COLD_EMAIL:
            body, subject = _parse_email_body_subject(text1)
        else:
            body, subject = text1, None

        try:
            new_id = _insert_revised_draft(
                req.contact_id, req.channel, body, subject, next_version, False
            )
        except Exception as exc:
            return DraftDispatchResponse(
                status="ERROR",
                error_message=f"DB write failed: {exc}",
            )

        return DraftDispatchResponse(
            status="OK",
            new_draft_id=new_id,
            new_version=next_version,
            body=body,
            subject=subject,
            quality_flag=False,
        )

    # --- Attempt 2 (guardrail regen) ---
    try:
        prompt2 = _build_revision_prompt(
            contact, req.channel, prior_body, feedback, voice_doc,
            anti_phrase=bad_phrase,
        )
        text2 = _call_claude_with_timeout(prompt2, anthropic_client, _timeout)
    except TimeoutError as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=str(exc),
        )
    except Exception as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=f"LLM regen failed: {exc}",
        )

    quality_flag = check_draft(text2) is not None

    if req.channel == Channel.COLD_EMAIL:
        body2, subject2 = _parse_email_body_subject(text2)
    else:
        body2, subject2 = text2, None

    try:
        new_id2 = _insert_revised_draft(
            req.contact_id, req.channel, body2, subject2, next_version, quality_flag
        )
    except Exception as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=f"DB write failed: {exc}",
        )

    status = "GUARDRAIL_FLAGGED" if quality_flag else "OK"
    return DraftDispatchResponse(
        status=status,
        new_draft_id=new_id2,
        new_version=next_version,
        body=body2,
        subject=subject2,
        quality_flag=quality_flag,
    )
