"""
src/cli/network_nextmove_host.py
Host-token next-move bridge (issue #50): draft the reply-aware next move on the
HOST Claude's tokens — no Anthropic API key. Mirrors the drafting bridge.

The host-orchestrated loop:

  1. ``context <contact_id> "<reply>" [--move M] [--channel C] [--outcome O]``
     → JSON grounding (contact facts, the classified move + its instruction,
       voice, fact discipline, channel constraints, the reply)
  2. the host model / `networking-nextmove` subagent writes the reply
  3. ``gate <CHANNEL>`` (body on stdin) → JSON ``{quality_code, body}`` after the
     deterministic humanize + hard_check gate (next moves aren't persisted —
     they're printed for the human to send, same as ``/network-nextmove``)
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agents.drafter import build_next_move_context, gate_host_text
from src.core.schemas import Channel, NextMove

__all__ = ["run_nextmove_host"]

_CHANNELS: dict[str, Channel] = {c.value: c for c in Channel}
_MOVES: dict[str, NextMove] = {m.value: m for m in NextMove}


def run_context(args: argparse.Namespace) -> int:
    """Print the next-move grounding as JSON. 1 on bad move/channel/contact."""
    move = None
    if args.move:
        move = _MOVES.get(args.move.upper())
        if move is None:
            print(json.dumps({"error": f"unknown move: {args.move}", "valid": sorted(_MOVES)}))
            return 1
    channel = None
    if args.channel:
        channel = _CHANNELS.get(args.channel.upper())
        if channel is None:
            print(json.dumps({"error": f"unknown channel: {args.channel}",
                              "valid": sorted(_CHANNELS)}))
            return 1
    if not (args.reply or "").strip():
        print(json.dumps({"error": "empty reply — provide the contact's reply text"}))
        return 1

    ctx = build_next_move_context(
        args.contact_id, args.reply, channel=channel, outcome=args.outcome, move=move
    )
    if ctx is None:
        print(json.dumps({"error": f"contact not found: id={args.contact_id}"}))
        return 1
    print(json.dumps(ctx, indent=2))
    return 0


def run_gate(channel_name: str, body: str) -> int:
    """Run the deterministic gate on a host-written reply; print result JSON."""
    channel = _CHANNELS.get(channel_name.upper())
    if channel is None:
        print(json.dumps({"error": f"unknown channel: {channel_name}",
                          "valid": sorted(_CHANNELS)}))
        return 1
    if not body.strip():
        print(json.dumps({"error": "empty body — nothing to gate"}))
        return 1
    result = gate_host_text(body, channel)
    print(json.dumps({"quality_code": result["quality_code"], "body": result["body"]}))
    return 0


def run_nextmove_host(args: argparse.Namespace) -> int:
    """Dispatch the ``context`` / ``gate`` verbs."""
    if args.verb == "context":
        return run_context(args)
    body = args.body if args.body is not None else sys.stdin.read()
    return run_gate(args.channel, body)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Host-token next-move bridge (#50): context | gate."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_ctx = sub.add_parser("context", help="Print next-move grounding as JSON")
    p_ctx.add_argument("contact_id", type=int)
    p_ctx.add_argument("reply", help="The contact's reply text (verbatim)")
    p_ctx.add_argument("--move", default=None, help="Force move: " + ", ".join(sorted(_MOVES)))
    p_ctx.add_argument("--channel", default=None, help="Force channel")
    p_ctx.add_argument("--outcome", default=None, help="Recorded outcome (e.g. POC)")

    p_gate = sub.add_parser("gate", help="Gate a host-written reply (body on stdin)")
    p_gate.add_argument("channel", help="One of: " + ", ".join(sorted(_CHANNELS)))
    p_gate.add_argument("--body", default=None, help="Body (default: read stdin)")

    sys.exit(run_nextmove_host(parser.parse_args()))
