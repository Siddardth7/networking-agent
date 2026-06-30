"""
src/cli/network_critic_host.py
Host-token critic bridge (issue #50): run the Layer-4 critic *judgment* over a
saved draft on the HOST Claude's tokens — no Anthropic API key.

`save_host_draft` deliberately persists a draft with only the deterministic
*safety* gate applied (humanize → hard_check), leaving the critic — the judgment
step — to the host model. This bridge closes that loop:

  1. ``context <draft_id>`` → JSON grounding (recipient, channel, approved facts,
     the draft, the six-dimension rubric, and the hold rule) — no LLM.
  2. the host model / `networking-critic` subagent returns
     ``{specificity, one_ask, tone, grounded_facts, economy, relevance, issues}``.
  3. ``apply <draft_id>`` (scores JSON on stdin) → ``apply_critique`` folds the
     scores into the canonical ``CriticResult`` (recalibrated hold rule + the
     deterministic AI-tell backstop), persists the trace, and downgrades
     OK/SOFT_FLAG → CRITIC_HOLD when held (never touching a HARD_FAIL). Prints
     ``{draft_id, quality_code, passed, reason}``.

The judgment moves to host tokens; the score→verdict decision and persistence
stay in tested Python.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agents.critic import apply_critique, build_critique_context
from src.agents.drafter import build_draft_context
from src.core.db import get_connection, with_writer
from src.core.schemas import Channel

__all__ = ["run_critic_host"]


def _load_draft(draft_id: int) -> dict | None:
    """Load the row the critic needs: body, subject, channel, contact, code."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, contact_id, channel, body, subject, quality_code "
            "FROM drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def run_context(draft_id: int) -> int:
    """Print the critique grounding for one draft as JSON. 1 on missing draft."""
    draft = _load_draft(draft_id)
    if draft is None:
        print(json.dumps({"error": f"draft not found: id={draft_id}"}))
        return 1
    try:
        channel = Channel(draft["channel"])
    except ValueError:
        print(json.dumps({"error": f"unknown channel on draft: {draft['channel']}"}))
        return 1
    # Reuse build_draft_context to rebuild the contact facts + approved facts the
    # drafter saw (same match_achievements call) — no duplicate fact-assembly.
    dctx = build_draft_context(draft["contact_id"], channel)
    if dctx is None:  # pragma: no cover - FK guarantees the contact exists; defensive only
        print(json.dumps({"error": f"contact not found for draft id={draft_id}"}))
        return 1
    source_facts = "\n".join(dctx["approved_facts"]) or None
    ctx = build_critique_context(
        draft["body"] or "", dctx["contact"], channel.value, source_facts, draft["subject"]
    )
    print(json.dumps(ctx, indent=2))
    return 0


def run_apply(draft_id: int, scores_json: str) -> int:
    """Fold host scores into a CriticResult, persist it, and print the verdict."""
    draft = _load_draft(draft_id)
    if draft is None:
        print(json.dumps({"error": f"draft not found: id={draft_id}"}))
        return 1
    try:
        data = json.loads(scores_json)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON on stdin: {exc}"}))
        return 1
    if not isinstance(data, dict):
        print(json.dumps({"error": "stdin must be a JSON object of scores"}))
        return 1

    result = apply_critique(data, draft["body"] or "", draft["subject"])

    # The critic only DOWNGRADES OK/SOFT_FLAG → CRITIC_HOLD (drafter precedence:
    # a critic hold wins over a soft flag). It never upgrades a HARD_FAIL (more
    # severe) and never flips a hold back to OK. A passing critique leaves the
    # safety-gate code as-is.
    current = draft["quality_code"] or "OK"
    new_code = "CRITIC_HOLD" if (not result.passed and current != "HARD_FAIL") else current
    with with_writer() as conn:
        conn.execute(
            "UPDATE drafts SET critic_trace = ?, quality_code = ?, quality_flag = ? WHERE id = ?",
            (result.to_json(), new_code, int(new_code != "OK"), draft_id),
        )
    print(json.dumps({
        "draft_id": draft_id,
        "quality_code": new_code,
        "passed": result.passed,
        "reason": result.reason,
    }))
    return 0


def run_critic_host(args: argparse.Namespace) -> int:
    """Dispatch the ``context`` / ``apply`` verbs."""
    if args.verb == "context":
        return run_context(args.draft_id)
    scores = args.scores if args.scores is not None else sys.stdin.read()
    return run_apply(args.draft_id, scores)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Host-token critic bridge (#50): context | apply."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_ctx = sub.add_parser("context", help="Print critique grounding as JSON")
    p_ctx.add_argument("draft_id", type=int)

    p_apply = sub.add_parser("apply", help="Apply host scores to a draft (JSON on stdin)")
    p_apply.add_argument("draft_id", type=int)
    p_apply.add_argument("--scores", default=None, help="Scores JSON (default: read stdin)")

    sys.exit(run_critic_host(parser.parse_args()))
