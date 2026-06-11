"""
src/agents/drafter.py
Parallel fan-out draft generation for selected contacts.
Traceability: DESIGN.md §4 (Drafter phases), §6 (Drafting subsystem)
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from src.agents.achievement_matcher import load_resume_library, match_achievements
from src.agents.critic import critique_draft, hard_fail_trace
from src.agents.guardrails import (
    check_draft,
    detect_multi_ask,
    detect_redundant_intro,
    find_placeholder,
    hard_check,
    redact_placeholders,
)
from src.agents.shared import (
    CHANNEL_CONSTRAINTS,
    call_claude,
    parse_email_body_subject,
)
from src.core.config import HAIKU_MODEL, load_config, voice_doc_path
from src.core.db import get_connection, with_writer
from src.core.schemas import Channel, FocusArea, Persona

__all__ = [
    "Draft",
    "DrafterPartialFailure",
    "OpenerRegistry",
    "draft_for_contacts",
    "normalize_opener",
]

logger = logging.getLogger(__name__)

# Cap workers to avoid hitting Anthropic Tier 1 rate limits (50 RPM)
_MAX_WORKERS = 6

_MODEL = HAIKU_MODEL

# Template directory relative to this file: src/agents/ → src/templates/personas/
_PERSONA_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "personas"

# Size cap for the user-controlled voice doc (AUDIT-A17). An oversized
# file would balloon every prompt (token blow-up / prompt-injection
# amplification); content past the cap is truncated with a warning.
_VOICE_DOC_MAX_CHARS = 16 * 1024

# CHANNEL_CONSTRAINTS now lives in src/agents/shared.py — imported above.
# Kept as a module-level alias so anything still referencing the private
# name keeps working; the source of truth is shared.CHANNEL_CONSTRAINTS.
_CHANNEL_CONSTRAINTS = CHANNEL_CONSTRAINTS


class DrafterPartialFailure(RuntimeError):  # noqa: N818 — public name since v0.1.1; renaming breaks callers
    """Raised when one or more contact drafting workers failed.

    Carries the partial successes so callers can see which contacts were
    drafted vs which raised. Subclasses ``RuntimeError`` so existing callers
    that catch ``RuntimeError`` continue to work (P7).

    Attributes
    ----------
    partial_results:
        Mapping of contact_id → list[Draft] for every worker that completed
        successfully. Empty dict if all workers raised.
    errors:
        List of (contact_id, exception) tuples for every worker that raised.
    """

    def __init__(
        self,
        partial_results: dict[int, list[Draft]],
        errors: list[tuple[int, Exception]],
    ) -> None:
        self.partial_results = partial_results
        self.errors = errors
        failed_ids = [cid for cid, _ in errors]
        # Keep the substring "Drafting failed for contact" in the message so
        # downstream string matchers (and the existing P6 tests) keep working.
        msg = (
            f"Drafting failed for contact(s) {failed_ids}: "
            f"{len(errors)} failed, {len(partial_results)} succeeded"
        )
        super().__init__(msg)


@dataclass
class Draft:
    draft_id: int
    contact_id: int
    channel: str
    body: str
    subject: str | None
    version: int
    quality_flag: bool
    # Canonical quality status. Bool quality_flag is retained for back-compat
    # with the marketer's "⚠" rendering; quality_code is what the gate reads.
    # Values: "OK" | "SOFT_FLAG" | "HARD_FAIL" | "CRITIC_HOLD".
    quality_code: str = "OK"
    # Serialized CriticResult JSON; None when the critic was not run
    # (HARD_FAIL short-circuit, enable_critic=False, pre-migration row).
    # Surfaced in the marketer + artifact so reviewers can see WHY a
    # CRITIC_HOLD verdict was issued — the calibration knob depends on
    # this being durable, not just present in the model response.
    critic_trace: str | None = None


def _load_persona_template(persona: Persona) -> str:
    """Return the persona template text for *persona*.

    Inputs: a Persona enum value. Output: the template file content from
    ``src/templates/personas/`` (utf-8), or a minimal identity line when
    the file is missing. Reads the filesystem; no other side effects.
    """
    template_map = {
        Persona.RECRUITER: "recruiter.md",
        Persona.SENIOR_MANAGER: "senior_manager.md",
        Persona.PEER_ENGINEER: "peer_engineer.md",
        Persona.ALUMNI: "alumni.md",
    }
    filename = template_map.get(persona, "peer_engineer.md")
    path = _PERSONA_TEMPLATE_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Write outreach messages as Siddardth Pathipaka, MS Aerospace UIUC (Dec 2025)."


def _load_voice_doc() -> str:
    """Return the user's voice doc content, size-capped.

    Inputs: none (resolves the path next to config.yaml, honoring the
    NETWORKING_AGENT_CONFIG override, AUDIT-A26). Output: file content
    (utf-8), truncated to ``_VOICE_DOC_MAX_CHARS`` with a logged warning
    when oversized (AUDIT-A17); empty string when the file is absent.
    Reads the filesystem; no other side effects.
    """
    path = voice_doc_path()
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) > _VOICE_DOC_MAX_CHARS:
        logger.warning(
            "voice.md is %d chars; truncating to %d",
            len(text),
            _VOICE_DOC_MAX_CHARS,
        )
        text = text[:_VOICE_DOC_MAX_CHARS]
    return text


def _load_contact(contact_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, company_id, full_name, title, persona, focus_area, "
            "linkedin_url, email, hook "
            "FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


# Regeneration note injected when the first generation leaked a bracketed
# placeholder token (AUDIT-A1). The {token} slot names the offender so the
# model knows exactly what to remove.
_PLACEHOLDER_REGEN_NOTE = (
    "It contained the placeholder token {token}. NEVER emit bracketed "
    "placeholder tokens. If a specific fact is unavailable, omit that "
    "sentence entirely, or anchor on the contact's actual title instead."
)

# Regeneration note injected when the opener has already been used by the
# maximum allowed number of contacts in this run (AUDIT-A6, Layer 1-A).
_OPENER_REGEN_NOTE = (
    'It opened with "{opener}" — the same opening already used for other '
    "contacts in this batch. Write a structurally different opening that "
    "leads with something specific to THIS person."
)

# Regeneration note injected when the draft stacked more than one ask
# (AUDIT-A7) — a main driver of June-6 critic holds.
_ONE_ASK_REGEN_NOTE = (
    "It made more than one ask. Make exactly ONE ask — drop every "
    "secondary request ('otherwise...', 'also, if you know someone...', "
    "'or if there's a better person...')."
)

# Regeneration note injected when the self-intro repeats between the body
# and the signature (AUDIT-A8).
_INTRO_REGEN_NOTE = (
    "It stated the sender's program/school more than once (body AND "
    "signature). State the identity exactly once."
)

# Cross-contact opener bookkeeping (AUDIT-A6). Openers are normalized to a
# short lowercase word window so trivial punctuation differences do not
# defeat the repetition check.
_OPENER_WORD_WINDOW = 12
# Em-dash counts as a sentence break for opener purposes — "Saw your work
# on X — would value connecting" and "Saw your work on X." share an opener.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n—]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


def normalize_opener(text: str) -> str:
    """Normalize a draft's opening for cross-contact repetition checks.

    Inputs: full draft body text. Output: the first sentence, lowercased,
    stripped of non-alphanumerics, capped at ``_OPENER_WORD_WINDOW`` words
    (empty string for empty input). Pure function, no side effects.
    """
    first_sentence = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0]
    cleaned = _NON_ALNUM_RE.sub(" ", first_sentence.lower())
    words = cleaned.split()
    return " ".join(words[:_OPENER_WORD_WINDOW])


class OpenerRegistry:
    """Thread-safe per-run registry of opener usage per channel.

    The drafter fans out one thread per contact; this registry is the
    only cross-contact state, guarded by its own lock. ``is_overused``
    answers "have ``max_repeats`` contacts already used this opener on
    this channel?" and ``register`` records a final draft's opener.
    """

    def __init__(self, max_repeats: int = 2) -> None:
        self.max_repeats = max_repeats
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], int] = {}

    def is_overused(self, channel: str, opener_key: str) -> bool:
        """True when *opener_key* already hit the per-channel repeat cap."""
        if not opener_key:
            return False
        with self._lock:
            return self._counts.get((channel, opener_key), 0) >= self.max_repeats

    def register(self, channel: str, opener_key: str) -> None:
        """Record one more use of *opener_key* on *channel*."""
        if not opener_key:
            return
        with self._lock:
            key = (channel, opener_key)
            self._counts[key] = self._counts.get(key, 0) + 1


_FACT_DISCIPLINE = """## FACT DISCIPLINE — non-negotiable

