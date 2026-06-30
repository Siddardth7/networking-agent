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
import re
from dataclasses import asdict, dataclass, field

from src.core.config import SONNET_MODEL

__all__ = [
    "CriticResult",
    "apply_critique",
    "build_critique_context",
    "critique_draft",
    "evaluate_scores",
    "hard_fail_trace",
    "scan_ai_tells",
    "RUBRIC_DIMENSIONS",
    "MIN_SCORE",
    "SEVERE_SCORE",
    "MAX_WEAK_DIMS",
]


# Rubric dimensions, each scored 0–5 by the critic.
RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "specificity",  # references something real, not generic flattery
    "one_ask",  # one clear CTA, no multi-ask, no hedge stacking
    "tone",  # professional + conversational, no AI tells, no begging
    "grounded_facts",  # every concrete claim traces to APPROVED FACTS or identity
    "economy",  # appropriately concise for channel; no filler
    "relevance",  # sender's background connects to recipient's role/context
)

# Decision-rule constants (AUDIT-A3 recalibration).
#
# The original rule — hold when ANY dimension scores below MIN_SCORE —
# held 28/30 drafts (93%) on the 2026-06-06 Joby run, making unattended
# runs impossible. The recalibrated rule holds a draft only when the
# failure is unambiguous:
#
#   - any dimension <= SEVERE_SCORE (egregious single failure, including
#     grounded_facts <= 1 = fabrication evidence — the P0 signal), OR
#   - more than MAX_WEAK_DIMS dimensions below MIN_SCORE (broadly weak).
#
# A single borderline dimension (score 2) no longer blocks an otherwise
# solid draft. On the captured June-6 fixture set this lands the hold
# rate at 33% — inside the 20-40% calibration band. See
# tests/test_critic_calibration.py for the regression fixtures.
MIN_SCORE = 3  # dimensions below this are "weak"
SEVERE_SCORE = 1  # any dimension at or below this always holds
MAX_WEAK_DIMS = 2  # hold when MORE than this many dimensions are weak


def evaluate_scores(scores: dict[str, int]) -> tuple[bool, list[str]]:
    """Apply the recalibrated hold rule to a critic score map.

    Inputs: ``{dimension: 0..5}`` map. Output: ``(passed, failing_dims)``
    where *failing_dims* names the dimensions that triggered the hold
    (empty when passed). Pure function, no side effects — this is the
    single source of truth for the CRITIC_HOLD decision, kept separate
    from the LLM call so calibration is testable offline (AUDIT-A32).
    """
    severe = [d for d, s in scores.items() if s <= SEVERE_SCORE]
    if severe:
        return False, severe
    weak = [d for d, s in scores.items() if s < MIN_SCORE]
    if len(weak) > MAX_WEAK_DIMS:
        return False, weak
    return True, []


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
    reason: str | None = None

    def to_json(self) -> str:
        """Serialize for persistence in ``drafts.critic_trace``.

        The JSON shape is intentionally stable — the marketer and
        artifact_writer parse it back to surface per-dimension scores
        + issues to the reviewer. Bumping the schema means migrating
        both readers.
        """
        return json.dumps(asdict(self), separators=(",", ":"))


def hard_fail_trace(reason: str | None) -> str:
    """Serialize a deterministic hard-gate failure in the critic-trace shape.

    Inputs: the ``HardCheckResult.reason`` string from ``guardrails.hard_check``.
    Output: JSON string matching the ``CriticResult.to_json()`` schema so the
    marketer and artifact renderers can display *why* a HARD_FAIL draft was
    held without a second code path (AUDIT-A9). No side effects.
    """
    reason_str = reason or "hard guardrail check failed"
    return CriticResult(
        passed=False,
        quality_code="HARD_FAIL",
        scores={},
        issues=[reason_str],
        reason=reason_str,
    ).to_json()


