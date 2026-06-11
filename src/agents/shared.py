"""
src/agents/shared.py
Layer 6 — single source of truth for logic shared by the Drafter (first
pass) and the Dispatcher (REVISE pass).

Before this module existed, ``drafter.py`` and ``dispatch.py`` each
defined ``_CHANNEL_CONSTRAINTS``, ``_parse_email_body_subject``, and
their own Claude call wrappers — already divergent (e.g. LinkedIn cap
was 200 in drafter, 300 in dispatch). Two sources of truth guaranteed
future drift; this module collapses them.

Traceability: DRAFTER_ROOT_CAUSE_AUDIT.md Layer 6.
"""

from __future__ import annotations

import concurrent.futures
from typing import Optional

from src.core.config import HAIKU_MODEL
from src.core.errors import EmptyLLMResponseError
from src.core.schemas import Channel

__all__ = [
    "CHANNEL_CONSTRAINTS",
    "parse_email_body_subject",
    "call_claude",
    "call_claude_with_timeout",
    "DEFAULT_TIMEOUT_SECONDS",
]


# Default LLM-call timeout in seconds. Hosts can override per-call.
DEFAULT_TIMEOUT_SECONDS: float = 90.0


# Channel-specific instructions appended to every prompt. Hard limits
# here are deliberately consistent with ``guardrails.hard_check`` so the
# prompt and the gate agree on what "too long" means.
CHANNEL_CONSTRAINTS: dict[Channel, str] = {
    Channel.LINKEDIN_CONNECTION: (
        "Write ONLY the LinkedIn connection request note. "
        "Hard limit: 200 characters total (including spaces) — LinkedIn free-account cap. "
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
        "Output format — first line: 'Subject: <subject text>', "
        "then a blank line, then the email body."
    ),
}


def parse_email_body_subject(text: str) -> tuple[str, Optional[str]]:
    """Split a COLD_EMAIL response into ``(body, subject)``.

    Expected format: ``"Subject: <text>\\n\\n<body>"``. When the leading
    ``Subject:`` line is absent the whole text is treated as the body
    and the subject is ``None``.
    """
    lines = text.strip().split("\n")
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][len("subject:"):].strip()
        body = "\n".join(lines[1:]).lstrip("\n").strip()
        return body, subject
    return text.strip(), None


def call_claude(prompt: str, anthropic_client, model: str = HAIKU_MODEL,
                max_tokens: int = 600) -> str:
    """Single Anthropic call. Returns stripped first-content-block text.

    Inputs: prompt text, an Anthropic client (or test double), model id,
    token cap. Output: the first content block's text, stripped. Side
    effects: one network call. Raises ``EmptyLLMResponseError`` when the
    response has no content blocks or the first block carries no text
    (AUDIT-A20) instead of an opaque IndexError/AttributeError.
    """
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.content:
        raise EmptyLLMResponseError("LLM response contained no content blocks")
    text = getattr(response.content[0], "text", None)
    if not isinstance(text, str):
        raise EmptyLLMResponseError("LLM response first block has no text")
    return text.strip()


def call_claude_with_timeout(
    prompt: str,
    anthropic_client,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    model: str = HAIKU_MODEL,
    max_tokens: int = 600,
) -> str:
    """:func:`call_claude` with a hard timeout.

    Raises ``TimeoutError`` if the call exceeds *timeout* seconds. Used by
    ``dispatch.dispatch_revision`` where a hung Anthropic request would
    block the interactive marketer loop.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            call_claude, prompt, anthropic_client, model, max_tokens,
        )
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"LLM call exceeded {timeout}s timeout")
