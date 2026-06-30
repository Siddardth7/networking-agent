"""
src/cli/network_run_host.py
Host-token run planner (issue #50): the deterministic driver that lets the host
model orchestrate the FULL pipeline on its own tokens — no Anthropic API key.

The per-step bridges already exist (discover/classify/ingest, draft, critic). This
is the glue that ties them into a default end-to-end run: given a company slug it
reports the pipeline state and the work items for the NEXT host action, so the
host model (running `/network-run`) knows which bridge to call next and over which
ids. Read-only — it never mutates state or calls an LLM; the host does the
judgment via the subagents, Python just says what's next.

  ``plan <slug>`` → ``{company, state, next, items}`` where ``next`` is one of
  ``discover | select | draft | approve | done`` and ``items`` carries the ids/rows
  that step operates on.
  ``select <slug> --ids 1,3,5`` → mark those contacts SELECTED (and the company
  SELECTED) so the run is resumable and ``/network-status`` is accurate — the one
  state write the host's selection step needs (the interactive selection gate is
  the API path's equivalent).
"""

from __future__ import annotations

import argparse
import json
import sys

from src.core.db import get_connection, init_db, with_writer

__all__ = ["apply_selection", "build_run_plan", "run_run_host"]

# Company pipeline state → the next host action. Mirrors the run_pipeline state
# machine (orchestrator.py) but advises the host loop instead of executing it.
_NEXT_BY_STATE: dict[str, str] = {
    "NEW": "discover",
    "FOUND": "select",
    "SELECTED": "draft",
    "DRAFTED": "approve",
    "APPROVED": "done",
}


def _company(slug: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, slug, name, state FROM companies WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _selectable_contacts(company_id: int) -> list[dict]:
    """All contacts for the company, rank-ordered — the selection-gate set."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, full_name, title, persona, focus_area, hook, rank_score, rank_reasons "
            "FROM contacts WHERE company_id = ? ORDER BY rank_score DESC, id ASC",
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _selected_contacts(company_id: int) -> list[dict]:
    """Contacts in SELECTED state — the ones still to draft (×3 channels each)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, full_name FROM contacts "
            "WHERE company_id = ? AND state = 'SELECTED' ORDER BY id",
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def build_run_plan(slug: str) -> dict:
    """Deterministic next-step plan for the host-token ``/network-run`` loop. No LLM.

    Returns ``{company, state, next, items}``. An unknown slug (no row yet) plans a
    fresh ``discover``. ``items`` is the selectable contacts at ``select``, the
    SELECTED contacts at ``draft``, and empty otherwise. Read-only — never mutates.
    """
    company = _company(slug)
    if company is None:
        return {"company": None, "state": "NEW", "next": "discover", "items": []}

    state = company["state"]
    next_action = _NEXT_BY_STATE.get(state, "unknown")
    if next_action == "select":
        items: list[dict] = _selectable_contacts(company["id"])
    elif next_action == "draft":
        items = _selected_contacts(company["id"])
    else:
        items = []
    return {"company": company, "state": state, "next": next_action, "items": items}


def apply_selection(slug: str, contact_ids: list[int]) -> dict:
    """Mark *contact_ids* SELECTED (and the company SELECTED). Mirrors the
    selection gate's writes (selection_gate.py) for the host-token path. Only
    contacts belonging to *slug* are touched. Returns ``{selected, company}``.
    """
    company = _company(slug)
    if company is None:
        return {"error": f"company not found: {slug}"}
    with with_writer() as conn:
        applied: list[int] = []
        for cid in contact_ids:
            cur = conn.execute(
                "UPDATE contacts SET selected = 1, state = 'SELECTED' "
                "WHERE id = ? AND company_id = ?",
                (cid, company["id"]),
            )
            if cur.rowcount > 0:
                applied.append(cid)
        if applied:
            conn.execute(
                "UPDATE companies SET state = 'SELECTED' WHERE id = ?", (company["id"],)
            )
    return {"selected": applied, "company": slug}


def _parse_ids(raw: str | None) -> list[int]:
    """Parse a ``1,3,5`` id list; skip blanks/non-ints rather than crash."""
    out: list[int] = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.append(int(tok))
    return out


def run_plan(slug: str) -> int:
    """Print the run plan for *slug* as JSON. 1 on a blank slug."""
    if not (slug or "").strip():
        print(json.dumps({"error": "missing slug"}))
        return 1
    init_db()
    print(json.dumps(build_run_plan(slug), indent=2))
    return 0


def run_select(slug: str, ids_raw: str | None) -> int:
    """Mark the chosen contacts SELECTED; print the result JSON. 1 on bad input."""
    if not (slug or "").strip():
        print(json.dumps({"error": "missing slug"}))
        return 1
    ids = _parse_ids(ids_raw)
    if not ids:
        print(json.dumps({"error": "no valid ids — pass --ids 1,3,5"}))
        return 1
    init_db()
    result = apply_selection(slug, ids)
    print(json.dumps(result))
    return 1 if "error" in result else 0


def run_run_host(args: argparse.Namespace) -> int:
    """Dispatch the ``plan`` / ``select`` verbs."""
    if args.verb == "select":
        return run_select(args.slug, args.ids)
    return run_plan(args.slug)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Host-token run planner (#50): plan | select."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p_plan = sub.add_parser("plan", help="Print the next host-run step + items as JSON")
    p_plan.add_argument("slug", help="Company slug")

    p_sel = sub.add_parser("select", help="Mark contacts SELECTED for the run")
    p_sel.add_argument("slug", help="Company slug")
    p_sel.add_argument("--ids", default=None, help="Comma-separated contact ids, e.g. 1,3,5")

    sys.exit(run_run_host(parser.parse_args()))