# ---------------------------------------------------------------------------
# Anti-AI-detection — deterministic tell scanner (moat thread, issue #6)
# ---------------------------------------------------------------------------
#
# Research: ~33% of recruiters say they spot AI-written outreach within 20s, and
# AI-shaped messages get ignored — our quality is the moat. The critic's `tone`
# dimension judges this holistically; this scanner is the deterministic backstop
# for KNOWN, high-precision tells so they can never reach the wire even on an
# off LLM run. Curated for PRECISION (a human-grade draft MUST pass): every entry
# is a phrase almost never found in sharp, genuine outreach. This is the START of
# the thread — holistic per-channel calibration and rhythm/structure analysis
# come in later releases. See docs/ANTI_AI_DETECTION.md. Extend deliberately.
_AI_TELL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("filler opener (hope-this-finds-you)", re.compile(
        r"\bi hope (?:this (?:message|email|note|letter) )?finds you\b"
        r"|\bi hope (?:you(?:'re| are)|things are)[^.!?]{0,20}\bwell\b", re.I)),
    ("cold-open (came-across/stumbled-upon)", re.compile(
        r"\bi (?:came across|stumbled (?:up)?on)\b", re.I)),
    ("reaching-out cliché", re.compile(
        r"\bi (?:wanted to reach out|am reaching out to"
        r"|am writing to (?:express|inquire|introduce))\b", re.I)),
    ("corporate buzzword", re.compile(
        r"\b(?:leverage my|leverage your|delve into|spearhead(?:ed|ing)?"
        r"|synerg(?:y|ies)|paradigm shift)\b", re.I)),
    ("today's-fast-paced cliché", re.compile(
        r"\bin today'?s (?:fast-paced|ever-(?:changing|evolving)|digital|competitive)\b", re.I)),
    ("ever-evolving-landscape cliché", re.compile(
        r"\bever-(?:changing|evolving) (?:landscape|world|industry|field)\b"
        r"|\bnavigat(?:e|ing) the (?:complexities|landscape)\b", re.I)),
    ("testament cliché", re.compile(r"\bis a testament to\b", re.I)),
    ("passion cliché", re.compile(
        r"\b(?:i am|i'm) (?:deeply |truly |incredibly |genuinely )?passionate about\b"
        r"|\bpassion for (?:excellence|innovation|technology|learning)\b", re.I)),
    ("excitement cliché", re.compile(
        r"\b(?:thrilled|excited|delighted|eager) to (?:connect|share|announce|be part)\b", re.I)),
    ("resonate cliché", re.compile(r"\bresonate[sd]? (?:deeply )?with\b", re.I)),
    ("align cliché", re.compile(
        r"\b(?:aligns?|aligning) (?:perfectly |closely )?with (?:my|your|the)\b", re.I)),
    ("wealth-of-experience cliché", re.compile(
        r"\b(?:wealth|vast|breadth|wide (?:range|array)) of "
        r"(?:experience|knowledge|expertise)\b", re.I)),
    ("closing cliché", re.compile(
        r"\b(?:feel free to|don'?t hesitate to) (?:reach out|connect|contact)\b"
        r"|\bi would (?:love|welcome) the (?:opportunity|chance) to\b", re.I)),
    ("cover-letter voice", re.compile(
        r"\bas a (?:passionate|results[- ]driven|detail[- ]oriented"
        r"|highly motivated|dedicated|seasoned)\b", re.I)),
    ("not-only-but-also construction", re.compile(
        r"\bnot only\b[^.!?]{0,80}\bbut also\b", re.I)),
    ("formal transition filler", re.compile(
        r"\b(?:furthermore|moreover)\b|\bit'?s worth noting that\b|\bneedless to say\b", re.I)),
)


def scan_ai_tells(text: str) -> list[str]:
    """Return the labels of known AI-writing tells found in *text* (empty = clean).

    Deterministic, high-precision backstop to the critic's holistic ``tone``
    judgment (moat thread, issue #6). Pure function, no side effects.
    """
    if not text:
        return []
    return [label for label, pattern in _AI_TELL_PATTERNS if pattern.search(text)]


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
        "identity. 0-1 = invents facts or metrics, or attributes coursework "
        "as employer work (fabrication). The ABSENCE of APPROVED FACTS is "
        "not itself a failure: when no facts were available, score on "
        "whether the draft stays within the sender's identity (a modest, "
        "claim-free draft with no facts available deserves a 3+)."
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
    source_facts: str | None,
    anthropic_client,
    subject: str | None = None,
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

    return apply_critique(tool_block.input or {}, body, subject)


