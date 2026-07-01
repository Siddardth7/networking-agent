"""
src/agents/dispatch.py
REVISE dispatch protocol: regenerate a single draft with user feedback.

Layer 6: revisions now use the **same** grounding contract as the first
draft — persona template, APPROVED FACTS with provenance, FACT DISCIPLINE
block, hard_check, optional critic. Pre-Layer-6 the revision prompt
dropped the persona template, dropped the achievement bullets, and
dropped the fact-discipline rules, so REVISE made bad drafts worse.
That regression is gone.

Traceability: DESIGN.md §8.3 · DRAFTER_ROOT_CAUSE_AUDIT.md §2.6, Layer 6
"""

from __future__ import annotations

from src.agents.achievement_matcher import load_resume_library, match_achievements
from src.agents.critic import critique_draft, hard_fail_trace
from src.agents.drafter import (
    _build_prompt,
    _coerce_focus_label,
    _load_persona_template,
    _load_voice_doc,
)
from src.agents.guardrails import (
    check_draft,
    find_placeholder,
    hard_check,
    redact_placeholders,
)
from src.agents.shared import (
    CHANNEL_CONSTRAINTS,
    DEFAULT_TIMEOUT_SECONDS,
    call_claude_with_timeout,
    parse_email_body_subject,
)
from src.core.config import HAIKU_MODEL, load_config
from src.core.db import get_connection, with_writer
from src.core.schemas import (
    Channel,
    DraftDispatchRequest,
    DraftDispatchResponse,
    Persona,
)

__all__ = ["dispatch_revision"]

_MODEL = HAIKU_MODEL
_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS

# Module-level aliases — preserved for back-compat with anything that
# referenced the private names from outside.
_CHANNEL_CONSTRAINTS = CHANNEL_CONSTRAINTS
_parse_email_body_subject = parse_email_body_subject


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_contact(contact_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, full_name, title, persona, focus_area, linkedin_url, "
            "email, hook, shared_signals "
            "FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_prior_draft(draft_id: int) -> dict | None:
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
        return row[0] or 0
    finally:
        conn.close()


def _insert_revised_draft(
    contact_id: int,
    channel: Channel,
    body: str,
    subject: str | None,
    version: int,
    quality_flag: bool,
    quality_code: str = "OK",
    critic_trace: str | None = None,
) -> int:
    """Insert a new draft row and return its id.

    Persists quality_code so the marketer gate (Layer 5) blocks HARD_FAIL
    and CRITIC_HOLD revisions the same way it blocks first-pass drafts,
    and critic_trace so the reviewer can see WHY a revision was held.
    """
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, subject, version, "
            "quality_flag, quality_code, critic_trace) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                contact_id,
                channel.value,
                body,
                subject,
                version,
                int(quality_flag),
                quality_code,
                critic_trace,
            ),
        )
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Prompt construction — fully-grounded, mirrors the first-draft contract
# ---------------------------------------------------------------------------


def _build_revision_prompt(
    contact: dict,
    channel: Channel,
    bullets: list,
    persona_template: str,
    voice_doc: str,
    prior_body: str,
    feedback: str,
    anti_phrases: list[str] | None = None,
) -> str:
    """Build the REVISE prompt using the *same* grounding as the first draft.

    Composition strategy: call ``drafter._build_prompt`` to get the
    persona + APPROVED FACTS + FACT DISCIPLINE block, then append the
    REVISION CONTEXT (prior draft + feedback). The model sees one
    coherent document and cannot quietly drop grounding under feedback
    pressure.
    """
    try:
        persona = Persona(contact["persona"])
    except (KeyError, ValueError, TypeError):
        persona = Persona.PEER_ENGINEER

    base_prompt = _build_prompt(
        contact,
        channel,
        persona,
        bullets,
        persona_template,
        voice_doc,
        anti_phrases=anti_phrases,
    )

    # Replace the trailing "Now write the message." instruction with the
    # revision-specific tail so we keep all grounding but redirect the
    # task.
    base_no_tail = base_prompt.rsplit(
        "Now write the message.",
        1,
    )[0].rstrip()

    return (
        f"{base_no_tail}\n\n"
        f"## REVISION CONTEXT — previous draft\n{prior_body}\n\n"
        f"## REVISION CONTEXT — feedback to address\n{feedback}\n\n"
        "Revise the draft to address the feedback while obeying every "
        "rule above (FACT DISCIPLINE, channel constraints, voice). "
        "Output ONLY the revised message text (and subject line if "
        "applicable) — no preamble, no explanation."
    )


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------


