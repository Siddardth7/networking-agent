"""
src/cli/network_jobs_host.py
Application-mode bridge (Phase B P2, #59) — the deterministic verbs the
host-token `/network-jobs` loop drives to turn a scored job feed into
per-posting referral candidates.

Verbs (the discover → classify → ingest of the contacts themselves reuse the
existing `network_classify_host` bridge; this adds the posting entity + linkage):

  - ``plan <feed.json>``
    → parse the application-feed (P1 parser), UPSERT each `applications` row,
      and emit one work item per posting for the host loop:
      ``{job_id, company, company_slug, role_title, location, target_keywords,
      precaptured_contacts}`` plus the parser's drop/error ``report`` (no silent
      caps). Postings the parser rejected are counted in the report, not hidden.
  - ``link <job_id> <slug>`` (candidates on stdin)
    → link the posting's discovered contacts (matched to existing `contacts`
      rows by canonical URL / name — cross-mode dedup, decision #3) into
      `application_contacts`. Idempotent.

``plan`` writes the `applications` rows and parses the feed; ``link`` writes the
join table. Neither does any LLM or network work.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agents.application_feed import ApplicationFeedError, parse_application_feed
from src.agents.applications import link_contacts, upsert_application
from src.agents.finder import _get_or_create_company
from src.core.db import init_db
from src.core.schemas import ContactCandidate

__all__ = ["run_jobs_host"]


def run_plan(args: argparse.Namespace) -> int:
    """Parse *feed*, persist each posting row, and emit host-loop work items."""
    try:
        apps, report = parse_application_feed(args.feed)
    except (ApplicationFeedError, OSError) as exc:
        print(json.dumps({"error": f"parse failed: {exc}"}))
        return 1

    init_db()
    postings = []
    for app in apps:
        upsert_application(app)
        postings.append({
            "job_id": app.job_id,
            "company": app.company,
            "company_slug": app.company_slug,
            "role_title": app.role_title,
            "location": app.location,
            "target_keywords": app.target_keywords,
            "precaptured_contacts": len(app.contacts),
        })
    print(json.dumps({"postings": postings, "report": report}, indent=2))
    return 0


def run_link(args: argparse.Namespace) -> int:
    """Link a posting's discovered contacts (stdin) to *job_id* under *slug*.

    Stdin is a JSON list of candidate objects, or ``discover``-shaped
    ``{"candidate": {…}}`` items — either is accepted so the caller can pipe the
    raw discover output straight through.
    """
    job_id = (args.job_id or "").strip()
    slug = (args.slug or "").strip()
    if not job_id or not slug:
        print(json.dumps({"error": "missing job_id or slug"}))
        return 1
    try:
        items = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON on stdin: {exc}"}))
        return 1
    if not isinstance(items, list):
        print(json.dumps({"error": "stdin must be a JSON list"}))
        return 1

    candidates: list[ContactCandidate] = []
    for item in items:
        raw = item.get("candidate") if isinstance(item, dict) and "candidate" in item else item
        try:
            candidates.append(ContactCandidate.model_validate(raw))
        except Exception as exc:  # malformed candidate → surface, don't silently drop
            print(json.dumps({"error": f"invalid candidate: {exc}"}))
            return 1

    init_db()
    company_id = _get_or_create_company(slug)
    result = link_contacts(job_id, company_id, candidates)
    print(json.dumps({"job_id": job_id, **result}))
    return 0


def run_jobs_host(args: argparse.Namespace) -> int:
    """Dispatch the ``plan`` / ``link`` verbs."""
    if args.verb == "plan":
        return run_plan(args)
    return run_link(args)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Application-mode bridge (#59): plan a job feed | link contacts to a posting."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_plan = sub.add_parser("plan", help="Parse feed, persist postings, emit work items")
    p_plan.add_argument("feed", help="Path to the application-feed JSON file")

    p_link = sub.add_parser("link", help="Link discovered contacts (stdin) to a posting")
    p_link.add_argument("job_id", help="The posting's job_id")
    p_link.add_argument("slug", help="Company slug the contacts were ingested under")

    sys.exit(run_jobs_host(parser.parse_args()))
