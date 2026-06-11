"""
src/core/errors.py
Typed exceptions shared across the agent layers. Callers must catch these
types — never string-match on exception messages.
"""

from __future__ import annotations

__all__ = ["NetworkingAgentError", "EmptyLLMResponseError"]


class NetworkingAgentError(Exception):
    """Base class for agent-specific failures."""


class EmptyLLMResponseError(NetworkingAgentError):
    """The LLM returned a response with no usable text content.

    Raised by ``src.agents.shared.call_claude`` when the Anthropic
    response carries an empty content list or a first block without text
    (server-side truncation, policy block, or future API shape changes).
    """