def dispatch_revision(
    req: DraftDispatchRequest,
    anthropic_client=None,
    _timeout: float = _TIMEOUT_SECONDS,
    _library_path: str | None = None,
) -> DraftDispatchResponse:
    """Regenerate a single draft with user feedback, fully-grounded.

    Returns
    -------
    DraftDispatchResponse
        ``status`` is ``OK`` (clean + critic passes), ``GUARDRAIL_FLAGGED``
        (soft blocklist regen flagged), or ``ERROR``.
    """
    if anthropic_client is None:
        try:
            from src.core.config import get_anthropic_client

            anthropic_client = get_anthropic_client()
        except ValueError as exc:
            return DraftDispatchResponse(status="ERROR", error_message=str(exc))
        except Exception as exc:
            return DraftDispatchResponse(
                status="ERROR",
                error_message=f"Client init failed: {exc}",
            )

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

    current_max = _latest_version_for_channel(req.contact_id, req.channel)
    next_version = current_max + 1

    # Load the same grounding the first-draft pipeline used.
    try:
        persona = Persona(contact["persona"])
    except (ValueError, TypeError):
        persona = Persona.PEER_ENGINEER
    focus_area = _coerce_focus_label(contact["focus_area"])

    library = load_resume_library(_library_path)
    bullets = match_achievements(
        focus_area,
        contact.get("title") or "",
        library,
        top_n=3,
    )
    persona_template = _load_persona_template(persona)

    # Same loader as the first-draft path: utf-8, size-capped, env-aware
    # (AUDIT-A17, AUDIT-A23, AUDIT-A26).
    voice_doc = _load_voice_doc()

    feedback = req.feedback or ""

    cfg = load_config()

    # --- Attempt 1 ---
    try:
        prompt1 = _build_revision_prompt(
            contact,
            req.channel,
            bullets,
            persona_template,
            voice_doc,
            prior_body,
            feedback,
        )
        text1 = call_claude_with_timeout(
            prompt1,
            anthropic_client,
            _timeout,
            model=_MODEL,
            max_tokens=600,
        )
    except TimeoutError as exc:
        return DraftDispatchResponse(status="ERROR", error_message=str(exc))
    except Exception as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=f"LLM call failed: {exc}",
        )

    bad_phrase = check_draft(text1)
    soft_failed = False
    final_text = text1
    if bad_phrase is not None:
        # Regen once with anti-phrase nudge.
        try:
            prompt2 = _build_revision_prompt(
                contact,
                req.channel,
                bullets,
                persona_template,
                voice_doc,
                prior_body,
                feedback,
                anti_phrases=[bad_phrase],
            )
            final_text = call_claude_with_timeout(
                prompt2,
                anthropic_client,
                _timeout,
                model=_MODEL,
                max_tokens=600,
            )
        except TimeoutError as exc:
            return DraftDispatchResponse(status="ERROR", error_message=str(exc))
        except Exception as exc:
            return DraftDispatchResponse(
                status="ERROR",
                error_message=f"LLM regen failed: {exc}",
            )
        soft_failed = check_draft(final_text) is not None

    # Parse body/subject for COLD_EMAIL.
    if req.channel == Channel.COLD_EMAIL:
        body, subject = parse_email_body_subject(final_text)
    else:
        body, subject = final_text, None

    # Hard gate.
    source_facts = "\n".join(b.text for b in bullets) if bullets else None
    hc = hard_check(
        body,
        source_facts=source_facts,
        channel=req.channel.value,
        linkedin_char_limit=cfg.linkedin_char_limit,
        email_word_limit=cfg.email_word_limit,
    )

    critic_trace: str | None = None
    if not hc.passed:
        quality_code = hc.quality_code  # HARD_FAIL
        # Persist the gate's reason (AUDIT-A9) and redact any placeholder
        # tokens so they are never serialized (AUDIT-A2) — revision path
        # mirrors the first-draft path.
        critic_trace = hard_fail_trace(hc.reason)
        if find_placeholder(body) is not None:
            body = redact_placeholders(body)
    else:
        critic_code: str | None = None
        if cfg.enable_critic:
            try:
                cr = critique_draft(
                    body=body,
                    contact=contact,
                    channel=req.channel.value,
                    source_facts=source_facts,
                    anthropic_client=anthropic_client,
                    subject=subject,
                )
            except Exception:
                cr = None
            if cr is not None:
                # Persist whether held or passed — calibration data.
                critic_trace = cr.to_json()
                if not cr.passed:
                    critic_code = cr.quality_code  # CRITIC_HOLD
        if critic_code is not None:
            quality_code = critic_code
        elif soft_failed:
            quality_code = "SOFT_FLAG"
        else:
            quality_code = "OK"

    quality_flag = quality_code != "OK"

    try:
        new_id = _insert_revised_draft(
            req.contact_id,
            req.channel,
            body,
            subject,
            next_version,
            quality_flag,
            quality_code,
            critic_trace=critic_trace,
        )
    except Exception as exc:
        return DraftDispatchResponse(
            status="ERROR",
            error_message=f"DB write failed: {exc}",
        )

    # Status mapping:
    #   HARD_FAIL / CRITIC_HOLD → GUARDRAIL_FLAGGED (caller sees flag, marketer blocks)
    #   SOFT_FLAG               → GUARDRAIL_FLAGGED (caller sees flag, marketer allows)
    #   OK                      → OK
    status = "OK" if quality_code == "OK" else "GUARDRAIL_FLAGGED"
    return DraftDispatchResponse(
        status=status,
        new_draft_id=new_id,
        new_version=next_version,
        body=body,
        subject=subject,
        quality_flag=quality_flag,
    )