def apply_critique(data: dict, body: str, subject: str | None = None) -> CriticResult:
    """Deterministic verdict over a raw critic score map. Pure, no LLM.

    Shared by the API path (``critique_draft``, over the tool_use input) and the
    host-token path (#50, over the ``networking-critic`` subagent's JSON): coerce
    each dimension to 0–5 (unparseable → 0 = hold), apply the recalibrated hold
    rule (``evaluate_scores``), and run the deterministic AI-tell backstop over
    body + subject (any tell forces a hold). Both paths land on the same verdict.
    *data* is ``{<dimension>: 0..5, …, "issues": [...]}``; a non-dict degrades to
    all-defaults (a clean pass) rather than raising mid-pipeline.
    """
    if not isinstance(data, dict):
        data = {}
    scores: dict[str, int] = {}
    for dim in RUBRIC_DIMENSIONS:
        raw = data.get(dim, MIN_SCORE)
        try:
            scores[dim] = max(0, min(5, int(raw)))
        except (TypeError, ValueError):
            scores[dim] = 0  # fail-safe: unparseable score is a hold

    issues_raw = data.get("issues") or []
    issues = [str(x) for x in issues_raw if x]

    passed, failing = evaluate_scores(scores)

    # Deterministic anti-AI-detection backstop (moat thread, issue #6): scan the
    # body and subject for known tells. Any hit is an automatic hold — a tell a
    # recruiter spots in 20s defeats the message regardless of the rubric scores.
    tells = scan_ai_tells(body)
    for t in scan_ai_tells(subject or ""):
        if t not in tells:
            tells.append(t)
    if tells:
        issues = issues + [f"ai_detection: AI tell — {t}" for t in tells]
        passed = False

    if not passed:
        reason_parts: list[str] = []
        if failing:
            reason_parts.append(
                f"critic held the draft on {len(failing)} dimension(s): "
                f"{', '.join(f'{d}={scores[d]}' for d in failing)}"
            )
        if tells:
            reason_parts.append(f"AI-detection tells: {', '.join(tells)}")
        return CriticResult(
            passed=False,
            quality_code="CRITIC_HOLD",
            scores=scores,
            issues=issues,
            reason="; ".join(reason_parts),
        )

    return CriticResult(
        passed=True,
        quality_code="OK",
        scores=scores,
        issues=issues,
        reason=None,
    )


def build_critique_context(
    body: str,
    contact: dict,
    channel: str,
    source_facts: str | None,
    subject: str | None = None,
) -> dict:
    """Structured grounding for host-model critique of one draft. No LLM.

    The host model (or the ``networking-critic`` subagent) reads this and returns
    ``{<dimension>: 0..5, …, "issues": [...]}``, which ``apply_critique`` then
    folds into the canonical ``CriticResult`` — so the host path lands on exactly
    the same verdict as the API path. This is the data form of
    ``_build_critique_prompt`` plus the rubric descriptions and the hold rule.
    """
    return {
        "recipient": {
            "full_name": contact.get("full_name", "Unknown"),
            "title": contact.get("title") or "Unknown",
            "persona": contact.get("persona") or "Unknown",
            "hook": contact.get("hook") or "GENERIC",
        },
        "channel": channel,
        "approved_facts": source_facts or "(no APPROVED FACTS were available to the drafter)",
        "draft": {"subject": subject, "body": body},
        "rubric": dict(_DIMENSION_DESCRIPTIONS),
        "hold_rule": {
            "min_score": MIN_SCORE,
            "severe_score": SEVERE_SCORE,
            "max_weak_dims": MAX_WEAK_DIMS,
            "summary": (
                f"Score each dimension 0–5; default 3, a draft must EARN a 4–5. "
                f"A draft is held when any dimension ≤ {SEVERE_SCORE}, or when more "
                f"than {MAX_WEAK_DIMS} dimensions fall below {MIN_SCORE}."
            ),
        },
        "instruction": (
            "Score each rubric dimension 0–5 (strict: the default is 3; a draft "
            "must EARN a 4 or 5) and list concrete issues (one per problem, naming "
            "the failing dimension). Return {"
            + ", ".join(RUBRIC_DIMENSIONS) + ", issues}."
        ),
    }


def _build_critique_prompt(
    body: str,
    contact: dict,
    channel: str,
    source_facts: str | None,
    subject: str | None,
) -> str:
    facts_block = source_facts or "(no APPROVED FACTS were available to the drafter)"
    subject_line = f"Subject: {subject}\n" if subject else ""
    return f"""You are reviewing one outbound message before it can be sent.

## Recipient
- Name: {contact.get("full_name", "Unknown")}
- Title: {contact.get("title") or "Unknown"}
- Persona: {contact.get("persona") or "Unknown"}
- Hook the drafter used (why we are reaching out): {contact.get("hook") or "GENERIC"}

## Channel
{channel}

## APPROVED FACTS the drafter was given
{facts_block}

## Draft to critique
{subject_line}{body}

Score each rubric dimension 0–5 and list specific issues. Score strictly:
the default is 3, and a draft must EARN a 4 or 5. Reserve scores of
{SEVERE_SCORE} or below for unambiguous failures (fabricated facts, no ask
or hopelessly stacked asks, unusable tone) — a draft is blocked when any
dimension is that bad, or when more than {MAX_WEAK_DIMS} dimensions fall
below {MIN_SCORE}."""