You may only state facts that appear verbatim (or in trivially-paraphrased
form) in the APPROVED FACTS list below, the Voice & Style identity block,
or the Contact Information section. In particular:

1. **No invented numbers.** If a metric (percentage, count, dollar amount,
   timeline) does not appear in APPROVED FACTS, do NOT introduce one.
   "Significantly reduced weight" is acceptable; "12% weight reduction"
   is NOT unless that exact metric is in APPROVED FACTS.

2. **No re-attribution across project types.** Each bullet is tagged
   with its origin (COMPETITION / COURSEWORK / RESEARCH / INTERNSHIP /
   INDUSTRY). A COMPETITION or COURSEWORK bullet is academic work —
   you may NEVER describe it as work performed at the contact's employer
   or any other company. INTERNSHIP / INDUSTRY bullets may be referenced
   as professional experience. When in doubt, name the project explicitly
   (e.g., "in a SAMPE competition project") rather than implying employer.

3. **No placeholders, ever.** Do NOT emit tokens like `[RESEARCH_NEEDED]`,
   `[COMPANY]`, `[TEAM]`, or any other bracketed all-caps token. If you
   lack a specific fact, omit the sentence — do not flag for follow-up.

4. **Specificity floor.** If the only specific thing you can say is
   generic to the company (e.g., "your eVTOL work"), prefer a shorter
   message that omits the generic line entirely.

