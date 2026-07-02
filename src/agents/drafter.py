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
from src.agents.humanizer import humanize
from src.agents.shared import (
    CHANNEL_CONSTRAINTS,
    call_claude,
    parse_email_body_subject,
)
from src.core.config import HAIKU_MODEL, load_config, voice_doc_path
from src.core.db import get_connection, with_writer
from src.core.profile import Profile, coerce_focus_label, load_profile
from src.core.schemas import Channel, NextMove, Outcome, Persona

__all__ = [
    "Draft",
    "DrafterPartialFailure",
    "NextMoveDraft",
    "OpenerRegistry",
    "assign_ask_angles",
    "build_draft_context",
    "build_next_move_context",
    "classify_next_move",
    "draft_for_contacts",
    "draft_next_move",
    "gate_host_text",
    "normalize_opener",
    "save_host_draft",
]

logger = logging.getLogger(__name__)

# Hard ceiling on parallel draft workers, and the monkeypatch point used by
# tests to force serial (workers=1) execution. The binding Anthropic limit is
# input-tokens-per-minute (ITPM; 50k on Tier 1), NOT RPM — a full batch at
# high concurrency busts ITPM and even max_retries=8 can't recover. The
# effective worker count is min(this, Config.drafter_max_workers); this
# constant is the absolute ceiling, the config field is the tunable default.
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

    Inputs: a Persona enum value. Output: the template file content (utf-8),
    or a minimal identity line (from the active profile, #61) when no file
    exists. Reads the filesystem; no other side effects.

    Resolution order: the active profile's ``templates_dir`` (a custom
    profile's own persona voice) → the built-in ``src/templates/personas/``
    (the default profile's aerospace-voiced templates, unchanged) → the
    profile's ``fallback_identity`` line.
    """
    template_map = {
        Persona.RECRUITER: "recruiter.md",
        Persona.SENIOR_MANAGER: "senior_manager.md",
        Persona.PEER_ENGINEER: "peer_engineer.md",
        Persona.ALUMNI: "alumni.md",
    }
    filename = template_map.get(persona, "peer_engineer.md")
    profile = load_profile()
    if profile.templates_dir:
        custom = Path(profile.templates_dir).expanduser() / filename
        if custom.exists():
            return custom.read_text(encoding="utf-8")
    path = _PERSONA_TEMPLATE_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Write outreach messages as {profile.fallback_identity}."


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
        # LEFT JOIN companies so the drafter knows WHICH company this contact is
        # at. Without it the model is told (by the persona template) to write to
        # "a fellow alum at {Company}" but has no name, and fabricates one
        # (wrong employer) or leaks a "[Company]" placeholder.
        row = conn.execute(
            "SELECT c.id, c.company_id, c.full_name, c.title, c.persona, "
            "c.focus_area, c.linkedin_url, c.email, c.hook, co.name AS company_name "
            "FROM contacts c LEFT JOIN companies co ON co.id = c.company_id "
            "WHERE c.id = ?",
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

# Regeneration note injected when a LinkedIn connection note exceeds the hard
# character cap. There is no auto-trim, so the generator gets one chance to
# compress before the hard_check HARD_FAILs it on length. The context-first
# opener is good, but a connection note still has a cap (~280) — a generic
# "exploring roles" preamble is the usual culprit when it overflows.
_LENGTH_REGEN_NOTE = (
    "The note was {n} characters but the hard limit is {limit}. Cut it to "
    "under {target} characters. The biggest savings: drop any generic "
    "'exploring roles' preamble AND trim self-identity to at most "
    "'{identity}' (or omit it — the profile carries it). "
    "Spend the budget on the one specific detail about this person plus a "
    "short close like 'Would value connecting.'"
)

# Cross-contact opener bookkeeping (AUDIT-A6). Openers are normalized to a
# short lowercase word window so trivial punctuation differences do not
# defeat the repetition check.
_OPENER_WORD_WINDOW = 12
# Em-dash counts as a sentence break for opener purposes — "Saw your work
# on X — would value connecting" and "Saw your work on X." share an opener.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n—]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")

# Splits on sentence boundaries while KEEPING the terminator on the left side
# (lookbehind), so trimmed notes end on a clean sentence rather than mid-word.
_SENTENCE_KEEP_RE = re.compile(r"(?<=[.!?\n—])\s+")


def _trim_to_char_limit(text: str, limit: int) -> str:
    """Best-effort deterministic trim of an over-length note to ``<= limit``.

    Last-resort recovery for a LinkedIn connection note that still busts the
    hard char cap after its corrective regen (e.g. 287 vs 280): rather than
    HARD_FAIL a marginal overage, keep as many leading whole sentences as fit;
    if even the first sentence is too long, truncate on a word boundary and
    append an ellipsis. Pure function. Returns the original text unchanged
    when it is already within ``limit`` (or when ``limit`` is non-positive).
    """
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text

    kept = ""
    for sentence in _SENTENCE_KEEP_RE.split(text):
        candidate = f"{kept} {sentence}".strip() if kept else sentence.strip()
        if len(candidate) <= limit:
            kept = candidate
        else:
            break
    if kept:
        return kept

    # First sentence alone exceeds the cap: word-boundary truncate + ellipsis
    # (reserve one char for the "…").
    out = ""
    for word in text.split():
        candidate = f"{out} {word}".strip() if out else word
        if len(candidate) + 1 <= limit:
            out = candidate
        else:
            break
    return f"{out}…" if out else text[: max(0, limit - 1)] + "…"


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


# ---------------------------------------------------------------------------
# Phase 3: ask-rotation across same-company contacts
# ---------------------------------------------------------------------------
# Every message is single-ask (FACT DISCIPLINE / MESSAGE STRUCTURE). Variety
# happens ACROSS contacts, not within. The persona templates already tell the
# model to ROTATE the one ask across same-company alumni/peers — but each draft
# is generated in its own thread with no knowledge of what the others asked, so
# left to itself the model converges on the same "safe" angle every time.
#
# Fix: deterministically ASSIGN a distinct angle to each contact in a
# (company, persona) group up front, then inject it into the prompt. This is
# free (no extra LLM calls), race-free (computed before fan-out), and — unlike
# opener variety — can't be done reactively because the realized ask is
# semantic, not a regex match. Singletons get no assignment: a lone contact
# should still get the single most useful angle the model picks for them, which
# is the already-validated v0.3.0 behavior.
#
# Pools are kept in sync with the Close section of the matching persona
# template; the prose and the injected instruction must agree.
def _alumni_ask_angles(school_name: str) -> tuple[str, str, str, str, str]:
    """The alumni ask-angle pool, with the shared school from the profile (#61)."""
    return (
        "the hiring climate on their team right now (are they growing, are reqs open)",
        "whether the company sponsors or hires international students "
        "(STEM OPT / H-1B) for technical roles",
        "what the team and engineering culture are actually like day to day",
        f"how their own {school_name}-to-industry transition went "
        "and what they'd do differently",
        "who on the team would be the right person to talk to about the work",
    )


_PEER_ASK_ANGLES: tuple[str, str, str, str, str] = (
    "what the day-to-day engineering work on their team is actually like",
    "how they approached a specific project or technical challenge in their work",
    "what the team culture and trajectory feel like from the inside",
    "how they broke into the field and what their path looked like",
    "what they'd tell someone finishing an MS who's aiming at this kind of work",
)

# Personas whose one ask is worth rotating. Recruiters tie the ask to a
# specific role (and are ~one per company); senior managers carry no hard ask
# (low-obligation "stay connected"). Neither has anything to rotate.


def _ask_angle_pools(profile: Profile) -> dict[Persona, tuple[str, ...]]:
    return {
        Persona.ALUMNI: _alumni_ask_angles(profile.school_name),
        Persona.PEER_ENGINEER: _PEER_ASK_ANGLES,
    }


def assign_ask_angles(contact_ids: list[int]) -> dict[int, str | None]:
    """Assign a distinct ask-angle to each same-company, same-persona contact.

    Inputs: the contact ids being drafted this run. Output: a mapping of
    contact_id → angle string (the angle to anchor that contact's one ask on)
    or None (no rotation — let the model pick the best angle). Reads the
    contacts table; no writes.

    Contacts are grouped by ``(company_id, persona)``. Within a group of a
    rotation-eligible persona (alumni / peer) that has 2+ members, angles are
    handed out round-robin in stable contact_id order, so N contacts get N
    distinct angles (wrapping if the group is larger than the pool). Singletons,
    non-eligible personas, and unknown personas map to None.
    """
    if not contact_ids:
        return {}

    placeholders = ",".join("?" for _ in contact_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT id, company_id, persona FROM contacts WHERE id IN ({placeholders})",
            tuple(contact_ids),
        ).fetchall()
    finally:
        conn.close()

    # Group contact ids by (company_id, persona), preserving stable id order.
    pools = _ask_angle_pools(load_profile())
    groups: dict[tuple, list[int]] = {}
    persona_by_group: dict[tuple, Persona] = {}
    for row in rows:
        try:
            persona = Persona(row["persona"])
        except (ValueError, TypeError):
            continue
        if persona not in pools:
            continue
        key = (row["company_id"], persona)
        groups.setdefault(key, []).append(row["id"])
        persona_by_group[key] = persona

    assignments: dict[int, str | None] = {cid: None for cid in contact_ids}
    for key, ids in groups.items():
        if len(ids) < 2:
            continue  # singleton → no rotation, model picks the best angle
        pool = pools[persona_by_group[key]]
        for i, cid in enumerate(sorted(ids)):
            assignments[cid] = pool[i % len(pool)]
    return assignments


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

6. **One company, named exactly.** The contact's employer is given as
   "Company" in the Contact Information section. When you name their company,
   use that exact name and NO other — never substitute or invent a different
   employer. If Company is "Unknown", do not name any company; refer to "your
   team" / "your company" instead. Never emit a bracketed token like
   "[Company]".

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
    ask_angle: str | None = None,
) -> str:
    """Compose the full grounded generation prompt for one (contact, channel).

    Inputs: contact row dict, channel, persona, provenance-tagged bullets,
    persona template text, voice doc text, plus optional regeneration
    context: *anti_phrases* (blocklist hits to avoid) and
    *extra_instructions* (fault-specific notes such as the anti-placeholder
    rule, AUDIT-A1). *ask_angle* (Phase 3) is the rotation-assigned angle to
    anchor this contact's single ask on, when several same-company contacts of
    this persona are being reached. Output: the prompt string. No side effects.
    """
    hook = contact.get("hook") or "GENERIC"
    approved_facts = _render_approved_facts(bullets)

    voice_section = f"\n\n## Voice & Style Rules\n{voice_doc}" if voice_doc else ""

    # Phase 3: when an angle was assigned, steer the (still single) ask toward
    # it so same-company contacts don't all land on the same script. This rides
    # on top of the "one ask only" rule — it changes WHICH ask, not how many.
    ask_angle_section = ""
    if ask_angle:
        ask_angle_section = (
            "\n\n## ASSIGNED ASK ANGLE — this is the ONE ask for this person\n"
            "Several people at this company are being contacted, so the ask is "
            "rotated to avoid sending the same script to everyone. Anchor your "
            f"single ask on: {ask_angle}.\n"
            "Make exactly ONE ask, and make it this one. Phrase it naturally in "
            "your own voice — do not quote this instruction. Do not add a second "
            "ask of any kind."
        )

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
- Company: {contact.get("company_name") or "Unknown"}
- Title: {contact.get("title") or "Unknown"}
- LinkedIn: {contact.get("linkedin_url") or "N/A"}
- Email: {contact.get("email") or "N/A"}
- Hook (why you're reaching out): {hook}

## APPROVED FACTS — the only achievements you may state
{approved_facts}

{_FACT_DISCIPLINE}

## Channel Constraints
{_CHANNEL_CONSTRAINTS[channel]}
{ask_angle_section}{anti_phrase_section}{extra_section}

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
    linkedin_char_limit: int = 280,
    email_word_limit: int = 150,
    enable_critic: bool = True,
    opener_registry: OpenerRegistry | None = None,
    ask_angle: str | None = None,
) -> tuple[str, str | None, bool, str, str | None]:
    """Generate one draft for (contact, channel).

    Returns ``(body, subject, quality_flag, quality_code, critic_trace)``.
    ``quality_code`` is the canonical status; ``quality_flag`` is its
    backward-compatible boolean projection (True iff not ``"OK"``).
    ``critic_trace`` is the serialized CriticResult JSON, or None when
    the critic was not run (HARD_FAIL short-circuit / enable_critic=False).
    ``ask_angle`` (Phase 3) is the rotation-assigned ask angle for this
    contact, or None to let the model pick the single best angle.
    """
    prompt = _build_prompt(
        contact,
        channel,
        Persona(contact["persona"]),
        bullets,
        persona_template,
        voice_doc,
        ask_angle=ask_angle,
    )
    text = humanize(_call_claude(prompt, anthropic_client))

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

    # Length pre-check (LinkedIn connection note only): if the note busts the
    # hard char cap, give the generator one chance to compress before the
    # hard_check HARD_FAILs it. There is no auto-trim retry otherwise.
    if channel == Channel.LINKEDIN_CONNECTION and len(check_body) > linkedin_char_limit:
        extra_notes.append(
            _LENGTH_REGEN_NOTE.format(
                n=len(check_body),
                limit=linkedin_char_limit,
                target=max(0, linkedin_char_limit - 25),
                identity=load_profile().identity_short,
            )
        )

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
            ask_angle=ask_angle,
        )
        text = humanize(_call_claude(prompt2, anthropic_client))

    # Parse body/subject before length-checking so we measure what's sent.
    body, subject = (
        _parse_email_body_subject(text) if channel == Channel.COLD_EMAIL else (text, None)
    )

    # Auto-trim (LinkedIn connection only): the one-shot length regen above
    # asks the model to compress, but it can come back still marginally over
    # the cap (e.g. 287 vs 280). Rather than let hard_check HARD_FAIL a draft
    # that is otherwise fine, deterministically trim it to fit. The trim is
    # surfaced as SOFT_FLAG below so the reviewer sees the note was machine-
    # shortened rather than silently sent as OK.
    auto_trimmed = False
    if channel == Channel.LINKEDIN_CONNECTION and len(body) > linkedin_char_limit:
        trimmed = _trim_to_char_limit(body, linkedin_char_limit)
        if trimmed != body and len(trimmed) <= linkedin_char_limit:
            body = trimmed
            auto_trimmed = True

    # A fault that survives its corrective regen stays visible to the
    # reviewer as SOFT_FLAG (placeholders escalate to HARD_FAIL below).
    soft_failed = auto_trimmed or (
        regenerated
        and (
            check_draft(text) is not None
            or detect_multi_ask(body)
            or detect_redundant_intro(body)
        )
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
    ask_angle: str | None = None,
) -> list[Draft]:
    contact = _load_contact(contact_id)
    if contact is None:
        return []

    try:
        persona = Persona(contact["persona"])
    except (ValueError, TypeError):
        persona = Persona.PEER_ENGINEER

    focus_area = coerce_focus_label(contact["focus_area"])

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
            ask_angle=ask_angle,
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

    Spawns up to min(_MAX_WORKERS, Config.drafter_max_workers) threads
    (default 3, sized to keep a batch under the Anthropic Tier-1 ITPM ceiling).
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
    cfg = load_config()
    opener_registry = OpenerRegistry(max_repeats=cfg.opener_max_repeats)

    # Phase 3: assign each contact a rotated ask angle BEFORE fan-out, so
    # same-company alumni/peers ask different things. Computed once up front
    # (deterministic, race-free); singletons / non-eligible personas map to
    # None and behave exactly as before.
    ask_angles = assign_ask_angles(contact_ids) if cfg.enable_ask_rotation else {}

    # Effective concurrency = min(hard ceiling, configured default, batch size).
    # Tests monkeypatch _MAX_WORKERS=1 to force serial execution; that still
    # wins here via the min(). (Finding A: default 3 keeps a batch under the
    # Tier-1 ITPM limit; users on higher tiers raise cfg.drafter_max_workers.)
    workers = max(1, min(_MAX_WORKERS, cfg.drafter_max_workers, len(contact_ids)))
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
                ask_angles.get(cid),
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


# ---------------------------------------------------------------------------
# Reply-aware next-move drafting (issue #19, A8 — "they replied, now what?")
# ---------------------------------------------------------------------------
#
# When a contact replies, the hardest moment is the next message: take the
# offered intro, answer the sponsorship question, propose a chat, or ask for a
# referral. We classify the move DETERMINISTICALLY from the reply text + the
# recorded outcome (same deterministic-over-LLM lesson as #5/#6/#18 — the model
# is great at phrasing, not at being auditable), then draft it through the SAME
# voice + gate machinery as a cold draft (humanize → hard_check → critic).

# ponytail: keyword cue heuristic, not intent NLU. Checked in goal-advancing
# precedence order (intro > sponsorship > schedule > referral). The CLI's
# --move flag overrides it when the human reads the reply differently. Extend
# the cue lists as real replies show what language people actually use.
_INTRO_CUES: tuple[str, ...] = (
    "introduce you", "connect you", "i'll connect", "let me connect",
    "put you in touch", "happy to refer", "can refer", "i'll refer",
    "loop you in", "loop in", "you should talk to", "reach out to",
)
_SPONSORSHIP_CUES: tuple[str, ...] = (
    "sponsor", "sponsorship", "visa", "h-1b", "h1b",
    "work authorization", "work auth", "green card",
)
_CALL_CUES: tuple[str, ...] = (
    "call", "chat", "talk", "speak", "meet", "schedule", "calendar",
    "zoom", "phone", "hop on", "catch up", "grab time", "coffee",
)
_REFERRAL_CUES: tuple[str, ...] = (
    "refer", "referral", "hiring", "opening", "open role", "position",
    "apply", "recruiter", "we're looking", "job",
)


def _matches_any(text: str, cues: tuple[str, ...]) -> bool:
    """True if any cue appears in *text* as a whole word/phrase (boundary-safe).

    Word boundaries stop short cues like "call" firing inside "recall".
    """
    return any(re.search(rf"\b{re.escape(cue)}\b", text) for cue in cues)


def classify_next_move(reply_text: str, outcome: str | None = None) -> NextMove:
    """Pick the next move from a reply + recorded outcome. Pure, deterministic.

    Precedence: a concrete intro/POC offer (or recorded ``POC`` outcome) →
    THANK_INTRO; an explicit sponsorship/visa mention → SPONSORSHIP_QUESTION; an
    invitation to talk → SCHEDULE_CALL; a hiring/role mention → REFERRAL_ASK; any
    other warm reply → SCHEDULE_CALL (advance to a conversation).
    """
    text = (reply_text or "").lower()
    if outcome == Outcome.POC.value or _matches_any(text, _INTRO_CUES):
        return NextMove.THANK_INTRO
    if _matches_any(text, _SPONSORSHIP_CUES):
        return NextMove.SPONSORSHIP_QUESTION
    if _matches_any(text, _CALL_CUES):
        return NextMove.SCHEDULE_CALL
    if _matches_any(text, _REFERRAL_CUES):
        return NextMove.REFERRAL_ASK
    return NextMove.SCHEDULE_CALL


_MOVE_INSTRUCTIONS: dict[NextMove, str] = {
    NextMove.SCHEDULE_CALL: (
        "They're open to engaging. Propose a brief (15-minute) call to talk. "
        "Offer to work around their schedule. Make exactly ONE ask: the call."
    ),
    NextMove.SPONSORSHIP_QUESTION: (
        "They touched on work authorization / sponsorship. Warmly and directly "
        "ask whether the team or company sponsors work visas for a role like "
        "this. Ask exactly ONE clear question."
    ),
    NextMove.REFERRAL_ASK: (
        "They're warm and mentioned roles or hiring. Ask — low-friction — "
        "whether they'd be open to referring you or pointing you to the right "
        "person on the hiring side. Make exactly ONE specific ask."
    ),
    NextMove.THANK_INTRO: (
        "They offered an introduction or a point of contact. Thank them "
        "genuinely and confirm the next step (that you'd welcome the intro / who "
        "you'll reach out to). Warm, gracious, ONE clear close — no new ask "
        "piled on."
    ),
}


def _build_next_move_prompt(
    contact: dict,
    channel: Channel,
    move: NextMove,
    reply_text: str,
    voice_doc: str,
) -> str:
    """Compose the reply-aware next-move prompt. No side effects."""
    voice_section = f"\n\n## Voice & Style Rules\n{voice_doc}" if voice_doc else ""
    hook = contact.get("hook") or "GENERIC"
    return f"""You are drafting the NEXT message in an ongoing outreach \
conversation. The contact replied to your earlier message; write the single \
best next move toward building a warm, useful connection.{voice_section}

## Conversation context
- Contact: {contact["full_name"]}
- Company: {contact.get("company_name") or "Unknown"}
- Title: {contact.get("title") or "Unknown"}
- Why you first reached out: {hook}

## Their reply (verbatim)
\"\"\"
{reply_text}
\"\"\"

## Your next move: {move.value}
{_MOVE_INSTRUCTIONS[move]}

{_FACT_DISCIPLINE}

## Channel Constraints
{_CHANNEL_CONSTRAINTS[channel]}

Write ONLY the reply message (and a subject line if it's an email) — no \
preamble, no explanation, and do not quote these instructions."""


@dataclass
class NextMoveDraft:
    """A gated reply-aware next move (issue #19)."""

    contact_id: int
    move: NextMove
    body: str
    subject: str | None
    quality_code: str  # "OK" / "HARD_FAIL" / "CRITIC_HOLD"
    critic_trace: str | None


def draft_next_move(
    contact_id: int,
    reply_text: str,
    *,
    anthropic_client,
    channel: Channel | None = None,
    outcome: str | None = None,
    move: NextMove | None = None,
    enable_critic: bool | None = None,
) -> NextMoveDraft | None:
    """Draft the gated next move for a contact who replied.

    Returns None if the contact is unknown. *move* forces the next-move type
    (else it's classified from *reply_text*/*outcome*); *channel* defaults to
    email when an address is on file, else the post-connection LinkedIn thread.
    Runs the same humanize → hard_check → critic gates as a cold draft; a
    HARD_FAIL short-circuits the critic and redacts any leaked placeholder.
    """
    contact = _load_contact(contact_id)
    if contact is None:
        return None

    if channel is None:
        channel = Channel.COLD_EMAIL if contact.get("email") else Channel.LINKEDIN_POST_CONNECTION
    chosen_move = move or classify_next_move(reply_text, outcome)

    cfg = load_config()
    if enable_critic is None:
        enable_critic = cfg.enable_critic

    voice_doc = _load_voice_doc()
    prompt = _build_next_move_prompt(contact, channel, chosen_move, reply_text, voice_doc)
    text = humanize(_call_claude(prompt, anthropic_client))
    body, subject = (
        _parse_email_body_subject(text) if channel == Channel.COLD_EMAIL else (text, None)
    )

    # The next move makes no new metric claims, so source_facts=None (skips the
    # numeric-provenance check); placeholder + length gates still apply.
    hc = hard_check(
        body,
        source_facts=None,
        channel=channel.value,
        linkedin_char_limit=cfg.linkedin_char_limit,
        email_word_limit=cfg.email_word_limit,
    )
    if not hc.passed:
        if find_placeholder(body) is not None:
            body = redact_placeholders(body)
        return NextMoveDraft(
            contact_id, chosen_move, body, subject, hc.quality_code, hard_fail_trace(hc.reason)
        )

    quality_code = "OK"
    critic_trace: str | None = None
    if enable_critic:
        try:
            critic_result = critique_draft(
                body=body,
                contact=contact,
                channel=channel.value,
                source_facts=None,
                anthropic_client=anthropic_client,
                subject=subject,
            )
        except Exception:
            critic_result = None  # critic is fail-open; hard_check is the safety net
        if critic_result is not None:
            critic_trace = critic_result.to_json()
            if not critic_result.passed:
                quality_code = critic_result.quality_code

    return NextMoveDraft(contact_id, chosen_move, body, subject, quality_code, critic_trace)


# ---------------------------------------------------------------------------
# Host-token drafting seam (issue #50): run the writing step on the HOST Claude's
# tokens instead of a separate API key. `build_draft_context` is the
# deterministic handoff — everything the host model (or a `model: sonnet`
# drafter subagent) needs to write a draft, with NO LLM call — and
# `save_host_draft` runs the same deterministic guardrails on the host-produced
# text before persisting. The host model does the writing; Python keeps the
# facts, the voice, and the safety gate. Both are pure of the Anthropic client.
# ---------------------------------------------------------------------------


def _load_posting(job_id: str) -> dict | None:
    """Return a posting's role_title + job_url for role-aware drafting (#60), or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT role_title, job_url FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"job_id": job_id, "role_title": row["role_title"], "job_url": row["job_url"]}


def build_draft_context(
    contact_id: int,
    channel: Channel,
    *,
    library_path: str | None = None,
    job_id: str | None = None,
) -> dict | None:
    """Assemble the structured inputs a host model needs to draft. No LLM call.

    Returns None for an unknown contact. The dict carries the contact facts, the
    persona template, the voice doc, the matched achievement bullets ("approved
    facts"), the fact-discipline rules, and the channel constraints — the exact
    grounding the API path feeds into its prompt, exposed as data so the host
    model (or the drafter subagent) can do the writing on host tokens.

    Application mode (#60): when *job_id* names a posting, the context gains a
    ``posting`` block (role_title + job_url) so the note can name the specific
    role — a named-role ask out-converts a generic company ask. ``posting`` is
    None in Campaign mode (or for an unknown job_id), leaving behavior unchanged.
    """
    contact = _load_contact(contact_id)
    if contact is None:
        return None
    try:
        persona = Persona(contact["persona"])
    except (ValueError, TypeError):
        persona = Persona.PEER_ENGINEER
    focus_area = coerce_focus_label(contact["focus_area"])

    library = load_resume_library(library_path)
    bullets = match_achievements(focus_area, contact.get("title") or "", library, top_n=3)

    return {
        "contact": {
            "full_name": contact["full_name"],
            "title": contact.get("title"),
            "company": contact.get("company_name"),
            "linkedin_url": contact.get("linkedin_url"),
            "email": contact.get("email"),
            "hook": contact.get("hook") or "GENERIC",
            "persona": persona.value,
            "focus_area": str(focus_area),
        },
        "persona_template": _load_persona_template(persona),
        "voice_doc": _load_voice_doc(),
        "approved_facts": [b.text for b in bullets],
        "fact_discipline": _FACT_DISCIPLINE,
        "channel": channel.value,
        "channel_constraints": _CHANNEL_CONSTRAINTS[channel],
        "posting": _load_posting(job_id) if job_id else None,
    }


def gate_host_text(
    body: str,
    channel: Channel,
    *,
    source_facts: str | None = None,
) -> dict:
    """Run the deterministic safety gate on host-written text. No LLM, no persist.

    Humanize → hard_check (placeholder / fabrication / length), redacting any
    leaked placeholder on HARD_FAIL. Returns ``{"quality_code", "body",
    "critic_trace"}`` where ``critic_trace`` is the hard-fail reason (or None).
    Shared by ``save_host_draft`` (draft path) and the next-move path so the
    gate is one source of truth.
    """
    cfg = load_config()
    body = humanize(body)
    hc = hard_check(
        body,
        source_facts=source_facts,
        channel=channel.value,
        linkedin_char_limit=cfg.linkedin_char_limit,
        email_word_limit=cfg.email_word_limit,
    )
    quality_code = "OK" if hc.passed else hc.quality_code
    critic_trace = None
    if not hc.passed:
        critic_trace = hard_fail_trace(hc.reason)
        if find_placeholder(body) is not None:
            body = redact_placeholders(body)
    return {"quality_code": quality_code, "body": body, "critic_trace": critic_trace}


def save_host_draft(
    contact_id: int,
    channel: Channel,
    body: str,
    subject: str | None = None,
    *,
    source_facts: str | None = None,
) -> dict:
    """Persist a host-model-written draft after the deterministic gate. No LLM call.

    Runs the shared :func:`gate_host_text` safety gate, inserts the draft, and
    marks the contact DRAFTED. The critic (a judgment step) is intentionally left
    to the host model / a critic subagent — this keeps the *safety* gate in
    tested Python. Returns ``{"draft_id", "quality_code", "body", "subject"}``.
    """
    gated = gate_host_text(body, channel, source_facts=source_facts)
    body, quality_code = gated["body"], gated["quality_code"]
    with with_writer() as conn:
        draft_id = _insert_draft(
            contact_id, channel, body, subject, quality_code != "OK", conn,
            quality_code=quality_code, critic_trace=gated["critic_trace"],
        )
        _mark_contact_drafted(contact_id, conn)

    return {"draft_id": draft_id, "quality_code": quality_code, "body": body, "subject": subject}


def build_next_move_context(
    contact_id: int,
    reply_text: str,
    *,
    channel: Channel | None = None,
    outcome: str | None = None,
    move: NextMove | None = None,
) -> dict | None:
    """Assemble the structured inputs a host model needs to draft the next move.

    No LLM call. The move is classified deterministically (``classify_next_move``)
    unless *move* overrides it; the dict carries the contact facts, the reply, the
    chosen move + its instruction, the voice doc, fact discipline, and channel
    constraints — the same grounding the API path's prompt uses. Returns None for
    an unknown contact. Channel defaults to email when an address is on file.
    """
    contact = _load_contact(contact_id)
    if contact is None:
        return None
    if channel is None:
        channel = Channel.COLD_EMAIL if contact.get("email") else Channel.LINKEDIN_POST_CONNECTION
    chosen = move or classify_next_move(reply_text, outcome)
    return {
        "contact": {
            "full_name": contact["full_name"],
            "title": contact.get("title"),
            "company": contact.get("company_name"),
            "hook": contact.get("hook") or "GENERIC",
        },
        "reply": reply_text,
        "move": chosen.value,
        "move_instruction": _MOVE_INSTRUCTIONS[chosen],
        "voice_doc": _load_voice_doc(),
        "fact_discipline": _FACT_DISCIPLINE,
        "channel": channel.value,
        "channel_constraints": _CHANNEL_CONSTRAINTS[channel],
    }
