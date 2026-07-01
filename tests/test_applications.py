"""
tests/test_applications.py
Tests for src/agents/applications.py — Application-mode DB layer (#59).
Hermetic: real SQLite at a tmp path (migration-009 tables), no network/LLM.
"""

from __future__ import annotations

import pytest

from src.agents.applications import (
    aggregate_referral_status,
    all_statuses,
    link_contacts,
    posting_status,
    upsert_application,
)
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import Application, ContactCandidate


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _company(slug="joby-aviation", name="Joby Aviation") -> int:
    with with_writer() as conn:
        cur = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, 'FOUND')", (slug, name)
        )
        return int(cur.lastrowid)


def _contact(
    company_id: int, name: str, url: str | None = None,
    state: str = "NEW", outcome: str = "NONE",
) -> int:
    with with_writer() as conn:
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, linkedin_url, state, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, name, url, state, outcome),
        )
        return int(cur.lastrowid)


def _app(**over) -> Application:
    base = {"job_id": "ja-1", "company": "Joby Aviation", "role_title": "Quality Engineer"}
    base.update(over)
    return Application(**base)


# ---------------------------------------------------------------------------
# upsert_application
# ---------------------------------------------------------------------------


def test_upsert_inserts_row_status_new(tmp_db) -> None:
    upsert_application(_app(function="QUALITY", score=88, job_url="https://x/1"))
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM applications WHERE job_id = 'ja-1'").fetchone()
    finally:
        conn.close()
    assert row["company_slug"] == "joby-aviation"  # derived by the model
    assert row["role_title"] == "Quality Engineer"
    assert row["function"] == "QUALITY"
    assert row["score"] == 88
    assert row["status"] == "NEW"


def test_upsert_updates_fields_but_not_status(tmp_db) -> None:
    upsert_application(_app(role_title="Quality Engineer", score=70))
    # simulate the pipeline having advanced status, then re-feed with edits
    with with_writer() as conn:
        conn.execute("UPDATE applications SET status = 'searching' WHERE job_id = 'ja-1'")
    upsert_application(_app(role_title="Senior Quality Engineer", score=91))
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM applications WHERE job_id = 'ja-1'").fetchone()
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
    finally:
        conn.close()
    assert count == 1  # upsert, not duplicate
    assert row["role_title"] == "Senior Quality Engineer"
    assert row["score"] == 91
    assert row["status"] == "searching"  # lifecycle column untouched by the feed


# ---------------------------------------------------------------------------
# link_contacts
# ---------------------------------------------------------------------------


def test_link_by_linkedin_url(tmp_db) -> None:
    cid = _company()
    _contact(cid, "Jane Doe", "https://www.linkedin.com/in/jane/")
    upsert_application(_app())
    # candidate URL differs only by scheme/www/query → canonical match (#24)
    cand = ContactCandidate(
        full_name="Jane D.", company_slug="joby-aviation",
        linkedin_url="http://linkedin.com/in/jane?utm=x",
    )
    result = link_contacts("ja-1", cid, [cand])
    assert result == {"linked": 1, "unresolved": 0}
    conn = get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM application_contacts WHERE job_id = 'ja-1'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n == 1


def test_link_by_name_when_no_url(tmp_db) -> None:
    cid = _company()
    _contact(cid, "Jane Doe", None)
    upsert_application(_app())
    cand = ContactCandidate(full_name="  jane doe ", company_slug="joby-aviation")
    result = link_contacts("ja-1", cid, [cand])
    assert result == {"linked": 1, "unresolved": 0}


def test_link_unresolved_when_no_match(tmp_db) -> None:
    cid = _company()
    _contact(cid, "Someone Else", "https://linkedin.com/in/else")
    upsert_application(_app())
    cand = ContactCandidate(full_name="Ghost", company_slug="joby-aviation")
    result = link_contacts("ja-1", cid, [cand])
    assert result == {"linked": 0, "unresolved": 1}


def test_link_is_idempotent(tmp_db) -> None:
    cid = _company()
    _contact(cid, "Jane Doe", "https://linkedin.com/in/jane")
    upsert_application(_app())
    cand = ContactCandidate(
        full_name="Jane Doe", company_slug="joby-aviation",
        linkedin_url="https://linkedin.com/in/jane",
    )
    link_contacts("ja-1", cid, [cand])
    link_contacts("ja-1", cid, [cand])  # re-run
    conn = get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM application_contacts WHERE job_id = 'ja-1'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n == 1  # OR IGNORE on the (job_id, contact_id) PK


def test_link_cross_mode_dedup_links_existing(tmp_db) -> None:
    """Decision #3: a contact already present (Campaign mode) is linked, not duped."""
    cid = _company()
    existing = _contact(cid, "Jane Doe", "https://linkedin.com/in/jane")
    upsert_application(_app())
    cand = ContactCandidate(
        full_name="Jane Doe", company_slug="joby-aviation",
        linkedin_url="https://linkedin.com/in/jane",
    )
    link_contacts("ja-1", cid, [cand])
    conn = get_connection()
    try:
        linked_id = conn.execute(
            "SELECT contact_id FROM application_contacts WHERE job_id = 'ja-1'"
        ).fetchone()["contact_id"]
        contact_count = conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"]
    finally:
        conn.close()
    assert linked_id == existing
    assert contact_count == 1  # no duplicate contact row created