5. **No false attribution of company news.** Company-level news or
   announcements may inform your phrasing, but NEVER present them as the
   contact's own posts, statements, or personal work ("your recent
   posts", "saw your work on <company initiative>") unless the signal
   explicitly came from the person themselves.

## MESSAGE STRUCTURE — non-negotiable

- **One ask only.** Make exactly ONE ask. Never stack a second request —
  no "otherwise...", no "also, if you know someone...", no "or if
  there's a better person on your team...". One message, one clear CTA.
- **Identity stated once.** Say who you are (program, school, timeline)
  at most ONCE. If your signature carries the program and school, do not
  repeat them in the body."""


def _render_approved_facts(bullets: list) -> str:
    """Render achievement bullets with provenance for the prompt.

    Each line is: ``- [<TYPE>: <Project Title>] <bullet text>``. The type
    tag is what the FACT DISCIPLINE block uses to forbid re-attribution.
    """
    if not bullets:
        return (
            "(no achievements matched — keep the message brief and grounded "
            "in the identity block only; do NOT invent specifics)"
        )
    lines: list[str] = []
    for b in bullets:
        # Tolerate both ProvenancedBullet and legacy Bullet for now.
        project_title = getattr(b, "project_title", None)
        project_type = getattr(b, "project_type", None)
        if project_title and project_type:
            type_str = project_type.value if hasattr(project_type, "value") else str(project_type)
            lines.append(f"- [{type_str}: {project_title}] {b.text}")
        else:
            lines.append(f"- {b.text}")
    return "\n".join(lines)


def _build_prompt(
    contact: dict,
    channel: Channel,
    persona: Persona,
    bullets: list,
    persona_template: str,
    voice_doc: str,
    anti_phrases: list[str] | None = None,
    extra_instructions: list[str] | None = None,
) -> str:
    """Compose the full grounded generation prompt for one (contact, channel).

    Inputs: contact row dict, channel, persona, provenance-tagged bullets,
    persona template text, voice doc text, plus optional regeneration
    context: *anti_phrases* (blocklist hits to avoid) and
    *extra_instructions* (fault-specific notes such as the anti-placeholder
    rule, AUDIT-A1). Output: the prompt string. No side effects.
    """
    hook = contact.get("hook") or "GENERIC"
    approved_facts = _render_approved_facts(bullets)

    voice_section = f"\n\n## Voice & Style Rules\n{voice_doc}" if voice_doc else ""

    anti_phrase_section = ""
    if anti_phrases:
        joined = "\n".join(f'  - "{p}"' for p in anti_phrases)
        anti_phrase_section = (
            f"\n\n## CRITICAL: DO NOT USE THESE PHRASES OR ANYTHING SIMILAR\n"
            f"{joined}\n"
            f"If you were going to write something like that, rephrase it entirely."
        )

    extra_section = ""
    if extra_instructions:
        joined_notes = "\n".join(f"- {note}" for note in extra_instructions)
        extra_section = (
            f"\n\n## REGENERATION NOTES — your previous attempt had these "
            f"problems; fix every one\n{joined_notes}"
        )

    return f"""{persona_template}{voice_section}

