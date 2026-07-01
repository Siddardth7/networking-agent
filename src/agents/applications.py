"""
src/agents/applications.py
Application-mode DB layer (Phase B P2, #59) — the deterministic seam that
persists the `applications` posting entity and links discovered contacts to a
posting via the `application_contacts` join table.

No LLM, no network: pure DB writes over the migration-009 tables. The
host-token `/network-jobs` loop reuses the existing discover → classify →
`ingest_contacts` path for the contacts themselves; this module only adds the
posting row and the posting ↔ contact linkage (with cross-mode dedup — decision
#3: a contact already found via Campaign mode is looked up and linked, never
duplicated).

Traceability: docs/APPLICATION_FEED_INPUT_DESIGN_2026-06-30.md §5, §6.
"""

from __future__ import annotations

from src.core.db import get_connection, with_writer
from src.core.schemas import Application, ContactCandidate
from src.core.slug import canonical_linkedin_url

__all__ = ["upsert_application", "link_contacts"]


def upsert_application(app: Application) -> None:
    """Insert or update the `applications` row for *app* (keyed on job_id).

    Re-running a feed refreshes the posting's mutable fields (a consumer may
    re-score or edit a req) but never resets `status` — the pipeline lifecycle
    column (P3) is owned by the run, not the feed. `created_at` is set once on
    first insert and left alone on update.
    """
    with with_writer() as conn:
        conn.execute(
            """
            INSERT INTO applications
                (job_id, company, company_slug, role_title, function, job_url, score, deadline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                company = excluded.company,
                company_slug = excluded.company_slug,
                role_title = excluded.role_title,
                function = excluded.function,
                job_url = excluded.job_url,
                score = excluded.score,
                deadline = excluded.deadline
            """,
            (
                app.job_id,
                app.company,
                app.company_slug,
                app.role_title,
                app.function,
                app.job_url,
                app.score,
                app.deadline,
            ),
        )


def link_contacts(
    job_id: str, company_id: int, candidates: list[ContactCandidate]
) -> dict:
    """Link each of *candidates* to posting *job_id* via `application_contacts`.

    Resolves each candidate to an existing `contacts` row **within the same
    company** — by canonical LinkedIn URL (#24 cross-source key), then by
    lowercased full name — and inserts an (job_id, contact_id) pair. This gives
    cross-mode dedup for free (decision #3): a contact already present from
    Campaign mode is matched and linked, not duplicated. The pair PK +
    ``INSERT OR IGNORE`` make re-linking idempotent.

    Returns ``{"linked": int, "unresolved": int}`` — ``linked`` counts
    candidates matched to a contact row (surfaced so a posting whose contacts
    didn't persist isn't silently reported as linked; no silent caps).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, linkedin_url, full_name FROM contacts WHERE company_id = ?",
            (company_id,),
        ).fetchall()
    finally:
        conn.close()

    by_url: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for r in rows:
        url = canonical_linkedin_url(r["linkedin_url"])
        if url:
            by_url.setdefault(url, r["id"])
        by_name.setdefault(r["full_name"].strip().lower(), r["id"])

    pairs: list[tuple[str, int]] = []
    unresolved = 0
    for cand in candidates:
        url = canonical_linkedin_url(cand.linkedin_url)
        contact_id = by_url.get(url) if url else None
        if contact_id is None:
            contact_id = by_name.get(cand.full_name.strip().lower())
        if contact_id is None:
            unresolved += 1
            continue
        pairs.append((job_id, contact_id))

    if pairs:
        with with_writer() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO application_contacts (job_id, contact_id) "
                "VALUES (?, ?)",
                pairs,
            )

    return {"linked": len(pairs), "unresolved": unresolved}