def test_link_empty_candidates_writes_nothing(tmp_db) -> None:
    cid = _company()
    upsert_application(_app())
    result = link_contacts("ja-1", cid, [])
    assert result == {"linked": 0, "unresolved": 0}


# ---------------------------------------------------------------------------
# aggregate_referral_status (pure) + posting_status / all_statuses (#60)
# ---------------------------------------------------------------------------


def test_aggregate_empty_is_none():
    assert aggregate_referral_status([]) == {"status": "none", "contacts": 0, "sponsorship": None}


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([{"state": "NEW", "outcome": "NONE"}], "searching"),
        ([{"state": "SENT", "outcome": "NONE"}], "reached"),
        ([{"state": "SENT", "outcome": "REPLIED"}], "conversation"),
        ([{"state": "SENT", "outcome": "DECLINED"}], "conversation"),
        ([{"state": "SENT", "outcome": "POC"}], "referred"),
        # best-across-contacts: one referred beats the rest still searching
        ([{"state": "NEW", "outcome": "NONE"}, {"state": "SENT", "outcome": "POC"}], "referred"),
    ],
)
def test_aggregate_best_rung(rows, expected):
    assert aggregate_referral_status(rows)["status"] == expected


def test_aggregate_sponsorship_surfaced():
    yes = aggregate_referral_status([{"state": "SENT", "outcome": "SPONSORSHIP_YES"}])
    assert yes["status"] == "conversation" and yes["sponsorship"] == "YES"
    no = aggregate_referral_status([{"state": "SENT", "outcome": "SPONSORSHIP_NO"}])
    assert no["sponsorship"] == "NO"
    # YES beats NO when both appear
    both = aggregate_referral_status([
        {"state": "SENT", "outcome": "SPONSORSHIP_NO"},
        {"state": "SENT", "outcome": "SPONSORSHIP_YES"},
    ])
    assert both["sponsorship"] == "YES"


def test_posting_status_unknown_is_none(tmp_db):
    assert posting_status("nope") is None


def test_posting_status_rolls_up_linked_contacts(tmp_db):
    cid = _company()
    c_search = _contact(cid, "A", "https://linkedin.com/in/a", state="NEW", outcome="NONE")
    c_ref = _contact(cid, "B", "https://linkedin.com/in/b", state="SENT", outcome="POC")
    upsert_application(_app(role_title="Quality Engineer"))
    with with_writer() as conn:
        conn.executemany(
            "INSERT INTO application_contacts (job_id, contact_id) VALUES ('ja-1', ?)",
            [(c_search,), (c_ref,)],
        )
    status = posting_status("ja-1")
    assert status["job_id"] == "ja-1"
    assert status["company"] == "Joby Aviation"
    assert status["role_title"] == "Quality Engineer"
    assert status["status"] == "referred"  # best across the two
    assert status["contacts"] == 2


def test_posting_status_no_contacts_is_none_rung(tmp_db):
    _company()
    upsert_application(_app())
    status = posting_status("ja-1")
    assert status["status"] == "none"
    assert status["contacts"] == 0


def test_all_statuses_covers_every_posting(tmp_db):
    cid = _company()
    upsert_application(_app(job_id="ja-1"))
    upsert_application(_app(job_id="ja-2", role_title="Structures"))
    c = _contact(cid, "A", "https://linkedin.com/in/a", state="SENT", outcome="REPLIED")
    with with_writer() as conn:
        conn.execute(
            "INSERT INTO application_contacts (job_id, contact_id) VALUES ('ja-1', ?)", (c,)
        )
    out = all_statuses()
    by_id = {s["job_id"]: s for s in out}
    assert by_id["ja-1"]["status"] == "conversation"
    assert by_id["ja-2"]["status"] == "none"


def test_link_two_postings_share_contact(tmp_db) -> None:
    """A contact can back >1 req at one company — the join-not-FK reason."""
    cid = _company()
    _contact(cid, "Jane Doe", "https://linkedin.com/in/jane")
    upsert_application(_app(job_id="ja-1"))
    upsert_application(_app(job_id="ja-2", role_title="Structures Eng"))
    cand = ContactCandidate(
        full_name="Jane Doe", company_slug="joby-aviation",
        linkedin_url="https://linkedin.com/in/jane",
    )
    link_contacts("ja-1", cid, [cand])
    link_contacts("ja-2", cid, [cand])
    conn = get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM application_contacts WHERE contact_id = "
            "(SELECT id FROM contacts WHERE full_name = 'Jane Doe')"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n == 2
