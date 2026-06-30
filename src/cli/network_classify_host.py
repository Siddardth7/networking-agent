"""
src/cli/network_classify_host.py
Host-token classification bridge (issue #50, two-phase flow): classify a
contact's persona / focus_area / hook_signal on the HOST Claude's tokens — no
Anthropic API key.

Verbs for the end-to-end host-token find (discover → classify → ingest):

  - ``discover <slug> --limit N [--location L]``
    → run the Finder's Apify/Serper discovery (HTTP, no LLM) and emit each raw
      candidate paired with its ``build_classify_context`` grounding, as JSON.
  - ``context --name ... --title ... --snippet ... --company ...``
    → JSON grounding for a single candidate (the per-candidate building block of
      ``discover``); kept for ad-hoc one-off classification.
  - the host model / `networking-classifier` subagent returns
    ``{persona, focus_area, hook_signal}`` per candidate.
  - ``apply --persona P --focus F --hook-signal S``
    → JSON of the CANONICALIZED ``{persona, focus_area, hook_signal}`` after the
      deterministic post-processing (#5 non-engineer focus override + trim).
  - ``ingest <slug>`` (host classifications on stdin)
    → apply each classification, set persona/focus/hook, and save the contacts
      via ``ingest_contacts`` with NO Anthropic client (already LLM-free once
      persona+focus+hook are pre-set).

``context`` / ``apply`` are pure (no DB, no network, no LLM). ``discover`` does
HTTP only; ``ingest`` writes the DB (and optional Hunter/Apollo email lookup).
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agents.finder import (
    _discover,
    _generate_hook,
    _get_or_create_company,
    apply_classification,
    build_classify_context,
    build_discovery_chain,
    build_email_providers,
    ingest_contacts,
)
from src.core.config import load_config
from src.core.db import init_db, with_writer
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


def run_discover(args: argparse.Namespace) -> int:
    """Discover candidates for *slug* and emit each with its classify grounding.

    HTTP only — runs the Finder's Apify→Serper discovery, no LLM. Output is a
    JSON list of ``{"candidate": <ContactCandidate>, "context": <grounding>}``;
    the host classifies each ``context`` and feeds the results back to ``ingest``.
    """
    slug = (args.slug or "").strip()
    if not slug:
        print(json.dumps({"error": "missing slug"}))
        return 1
    cfg = load_config()
    try:
        chain, _ = build_discovery_chain(cfg)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    candidates = _discover(
        chain,
        company=slug.replace("-", " "),
        role_keywords=cfg.finder_role_keywords,
        limit=args.limit,
        location=args.location,
    )
    print(json.dumps([
        {
            "candidate": c.model_dump(mode="json"),
            "context": build_classify_context(c, slug),
        }
        for c in candidates
    ], indent=2))
    return 0


def run_ingest(args: argparse.Namespace) -> int:
    """Save host-classified candidates for *slug*; read classifications on stdin.

    Stdin is a JSON list of ``{"candidate": <ContactCandidate>, "classification":
    {persona, focus_area, hook_signal}}`` (the ``discover`` output paired with the
    host's per-candidate judgment). Each classification is canonicalized
    (``apply_classification``) and its hook generated deterministically
    (``_generate_hook``), so ``ingest_contacts`` runs with NO Anthropic client.
    """
    slug = (args.slug or "").strip()
    if not slug:
        print(json.dumps({"error": "missing slug"}))
        return 1
    try:
        items = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON on stdin: {exc}"}))
        return 1
    if not isinstance(items, list):
        print(json.dumps({"error": "stdin must be a JSON list"}))
        return 1

    prepared: list[ContactCandidate] = []
    for item in items:
        if not isinstance(item, dict) or "candidate" not in item:
            print(json.dumps({"error": "each item needs a 'candidate' object"}))
            return 1
        candidate = ContactCandidate.model_validate(item["candidate"]).model_copy(
            update={"company_slug": slug}
        )
        cls = item.get("classification") or {}
        persona, focus_area, hook_signal = apply_classification(
            cls.get("persona"), cls.get("focus_area"), cls.get("hook_signal")
        )
        # Pre-set the hook (deterministic tiers) so ingest_contacts never reaches
        # for the Haiku classifier — keeps the whole save LLM-free.
        hook = _generate_hook(candidate, hook_signal=hook_signal)
        prepared.append(candidate.model_copy(
            update={"persona": persona, "focus_area": focus_area, "hook": hook}
        ))

    init_db()
    cfg = load_config()
    hunter_provider, apollo_provider = build_email_providers(cfg)
    company_id = _get_or_create_company(slug)
    saved = ingest_contacts(
        prepared,
        company_id,
        slug,
        anthropic_client=None,
        hunter_provider=hunter_provider,
        apollo_provider=apollo_provider,
    )
    # Mirror find_contacts' terminal transition so the company advances NEW→FOUND.
    with with_writer() as conn:
        conn.execute("UPDATE companies SET state = 'FOUND' WHERE id = ?", (company_id,))
    print(json.dumps({
        "ingested": len(saved),
        "contacts": [c.full_name for c in saved],
    }))
    return 0


def run_classify_host(args: argparse.Namespace) -> int:
    """Dispatch the ``discover`` / ``context`` / ``apply`` / ``ingest`` verbs."""
    if args.verb == "discover":
        return run_discover(args)
    if args.verb == "context":
        return run_context(args)
    if args.verb == "ingest":
        return run_ingest(args)
    return run_apply(args)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Host-token classification bridge (#50): discover | context | apply | ingest."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_disc = sub.add_parser("discover", help="Discover candidates + grounding as JSON")
    p_disc.add_argument("slug", help="Company slug")
    p_disc.add_argument("--limit", type=int, default=5)
    p_disc.add_argument("--location", default=None)

    p_ing = sub.add_parser("ingest", help="Save host-classified candidates (stdin JSON)")
    p_ing.add_argument("slug", help="Company slug")

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
