"""
src/agents/guardrails.py
Reputation guardrails: regex blocklist applied to every generated draft.
Traceability: DESIGN.md §6 (Reputation guardrails)
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["check_draft", "BLOCKLIST"]

# Blocklist grows over time as new cringe patterns are observed in the wild.
BLOCKLIST: list[str] = [
    "I noticed",
    "I admire",
    "I came across your company",
    "your impressive work",
]


def check_draft(text: str) -> Optional[str]:
    """Return the first blocklist phrase found in *text*, or ``None`` if clean.

    Matching is case-insensitive.  The returned string is the original phrase
    from BLOCKLIST (preserving casing) so callers can include it in the
    anti-phrase nudge sent back to the LLM.
    """
    for phrase in BLOCKLIST:
        if re.search(re.escape(phrase), text, re.IGNORECASE):
            return phrase
    return None
