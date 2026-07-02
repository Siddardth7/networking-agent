"""
src/core/errors.py
Typed exceptions shared across the agent layers. Callers must catch these
types — never string-match on exception messages.
"""

from __future__ import annotations

__all__ = ["NetworkingAgentError", "EmptyLLMResponseError", "ProfileError"]


class NetworkingAgentError(Exception):
    """Base class for agent-specific failures."""


class ProfileError(NetworkingAgentError):
    """A profile file exists but cannot be used (malformed YAML).

    Raised by ``src.core.profile.load_profile`` so a typo in the
    user-editable profile.yaml surfaces as one clear message instead of a
    raw ``yaml.YAMLError`` traceback out of every entry point.
    """


class EmptyLLMResponseError(NetworkingAgentError):
    """The LLM returned a response with no usable text content.

    Raised by ``src.agents.shared.call_claude`` when the Anthropic
    response carries an empty content list or a first block without text
    (server-side truncation, policy block, or future API shape changes).
    """
