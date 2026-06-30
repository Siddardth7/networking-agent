"""
src/cli/network_classify_host.py
Host-token classification bridge (issue #50, two-phase flow): classify a
contact's persona / focus_area / hook_signal on the HOST Claude's tokens — no
Anthropic API key.

Phase-2 of the two-phase flow (discover → emit → host classifies → resume):

  1. ``context --name ... --title ... --snippet ... --company ...``
     → JSON grounding (the candidate facts + the persona/focus option semantics)
  2. the host model / `networking-classifier` subagent returns
     ``{persona, focus_area, hook_signal}``
  3. ``apply --persona P --focus F --hook-signal S``
     → JSON of the CANONICALIZED ``{persona, focus_area, hook_signal}`` after the
       deterministic post-processing (#5 non-engineer focus override + trim) — the
       same labels the API path would produce — to feed back into ingest.

Both verbs are pure: no DB, no network, no LLM.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agents.finder import apply_classification, build_classify_context
from src.core.schemas import ContactCandidate

__all__ = ["run_classify_host"]


def run_context(args: argparse.Namespace) -> int:
    """Print the classification grounding for one candidate as JSON."""
    if not (args.name or "").strip():
        print(json.dumps({"error": "missing --name"}))
        return 1
    candidate = ContactCandidate(
        full_name=args.name,
        title=args.title,
        snippet=args.snippet,
        company_slug=args.company or "unknown",
    )
    print(json.dumps(build_classify_context(candidate, args.company or "unknown"), indent=2))
    return 0


def run_apply(args: argparse.Namespace) -> int:
    """Canonicalize a host-provided classification; print the result as JSON."""
    persona, focus_area, hook_signal = apply_classification(
        args.persona, args.focus, args.hook_signal
    )
    print(json.dumps({
        "persona": persona.value,
        "focus_area": focus_area.value,
        "hook_signal": hook_signal,
    }))
    return 0


def run_classify_host(args: argparse.Namespace) -> int:
    """Dispatch the ``context`` / ``apply`` verbs."""
    if args.verb == "context":
        return run_context(args)
    return run_apply(args)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Host-token classification bridge (#50): context | apply."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_ctx = sub.add_parser("context", help="Print classification grounding as JSON")
    p_ctx.add_argument("--name", required=True)
    p_ctx.add_argument("--title", default=None)
    p_ctx.add_argument("--snippet", default=None)
    p_ctx.add_argument("--company", default=None, help="Company slug")

    p_apply = sub.add_parser("apply", help="Canonicalize a host classification")
    p_apply.add_argument("--persona", default=None)
    p_apply.add_argument("--focus", default=None, help="focus_area")
    p_apply.add_argument("--hook-signal", dest="hook_signal", default=None)

    sys.exit(run_classify_host(parser.parse_args()))
