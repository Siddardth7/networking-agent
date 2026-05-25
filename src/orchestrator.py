"""
src/orchestrator.py
State-machine orchestrator for /network-run.
Traceability: PLAN.md Phase 9, DESIGN.md §8.11
"""

from __future__ import annotations

import sys
from typing import Optional

from src.core.db import get_connection, with_writer, init_db

__all__ = ["run_pipeline"]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_or_create_company(slug: str) -> dict:
    """Return the companies row for *slug*, creating a NEW entry if absent.

    The name is derived from the slug (hyphens → spaces, title-cased).
    """
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, slug, name, state FROM companies WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row is not None:
            return dict(row)
    finally:
        conn.close()

    name = slug.replace("-", " ").title()
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, 'NEW')",
            (slug, name),
        )
        company_id = cursor.lastrowid
    return {"id": company_id, "slug": slug, "name": name, "state": "NEW"}


def _get_selected_contact_ids(company_id: int) -> list[int]:
    """Return IDs of contacts still in SELECTED state (not yet drafted)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM contacts WHERE company_id = ? AND state = 'SELECTED' ORDER BY id",
            (company_id,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(
    company_slug: str,
    anthropic_client=None,
    # Injectable dependencies — real modules resolved lazily; tests pass stubs
    _run_checks=None,
    _find_contacts=None,
    _run_selection_gate=None,
    _draft_for_contacts=None,
    _run_approval_loop=None,
    _write_artifact=None,
) -> None:
    """Run the full networking pipeline for *company_slug*, resuming from state.

    State-machine dispatch per company state:

    NEW
        Preflight → Finder → Selection gate → Drafter → Marketer → Artifact
    FOUND
        Selection gate → Drafter → Marketer → Artifact
    SELECTED
        Drafter (contacts still in SELECTED state only) → Marketer → Artifact
    DRAFTED
        Marketer (with resume message) → Artifact
    APPROVED
        No-op — outreach_log entries are pending manual send

    Parameters
    ----------
    company_slug:
        Target company identifier (DB slug / URL-friendly name).
    anthropic_client:
        Optional pre-built Anthropic client passed through to sub-agents.
    _run_checks, _find_contacts, _run_selection_gate, _draft_for_contacts,
    _run_approval_loop, _write_artifact:
        Step overrides for unit testing. ``None`` → real module is imported.
    """
    # Resolve real implementations lazily so tests can stub without side-imports
    if _run_checks is None:
        from src.cli.network_check import run_checks as _rc
        _run_checks = _rc
    if _find_contacts is None:
        from src.agents.finder import find_contacts as _fc
        _find_contacts = _fc
    if _run_selection_gate is None:
        from src.cli.selection_gate import run_selection_gate as _sg
        _run_selection_gate = _sg
    if _draft_for_contacts is None:
        from src.agents.drafter import draft_for_contacts as _dc
        _draft_for_contacts = _dc
    if _run_approval_loop is None:
        from src.agents.marketer import run_approval_loop as _al
        _run_approval_loop = _al
    if _write_artifact is None:
        from src.agents.artifact_writer import write_artifact as _wa
        _write_artifact = _wa

    company = _get_or_create_company(company_slug)
    company_id: int = company["id"]
    state: str = company["state"]

    if state != "NEW":
        print(f"Resuming pipeline for {company['name']} from state={state}...")

    # ---- APPROVED --------------------------------------------------------
    if state == "APPROVED":
        print("Nothing to do; outreach_log entries pending send.")
        return

    # ---- DRAFTED ---------------------------------------------------------
    if state == "DRAFTED":
        _run_approval_loop(company_id)
        _write_artifact(company_id)
        return

    # ---- SELECTED --------------------------------------------------------
    if state == "SELECTED":
        contact_ids = _get_selected_contact_ids(company_id)
        if contact_ids:
            _draft_for_contacts(contact_ids, anthropic_client)
        _run_approval_loop(company_id)
        _write_artifact(company_id)
        return

    # ---- FOUND -----------------------------------------------------------
    if state == "FOUND":
        selected_ids = _run_selection_gate(company_id)
        if selected_ids:
            _draft_for_contacts(selected_ids, anthropic_client)
        _run_approval_loop(company_id)
        _write_artifact(company_id)
        return

    # ---- NEW (full pipeline) ---------------------------------------------
    exit_code = _run_checks()
    if exit_code != 0:
        print(
            "Preflight checks failed. Fix the errors above and retry /network-run.",
            file=sys.stderr,
        )
        return

    _find_contacts(company_slug, anthropic_client=anthropic_client)

    # Re-fetch company_id after Finder (Finder may have created the row if slug
    # didn't exist yet; slug is UNIQUE so the same row is returned)
    company = _get_or_create_company(company_slug)
    company_id = company["id"]

    selected_ids = _run_selection_gate(company_id)
    if selected_ids:
        _draft_for_contacts(selected_ids, anthropic_client)
    _run_approval_loop(company_id)
    _write_artifact(company_id)
