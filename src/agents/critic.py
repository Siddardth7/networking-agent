"""
src/agents/critic.py
Layer 4 — automated critic pass.

A stronger model (Sonnet) re-reads each draft and scores it on six rubric
dimensions. Any score below the per-dimension floor flips the draft from
``OK`` to ``CRITIC_HOLD`` so the marketer gate blocks it.

This is the component that earns the right to operate without a human
reviewer — hard_check stops the obvious failures (brackets, fabricated
metrics, over-length), but specificity, single-ask discipline, tone, and
relevance need judgment a regex cannot provide.

Traceability: DRAFTER_ROOT_CAUSE_AUDIT.md Layer 4.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from src.core.config import SONNET_MODEL

__all__ = ["CriticResult", "critique_draft", "RUBRIC_DIMENSIONS"]


# Rubric dimensions, each scored 0–5 by the critic.  Any score below
# MIN_SCORE flips the draft to CRITIC_HOLD.
RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "specificity",      # references something real, not generic flattery
    "one_ask",          # one clear CTA, no multi-ask, no hedge stacking
    "tone",             # professional + conversational, no AI tells, no begging
    "grounded_facts",   # every concrete claim traces to APPROVED FACTS or identity
    "economy",          # appropriately concise for channel; no filler
    "relevance",        # sender's background connects to recipient's role/context
)

MIN_SCORE = 3  # 0–5 scale; any dimension below this triggers CRITIC_HOLD


@dataclass
class CriticResult:
    """Outcome of :func:`critique_draft`.

    Attributes
    ----------
    passed:
        True iff every dimension scored ≥ ``MIN_SCORE``.
    quality_code:
        ``"OK"`` when passed; ``"CRITIC_HOLD"`` otherwise. Written
        verbatim to ``drafts.quality_code`` so the marketer gate can act.
    scores:
        ``{dimension: int}`` map covering every entry in ``RUBRIC_DIMENSIONS``.
        Missing dimensions default to ``MIN_SCORE`` so partial responses
        from the critic are not silently passed.
    issues:
        Short critic-supplied notes — one per failing dimension. Surfaced
        in the marketer's render so the reviewer can act.
    reason:
        One-line summary suitable for logging / display.
    """

    passed: bool
    quality_code: str = "OK"
    scores: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    reason: Optional[str] = None

    def to_json(self) -> str:
        """Serialize for persistence in ``drafts.critic_trace``.

        The JSON shape is intentionally stable — the marketer and
        artifact_writer parse it back to surface per-dimension scores
        + issues to the reviewer. Bumping the schema means migrating
        both readers.
        """
        return json.dumps(asdict(self), separators=(",", ":"))


# ---------------------------------------------------------------------------
# Tool schema — Anthropic tool_use for structured output
# ---------------------------------------------------------------------------


def _build_tool_schema() -> dict:
    """Build the Anthropic tool_use schema for the critique call.

    Each dimension is a 0–5 int; ``issues`` lets the critic name specific
    problems verbatim. Dimensions list is built from RUBRIC_DIMENSIONS so
    the constant is the single source of truth.
    """
    properties: dict = {
        dim: {
            "type": "integer",
            "minimum": 0,
            "maximum": 5,
            "description": _DIMENSION_DESCRIPTIONS[dim],
        }
        for dim in RUBRIC_DIMENSIONS
    }
    properties["issues"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Short notes (one per problem). Empty when the draft is clean. "
            "Each note should name the failing dimension and the concrete "
            "issue, e.g. 'specificity: opens with generic eVTOL line, "
            "no real signal'."
        ),
    }
    return {
        "name": "critique_draft",
        "description": (
            "Score the draft on each rubric dimension (0=unusable, "
            "5=excellent) and list concrete issues found."
        ),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(RUBRIC_DIMENSIONS) + ["issues"],
        },
    }


_DIMENSION_DESCRIPTIONS: dict[str, str] = {
    "specificity": (
        "How specific is the personalization? 0 = generic flattery only; "
        "5 = anchored in a concrete, real signal about the recipient or company."
    ),
    "one_ask": (
        "Does the message make exactly one clear ask? 0 = no ask or "
        "multi-ask/hedge-stack; 5 = single, frictionless, well-placed CTA."
    ),
    "tone": (
        "Is the tone professional yet conversational, with NO AI/recruiter "
        "tells, NO begging, NO over-formality? 0 = obvious AI/cover-letter "
        "voice; 5 = sharp engineer voice."
    ),
    "grounded_facts": (
        "Every concrete claim must trace to APPROVED FACTS or the sender's "
        "identity. 0 = invents facts, attributes coursework as employer work, "
        "or makes claims that cannot be checked; 5 = strictly sourced."
    ),
    "economy": (
        "Is the message appropriately concise for the channel? 0 = bloated "
        "with filler/throat-clearing; 5 = every sentence earns its place."
    ),
    "relevance": (
        "Does the sender's background connect meaningfully to the recipient's "
        "role and context? 0 = no apparent link; 5 = the connection is the "
        "reason this message exists."
    ),
}


# ---------------------------------------------------------------------------
# Critic call
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are a senior recruiter and engineering hiring manager reviewing "
    "outbound networking messages for quality before they are sent. "
    "You score strictly: a draft must EARN a 4 or 5; the default is 3. "
    "Never inflate scores to be polite. Your job is to keep low-quality "
    "messages off the wire."
)


def critique_draft(
    body: str,
    contact: dict,
    channel: str,
    source_facts: Optional[str],
    anthropic_client,
    subject: Optional[str] = None,
) -> CriticResult:
    """Run the critic pass on a generated draft.

    Parameters
    ----------
    body:
        The draft body (subject excluded — passed separately so the critic
        can see it without it counting toward length judgments).
    contact:
        Minimal contact dict — at least ``full_name``, ``title``, ``hook``,
        ``persona``. Drives the relevance rubric.
    channel:
        Channel enum value (``"LINKEDIN_CONNECTION"`` etc.). Affects the
        economy rubric (a 30-word LinkedIn note vs a 140-word cold email).
    source_facts:
        Concatenated APPROVED FACTS that the drafter saw. Drives the
        ``grounded_facts`` rubric.
    anthropic_client:
        Required. Tests inject a Mock; production passes the real client.
    subject:
        Optional COLD_EMAIL subject line — shown to the critic for context.
    """
    user_msg = _build_critique_prompt(
        body=body,
        contact=contact,
        channel=channel,
        source_facts=source_facts,
        subject=subject,
    )

    response = anthropic_client.messages.create(
        model=SONNET_MODEL,
        max_tokens=600,
        system=_SYSTEM_PROMPT,
        tools=[_build_tool_schema()],
        tool_choice={"type": "tool", "name": "critique_draft"},
        messages=[{"role": "user", "content": user_msg}],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        # Critic failed to produce structured output — fail safe by holding.
        return CriticResult(
            passed=False,
            quality_code="CRITIC_HOLD",
            scores={dim: 0 for dim in RUBRIC_DIMENSIONS},
            issues=["critic returned no structured output"],
            reason="critic returned no structured output",
        )

    data = tool_block.input or {}
    scores: dict[str, int] = {}
    for dim in RUBRIC_DIMENSIONS:
        raw = data.get(dim, MIN_SCORE)
        try:
            scores[dim] = max(0, min(5, int(raw)))
        except (TypeError, ValueError):
            scores[dim] = 0  # fail-safe: unparseable score is a hold

    issues_raw = data.get("issues") or []
    issues = [str(x) for x in issues_raw if x]

    failing = [d for d, s in scores.items() if s < MIN_SCORE]
    if failing:
        reason = (
            f"critic flagged {len(failing)} dimension(s) "
            f"below {MIN_SCORE}: {', '.join(failing)}"
        )
        return CriticResult(
            passed=False,
            quality_code="CRITIC_HOLD",
            scores=scores,
            issues=issues,
            reason=reason,
        )

    return CriticResult(
        passed=True,
        quality_code="OK",
        scores=scores,
        issues=issues,
        reason=None,
    )


def _build_critique_prompt(
    body: str,
    contact: dict,
    channel: str,
    source_facts: Optional[str],
    subject: Optional[str],
) -> str:
    facts_block = source_facts or "(no APPROVED FACTS were available to the drafter)"
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"""You are reviewing one outbound message before it can be sent.

## Recipient
- Name: {contact.get('full_name', 'Unknown')}
- Title: {contact.get('title') or 'Unknown'}
- Persona: {contact.get('persona') or 'Unknown'}
- Hook the drafter used (why we are reaching out): {contact.get('hook') or 'GENERIC'}

## Channel
{channel}

## APPROVED FACTS the drafter was given
{facts_block}

## Draft to critique
{subject_line}{body}

Score each rubric dimension 0–5 and list specific issues. Score strictly:
the default is 3, and a draft must EARN a 4 or 5. Any score below {MIN_SCORE}
on any dimension will block the draft from being sent."""
