"""
src/cli/selection_gate.py
Minimal selection gate UX per DESIGN §8.10.
Numbered contact list, accepts "1,3,4" / "all" / "none", invalid input reprompts.
"""

from __future__ import annotations

from collections.abc import Callable

from src.core.db import get_connection, with_writer

__all__ = ["run_selection_gate"]


def _get_contacts_for_company(company_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, full_name, title, persona, focus_area, linkedin_url, hook,
                   rank_score, rank_reasons
            FROM contacts
            WHERE company_id = ?
            ORDER BY rank_score DESC, id ASC
            """,
            (company_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _print_contact_list(contacts: list[dict]) -> None:
    # Contacts arrive pre-sorted by referral-likelihood rank (#11), best first,
    # so "5 to the right people" are at the top. The score + reasons make the
    # ordering explainable.
    for i, c in enumerate(contacts, start=1):
        name = c.get("full_name") or "Unknown"
        title = c.get("title") or "No title"
        url = c.get("linkedin_url") or ""
        hook = c.get("hook") or "GENERIC"
        score = c.get("rank_score") or 0
        reasons = c.get("rank_reasons") or "no referral signals"
        print(
            f"{i}. [{score}] {name} — {title} (LinkedIn: {url})\n"
            f"     why: {reasons} | hook: {hook}"
        )


def _parse_selection(raw: str, max_index: int) -> list[int] | None:
    """Parse a selection string into a list of 1-based indices.

    Returns an empty list for "none", the full range for "all",
    a validated list for comma-separated numbers, or None on any error.
    """
    normalized = raw.strip().lower()
    if normalized == "none":
        return []
    if normalized == "all":
        return list(range(1, max_index + 1))
    try:
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        if not parts:
            return None
        indices = [int(p) for p in parts]
        if all(1 <= i <= max_index for i in indices):
            return indices
        return None
    except ValueError:
        return None


def run_selection_gate(
    company_id: int,
    _input_fn: Callable[[str], str] | None = None,
) -> list[int]:
    """Present the contact list for *company_id* and collect user selection.

    Returns the list of selected contact DB ids (empty if "none" chosen).
    Selected contacts are marked ``selected=1, state='SELECTED'`` in the DB.
    Company state is updated to ``'SELECTED'`` only when at least one contact is chosen.

    Parameters
    ----------
    company_id:
        Database id of the target company.
    _input_fn:
        Optional callable replacing ``input()`` — used in tests to inject
        deterministic responses without interactive prompts.
    """
    input_fn = _input_fn or input

    contacts = _get_contacts_for_company(company_id)
    if not contacts:
        print("No contacts found for this company. Run /network-find first.")
        return []

    _print_contact_list(contacts)

    while True:
        raw = input_fn('Select contacts to draft for (e.g. "1,3,4" or "all" or "none"): ')
        indices = _parse_selection(raw, len(contacts))
        if indices is None:
            print("Invalid selection. Use comma-separated numbers, 'all', or 'none'.")
            _print_contact_list(contacts)
            continue
        break

    if not indices:
        return []

    selected_ids = [contacts[i - 1]["id"] for i in indices]

    with with_writer() as conn:
        for contact_id in selected_ids:
            conn.execute(
                "UPDATE contacts SET selected = 1, state = 'SELECTED' WHERE id = ?",
                (contact_id,),
            )
        conn.execute(
            "UPDATE companies SET state = 'SELECTED' WHERE id = ?",
            (company_id,),
        )

    return selected_ids