## Contact Information
- Name: {contact["full_name"]}
- Title: {contact.get("title") or "Unknown"}
- LinkedIn: {contact.get("linkedin_url") or "N/A"}
- Email: {contact.get("email") or "N/A"}
- Hook (why you're reaching out): {hook}

## APPROVED FACTS — the only achievements you may state
{approved_facts}

{_FACT_DISCIPLINE}

## Channel Constraints
{_CHANNEL_CONSTRAINTS[channel]}
{anti_phrase_section}{extra_section}

Now write the message. Output ONLY the message text (and subject line if \
applicable) — no preamble, no explanation."""


# _parse_email_body_subject and _call_claude moved to src/agents/shared.py.
# Aliases preserved so tests / external callers keep working.
_parse_email_body_subject = parse_email_body_subject


def _call_claude(prompt: str, anthropic_client) -> str:
    return call_claude(prompt, anthropic_client, model=_MODEL, max_tokens=600)


def _draft_one_channel(
    contact: dict,
    channel: Channel,
    anthropic_client,
    persona_template: str,
    voice_doc: str,
    bullets: list,
    linkedin_char_limit: int = 200,
    email_word_limit: int = 150,
    enable_critic: bool = True,
    opener_registry: OpenerRegistry | None = None,
) -> tuple[str, str | None, bool, str, str | None]:
    """Generate one draft for (contact, channel).

    Returns ``(body, subject, quality_flag, quality_code, critic_trace)``.
    ``quality_code`` is the canonical status; ``quality_flag`` is its
    backward-compatible boolean projection (True iff not ``"OK"``).
    ``critic_trace`` is the serialized CriticResult JSON, or None when
    the critic was not run (HARD_FAIL short-circuit / enable_critic=False).
    """
    prompt = _build_prompt(
        contact, channel, Persona(contact["persona"]), bullets, persona_template, voice_doc
    )
    text = _call_claude(prompt, anthropic_client)

    # Generation-fault pass: collect every detectable fault in the first
    # generation, then regenerate ONCE with all corrective notes combined.
    # Placeholder prevention (AUDIT-A1), multi-ask (AUDIT-A7), redundant
    # self-intro (AUDIT-A8), and opener variety (AUDIT-A6) all live here —
    # upstream of hard_check — so the generator gets one chance to fix
    # itself before any gate fires.
    check_body = parse_email_body_subject(text)[0] if channel == Channel.COLD_EMAIL else text

    anti_phrases: list[str] = []
    extra_notes: list[str] = []

    bad_phrase = check_draft(text)
    if bad_phrase is not None:
        anti_phrases.append(bad_phrase)

    placeholder = find_placeholder(text)
    if placeholder is not None:
        extra_notes.append(_PLACEHOLDER_REGEN_NOTE.format(token=placeholder))

    if detect_multi_ask(check_body):
        extra_notes.append(_ONE_ASK_REGEN_NOTE)

    if detect_redundant_intro(check_body):
        extra_notes.append(_INTRO_REGEN_NOTE)

    # Cross-contact opener variety (AUDIT-A6): if this opener already hit
    # the per-run repeat cap, ask for a structurally different opening.
    if opener_registry is not None and opener_registry.is_overused(
        channel.value, normalize_opener(check_body)
    ):
        raw_opener = _SENTENCE_SPLIT_RE.split(check_body, maxsplit=1)[0][:80]
        extra_notes.append(_OPENER_REGEN_NOTE.format(opener=raw_opener))

    regenerated = bool(anti_phrases or extra_notes)
    if regenerated:
        prompt2 = _build_prompt(
            contact,
            channel,
            Persona(contact["persona"]),
            bullets,
            persona_template,
            voice_doc,
            anti_phrases=anti_phrases or None,
            extra_instructions=extra_notes or None,
        )
        text = _call_claude(prompt2, anthropic_client)

    # Parse body/subject before length-checking so we measure what's sent.
    body, subject = (
        _parse_email_body_subject(text) if channel == Channel.COLD_EMAIL else (text, None)
    )

    # A fault that survives its corrective regen stays visible to the
    # reviewer as SOFT_FLAG (placeholders escalate to HARD_FAIL below).
    soft_failed = regenerated and (
        check_draft(text) is not None or detect_multi_ask(body) or detect_redundant_intro(body)
    )

    # Register the final opener; a draft that still repeats an overused
    # opener after its regen is kept visible to the reviewer as SOFT_FLAG.
    if opener_registry is not None:
        final_key = normalize_opener(body)
        if opener_registry.is_overused(channel.value, final_key):
            soft_failed = True
        opener_registry.register(channel.value, final_key)

    # Hard-fail gate: brackets, fabricated metrics, length. Operates on the
    # body (subject excluded from word/char counts intentionally).
    source_facts = "\n".join(b.text for b in bullets) if bullets else None
    hc = hard_check(
        body,
        source_facts=source_facts,
        channel=channel.value,
        linkedin_char_limit=linkedin_char_limit,
        email_word_limit=email_word_limit,
    )

    if not hc.passed:
        # Hard fail short-circuits — no critic, no soft considerations.
        # The reason is persisted in the trace column so the marketer and
        # artifact can explain the hold (AUDIT-A9), and any surviving
        # placeholder tokens are redacted so they are never serialized
        # to the DB or an artifact (AUDIT-A2).
        if find_placeholder(body) is not None:
            body = redact_placeholders(body)
        return body, subject, True, hc.quality_code, hard_fail_trace(hc.reason)

    # Layer 4: automated critic (Sonnet). Runs only when hard checks pass
    # and the user hasn't disabled it via config. Critic verdict can
    # downgrade OK → CRITIC_HOLD, but never overrides a SOFT_FLAG into OK
    # (soft signals are kept visible for the reviewer).
    critic_code: str | None = None
    critic_trace: str | None = None
    if enable_critic:
        try:
            critic_result = critique_draft(
                body=body,
                contact=contact,
                channel=channel.value,
                source_facts=source_facts,
                anthropic_client=anthropic_client,
                subject=subject,
            )
        except Exception:
            # Critic is fail-OPEN by design: a Sonnet outage or transport
            # blip must not silently downgrade every draft. Hard_check is
            # the real safety net; CRITIC_HOLD is an additional gate.
            critic_result = None
        if critic_result is not None:
            # Persist the trace regardless of pass/fail — passing
            # rationales are useful calibration data too.
            critic_trace = critic_result.to_json()
            if not critic_result.passed:
                critic_code = critic_result.quality_code  # "CRITIC_HOLD"

    if critic_code is not None:
        quality_code = critic_code
    elif soft_failed:
        quality_code = "SOFT_FLAG"
    else:
        quality_code = "OK"

    return body, subject, quality_code != "OK", quality_code, critic_trace


def _insert_draft(
    contact_id: int,
    channel: Channel,
    body: str,
    subject: str | None,
    quality_flag: bool,
    conn: sqlite3.Connection,
    quality_code: str = "OK",
    critic_trace: str | None = None,
) -> int:
    """Insert a draft row and return its id.

    ``conn`` is required: the INSERT executes on the supplied connection and
    the caller is responsible for the surrounding transaction / WRITE_LOCK.
    This helper deliberately does NOT open its own ``with_writer()`` block —
    see the atomicity note in ``_draft_all_channels_for_contact``.
    """
    cursor = conn.execute(
        "INSERT INTO drafts (contact_id, channel, body, subject, version, "
        "quality_flag, quality_code, critic_trace) "
        "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
        (contact_id, channel.value, body, subject, int(quality_flag), quality_code, critic_trace),
    )
    return cursor.lastrowid


def _mark_contact_drafted(contact_id: int, conn: sqlite3.Connection) -> None:
    """Mark a contact as DRAFTED.

    ``conn`` is required: the UPDATE runs on the supplied connection and the
    caller manages the transaction. This helper deliberately does NOT open
    its own ``with_writer()`` block — see the atomicity note in
    ``_draft_all_channels_for_contact``.
    """
    conn.execute(
        "UPDATE contacts SET state = 'DRAFTED' WHERE id = ?",
        (contact_id,),
    )


def _draft_all_channels_for_contact(
    contact_id: int,
    anthropic_client,
    library_path: str | None,
    opener_registry: OpenerRegistry | None = None,
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

    cfg = load_config()

    # Generate all drafts via the LLM BEFORE acquiring the writer lock.
    # Anthropic calls are slow (network) and would needlessly serialize
    # parallel workers if held inside with_writer().
    generated: list[tuple[Channel, str, str | None, bool, str, str | None]] = []
    has_email = bool(contact.get("email"))
    for channel in Channel:
        # Don't burn tokens drafting a cold email when we have no address to
        # send it to. (Root-cause audit §2.4: email drafted without address.)
        if channel == Channel.COLD_EMAIL and not has_email:
            continue
        body, subject, quality_flag, quality_code, critic_trace = _draft_one_channel(
            contact,
            channel,
            anthropic_client,
            persona_template,
            voice_doc,
            bullets,
            linkedin_char_limit=cfg.linkedin_char_limit,
            email_word_limit=cfg.email_word_limit,
            enable_critic=cfg.enable_critic,
            opener_registry=opener_registry,
        )
        generated.append((channel, body, subject, quality_flag, quality_code, critic_trace))

    # Atomic per-contact write: delete prior v1 drafts (idempotency from P2),
    # insert all channel drafts, and transition the contact to DRAFTED in one
    # transaction. If any step raises, with_writer() rolls back the whole
    # sequence so we never end up in DRAFTED with missing drafts (P6).
    #
    # Note: with_writer() is NOT reentrant (WRITE_LOCK is a plain
    # threading.Lock). The inserts/state-transition helpers therefore take an
    # optional `conn` and reuse this connection rather than nesting locks.
    #
    # DO NOT call any helper that itself opens with_writer() from inside this
    # block — WRITE_LOCK is non-reentrant and will deadlock. Helpers used here
    # (_insert_draft, _mark_contact_drafted) require a conn argument for this
    # reason.
    drafts: list[Draft] = []
    with with_writer() as conn:
        conn.execute(
            "DELETE FROM drafts WHERE contact_id = ? AND version = 1",
            (contact_id,),
        )

        for channel, body, subject, quality_flag, quality_code, critic_trace in generated:
            draft_id = _insert_draft(
                contact_id,
                channel,
                body,
                subject,
                quality_flag,
                conn=conn,
                quality_code=quality_code,
                critic_trace=critic_trace,
            )
            drafts.append(
                Draft(
                    draft_id=draft_id,
                    contact_id=contact_id,
                    channel=channel.value,
                    body=body,
                    subject=subject,
                    version=1,
                    quality_flag=quality_flag,
                    quality_code=quality_code,
                    critic_trace=critic_trace,
                )
            )

        _mark_contact_drafted(contact_id, conn=conn)

    return drafts


def draft_for_contacts(
    contact_ids: list[int],
    anthropic_client=None,
    library_path: str | None = None,
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

    # One registry per run — the only shared state between contact
    # workers, used to enforce cross-contact opener variety (AUDIT-A6).
    opener_registry = OpenerRegistry(max_repeats=load_config().opener_max_repeats)

    workers = min(_MAX_WORKERS, max(1, len(contact_ids)))
    results: dict[int, list[Draft]] = {}
    errors: list[tuple[int, Exception]] = []

    # Drain every dispatched future to completion before deciding whether to
    # raise. P6 made each contact's DB write atomic, so allowing in-flight
    # workers to commit-or-rollback fully is correct — we must NOT cancel
    # them. On any worker exception we collect (cid, exc) and continue; the
    # aggregated DrafterPartialFailure is raised after the loop so callers can
    # see which contacts succeeded via `.partial_results`. (P7)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_id = {
            executor.submit(
                _draft_all_channels_for_contact,
                cid,
                anthropic_client,
                library_path,
                opener_registry,
            ): cid
            for cid in contact_ids
        }
        for future in concurrent.futures.as_completed(future_to_id):
            cid = future_to_id[future]
            try:
                results[cid] = future.result()
            except Exception as exc:
                # Narrow to Exception so KeyboardInterrupt / SystemExit
                # propagate immediately — we do NOT want Ctrl-C to be
                # absorbed and deferred until all other workers finish.
                errors.append((cid, exc))

    if errors:
        raise DrafterPartialFailure(partial_results=results, errors=errors)

    return results
