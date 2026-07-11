"""
src/cli/network_draft_host.py
Host-token drafting bridge (issue #50): expose the deterministic drafting seam
as JSON-in/JSON-out CLI verbs so the host model (running a /network-* command)
can drive the writing on HOST tokens — no Anthropic API key.

The host-orchestrated loop per contact × channel:

  1. ``context <contact_id> <CHANNEL>``  → JSON grounding (facts, voice, limits)
  2. the host model / `networking-drafter` subagent writes the message
  3. ``save <contact_id> <CHANNEL> [--subject S]`` (body on stdin) → JSON
     ``{draft_id, quality_code, body, subject}`` after the deterministic gate

Both verbs are pure of the Anthropic client; the LLM step happens in between, on
the host model's tokens.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agents.drafter import build_draft_context, save_host_draft
from src.cli import read_stdin_text
from src.core.schemas import Channel

__all__ = ["run_draft_host"]

_CHANNELS: dict[str, Channel] = {c.value: c for c in Channel}


def _resolve_channel(name: str) -> Channel | None:
    return _CHANNELS.get(name.upper())


def run_context(contact_id: int, channel_name: str, job_id: str | None = None) -> int:
    """Print the build_draft_context grounding as JSON. 1 on bad channel/contact.

    *job_id* (Application mode, #60) adds the posting's role_title + job_url to the
    grounding so the note names the specific role.
    """
    channel = _resolve_channel(channel_name)
    if channel is None:
        print(json.dumps({"error": f"unknown channel: {channel_name}",
                          "valid": sorted(_CHANNELS)}))
        return 1
    ctx = build_draft_context(contact_id, channel, job_id=job_id or None)
    if ctx is None:
        print(json.dumps({"error": f"contact not found: id={contact_id}"}))
        return 1
    print(json.dumps(ctx, indent=2))
    return 0


def run_save(contact_id: int, channel_name: str, body: str, subject: str | None) -> int:
    """Gate + persist a host-written draft; print the result JSON. 1 on bad input."""
    channel = _resolve_channel(channel_name)
    if channel is None:
        print(json.dumps({"error": f"unknown channel: {channel_name}",
                          "valid": sorted(_CHANNELS)}))
        return 1
    if not body.strip():
        print(json.dumps({"error": "empty body — nothing to save"}))
        return 1
    result = save_host_draft(contact_id, channel, body, subject)
    print(json.dumps(result))
    return 0


def run_draft_host(args: argparse.Namespace) -> int:
    """Dispatch the ``context`` / ``save`` verbs."""
    if args.verb == "context":
        return run_context(args.contact_id, args.channel, getattr(args, "job_id", None))
    # save: body comes from --body or, by default, stdin (safe for newlines).
    body = args.body if args.body is not None else read_stdin_text()
    return run_save(args.contact_id, args.channel, body, args.subject)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Host-token drafting bridge (#50): context | save."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_ctx = sub.add_parser("context", help="Print draft grounding as JSON")
    p_ctx.add_argument("contact_id", type=int)
    p_ctx.add_argument("channel", help="One of: " + ", ".join(sorted(_CHANNELS)))
    p_ctx.add_argument("--job-id", dest="job_id", default=None,
                       help="Application-mode posting id (#60): adds role_title + job_url")

    p_save = sub.add_parser("save", help="Gate + persist a host-written draft (body on stdin)")
    p_save.add_argument("contact_id", type=int)
    p_save.add_argument("channel", help="One of: " + ", ".join(sorted(_CHANNELS)))
    p_save.add_argument("--subject", default=None, help="Subject line (cold email)")
    p_save.add_argument("--body", default=None, help="Draft body (default: read stdin)")

    sys.exit(run_draft_host(parser.parse_args()))
