"""
src/agents/guardrails.py
Reputation guardrails: soft blocklist check + hard-fail safety gate.
Traceability: DESIGN.md §6 (Reputation guardrails); DRAFTER_ROOT_CAUSE_AUDIT.md Layer 3
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "check_draft",
    "hard_check",
    "HardCheckResult",
    "BLOCKLIST",
    "find_placeholder",
    "redact_placeholders",
]

_VOICE_DOC_PATH = Path.home() / ".networking-agent" / "voice.md"

# Always-enforced seed phrases. Voice.md's "## Forbidden Phrases" section is
# merged in at import time so there is a single source of truth.
_SEED_BLOCKLIST: list[str] = [
    "I noticed",
    "I admire",
    "I came across your company",
    "your impressive work",
]


def _load_voice_forbidden_phrases(voice_path: Optional[Path] = None) -> list[str]:
    """Parse the ``## Forbidden Phrases`` section of voice.md.

    Returns the list of phrase strings (one per bullet under that heading),
    or an empty list if the file or section is missing.
    """
    path = voice_path or _VOICE_DOC_PATH
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    section_match = re.search(
        r"##\s*Forbidden Phrases\s*\n(.*?)(?=\n##\s|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []

    phrases: list[str] = []
    for line in section_match.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*", "+")):
            continue
        # Strip the bullet marker and any surrounding quotes/backticks.
        body = stripped[1:].strip().strip('"').strip("'").strip("`")
        # Stop at the first ``—``/``--``/``//`` annotation (treated as a comment).
        for sep in ("—", "--", "//"):
            if sep in body:
                body = body.split(sep, 1)[0].strip()
        if body:
            phrases.append(body)
    return phrases


def _build_blocklist(voice_path: Optional[Path] = None) -> list[str]:
    """Merge seed phrases with voice.md forbidden phrases (case-insensitive dedupe)."""
    seen: set[str] = set()
    result: list[str] = []
    for phrase in _SEED_BLOCKLIST + _load_voice_forbidden_phrases(voice_path):
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            result.append(phrase)
    return result


# Built once at import time from the installed voice.md. Tests that need to
# override the source can monkeypatch `BLOCKLIST` directly or call
# `_build_blocklist(custom_path)` and reassign.
BLOCKLIST: list[str] = _build_blocklist()


def check_draft(text: str) -> Optional[str]:
    """Return the first BLOCKLIST phrase found in *text*, or ``None`` if clean.

    Case-insensitive. The original-cased phrase is returned so callers can
    pass it back as an anti-phrase nudge.
    """
    for phrase in BLOCKLIST:
        if re.search(re.escape(phrase), text, re.IGNORECASE):
            return phrase
    return None


# ---------------------------------------------------------------------------
# Hard-fail gate
# ---------------------------------------------------------------------------

# Any bracketed all-caps token (``[RESEARCH_NEEDED]``, ``[COMPANY]``, etc.) —
# placeholders that must never reach the wire.
_BRACKET_PATTERN = re.compile(r"\[[A-Z][A-Z0-9_]+\]")

# Technical metric: a number adjacent to ``%`` or ``+``.  Picks up
# ``12%`` / ``15+`` while ignoring time references like ``in 15 minutes``.
_METRIC_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*[%+]")

# Replacement text for redacted placeholder tokens. Deliberately NOT a
# bracketed all-caps token so the redacted body can never re-trip the
# detector or be mistaken for a live placeholder.
_REDACTION_TEXT = "(placeholder removed)"


def find_placeholder(text: str) -> Optional[str]:
    """Return the first bracketed placeholder token in *text*, or None.

    Inputs: any draft text. Output: the matched ``[ALL_CAPS]`` token or
    ``None``. No side effects. Used by the drafter to trigger an
    anti-placeholder regeneration (AUDIT-A1) before the hard gate ever
    sees the draft.
    """
    match = _BRACKET_PATTERN.search(text)
    return match.group(0) if match is not None else None


def redact_placeholders(text: str) -> str:
    """Replace every bracketed placeholder token with a redaction marker.

    Inputs: draft text that failed the placeholder hard check. Output:
    the same text with each ``[ALL_CAPS]`` token replaced by
    ``(placeholder removed)``. No side effects. Guarantees a placeholder
    token is never serialized to the DB or a Markdown artifact (AUDIT-A2).
    """
    return _BRACKET_PATTERN.sub(_REDACTION_TEXT, text)


@dataclass
class HardCheckResult:
    """Outcome of :func:`hard_check`.

    ``quality_code`` is ``"OK"`` when ``passed`` is True, ``"HARD_FAIL"``
    otherwise — written verbatim to the ``drafts.quality_code`` column.
    """

    passed: bool
    reason: Optional[str] = None
    quality_code: str = "OK"


def hard_check(
    text: str,
    source_facts: Optional[str] = None,
    channel: Optional[str] = None,
    linkedin_char_limit: int = 200,
    email_word_limit: int = 150,
) -> HardCheckResult:
    """Apply the hard-fail safety gate to a generated draft.

    Returns on the *first* failure with a HARD_FAIL code. The three checks,
    in order:

    1. **Placeholder leak** — any ``[ALL_CAPS]`` bracketed token (catches
       ``[RESEARCH_NEEDED]`` and any future placeholder convention).
    2. **Numeric provenance** — every ``N%`` / ``N+`` style metric in the
       draft must also appear in *source_facts*. Skipped when no facts are
       supplied (we can't verify what we don't know).
    3. **Length** — LinkedIn connection notes are bounded by
       *linkedin_char_limit* characters; cold emails by *email_word_limit*
       words.

    Parameters
    ----------
    text:
        Draft body to inspect.
    source_facts:
        Concatenated text of the achievement bullets shown to the model.
        When falsy, the metric-provenance check is skipped.
    channel:
        Channel name string (e.g. ``"LINKEDIN_CONNECTION"``,
        ``"COLD_EMAIL"``). Only used for length checking.
    linkedin_char_limit, email_word_limit:
        Length thresholds. Wired through from ``config.yaml`` so the free
        LinkedIn 200-char cap is one source of truth.
    """
    bracket_hit = _BRACKET_PATTERN.search(text)
    if bracket_hit is not None:
        return HardCheckResult(
            passed=False,
            reason=f"Placeholder token leaked: {bracket_hit.group(0)!r}",
            quality_code="HARD_FAIL",
        )

    if source_facts:
        for num_str in _METRIC_PATTERN.findall(text):
            # Look for the exact metric (number adjacent to % or +) in facts.
            metric_in_facts = re.compile(
                rf"\b{re.escape(num_str)}\s*[%+]"
            )
            if not metric_in_facts.search(source_facts):
                return HardCheckResult(
                    passed=False,
                    reason=(
                        f"Metric '{num_str}' (with %/+) appears in draft but "
                        f"not in approved facts — possible fabrication"
                    ),
                    quality_code="HARD_FAIL",
                )

    if channel == "LINKEDIN_CONNECTION" and len(text) > linkedin_char_limit:
        return HardCheckResult(
            passed=False,
            reason=f"LinkedIn note is {len(text)} chars (limit {linkedin_char_limit})",
            quality_code="HARD_FAIL",
        )
    if channel == "COLD_EMAIL":
        word_count = len(text.split())
        if word_count > email_word_limit:
            return HardCheckResult(
                passed=False,
                reason=f"Cold email is {word_count} words (limit {email_word_limit})",
                quality_code="HARD_FAIL",
            )

    return HardCheckResult(passed=True, quality_code="OK")
