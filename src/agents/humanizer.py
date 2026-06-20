"""Deterministic humanizer pass.

Small, conservative post-generation cleanup that strips AI/filler patterns the
soft blocklist regen can't reliably shake (a small model like Haiku keeps
reaching for them). Every rule here must be GRAMMATICALLY SAFE in all contexts —
this is intensifier *deletion*, never a paraphrase. Anything fuzzier belongs in
the critic, not here.

Wired into the Drafter after each generation call (initial + regen) so the tell
never reaches the gate, the artifact, or the wire. See drafter._draft_one_channel.
"""

from __future__ import annotations

import re

# "exactly the kind/type/sort of …" and "exactly the direction …" — pure
# intensifier ("exactly") in front of a noun phrase. Dropping the intensifier is
# always grammatical. Capitalization of the following word is preserved when the
# stripped intensifier started a sentence (was itself capitalized).
_EXACTLY_FAMILY = re.compile(
    r"\b(exactly)\s+(the (?:kind|type|sort) of|the direction)\b",
    re.IGNORECASE,
)


def _strip_exactly(match: re.Match) -> str:
    head = match.group(2)
    intensifier = match.group(1)
    # Carry the capital forward only for title-case "Exactly" (a sentence start),
    # not lowercase "exactly" or all-caps "EXACTLY" (mid-sentence emphasis).
    if intensifier[:1].isupper() and intensifier[1:].islower():
        head = head[0].upper() + head[1:]
    return head


# Ordered list of (compiled_pattern, replacement) where replacement is a str or
# a callable. Extend deliberately — each entry must be safe in every context.
_RULES: list[tuple[re.Pattern, object]] = [
    (_EXACTLY_FAMILY, _strip_exactly),
]


def humanize(text: str) -> str:
    """Apply the safe normalization rules. Idempotent; leaves clean text intact."""
    if not text:
        return text
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text
