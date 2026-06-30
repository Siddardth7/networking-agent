"""
src/cli/network_nextmove.py
Draft the reply-aware next move for a contact who replied (issue #19, A8).

'They replied — now what?' Paste the reply; this drafts the single best next
move (take the intro, ask the sponsorship question, propose a chat, or ask for
a referral) in voice, through the same hard_check + critic gates as a cold
draft. The move type is classified from the reply; override it with --move when
you read the reply differently.
"""

from __future__ import annotations

import argparse
import sys

from src.agents.drafter import draft_next_move
from src.core.config import get_anthropic_client
from src.core.schemas import Channel, NextMove

__all__ = ["run_nextmove"]

_CHANNEL_BY_NAME = {c.value: c for c in Channel}
_MOVE_BY_NAME = {m.value: m for m in NextMove}


def run_nextmove(args: argparse.Namespace, anthropic_client=None) -> int:
    """Draft + print the gated next move. *anthropic_client* injectable for tests."""
    reply = (args.reply or "").strip()
    if not reply:
        print("Provide the contact's reply text (the message you're responding to).")
        return 1

    move = None
    if getattr(args, "move", None):
        move = _MOVE_BY_NAME.get(args.move.upper())
        if move is None:
            print(f"Invalid --move: {args.move!r}. One of: " + ", ".join(sorted(_MOVE_BY_NAME)))
            return 1

    channel = None
    if getattr(args, "channel", None):
        channel = _CHANNEL_BY_NAME.get(args.channel.upper())
        if channel is None:
            print(
                f"Invalid --channel: {args.channel!r}. One of: "
                + ", ".join(sorted(_CHANNEL_BY_NAME))
            )
            return 1

    if anthropic_client is None:
        anthropic_client = get_anthropic_client()

    draft = draft_next_move(
        args.contact_id,
        reply,
        anthropic_client=anthropic_client,
        channel=channel,
        outcome=getattr(args, "outcome", None),
        move=move,
    )
    if draft is None:
        print(f"Contact not found: id={args.contact_id}")
        return 1

    print(f"Next move: {draft.move.value}  [{draft.quality_code}]")
    if draft.subject:
        print(f"Subject: {draft.subject}")
    print("---")
    print(draft.body)
    return 0


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Draft the reply-aware next move for a contact who replied (#19)."
    )
    parser.add_argument("contact_id", type=int, help="Contact DB id")
    parser.add_argument("reply", help="The contact's reply text (verbatim)")
    parser.add_argument(
        "--move",
        default=None,
        help="Force the next-move type: " + ", ".join(sorted(_MOVE_BY_NAME)),
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Force the channel: " + ", ".join(sorted(_CHANNEL_BY_NAME)),
    )
    parser.add_argument(
        "--outcome", default=None, help="Recorded outcome (e.g. POC) to bias classification"
    )
    sys.exit(run_nextmove(parser.parse_args()))
