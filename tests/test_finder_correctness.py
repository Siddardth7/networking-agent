"""
tests/test_finder_correctness.py
FINDER_AUDIT correctness fixes (#27): D5 (no duplicate contact rows on re-run),
D9 (inferred email domain warns instead of failing silently), D12 (company-news
query uses the current year).
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import Mock

import pytest

from src.agents.finder import (
    _company_domain,
    _fetch_company_news_signal,
    _get_or_create_company,
    ingest_contacts,
)
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate, FocusArea, Persona


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


# --- D12: current-year company-news query ---------------------------------


def test_company_news_query_uses_current_year():
    sp = Mock()
    sp.search_general.return_value = "Acme ships a thing."
    _fetch_company_news_signal("acme-corp", sp)
    (query,), _ = sp.search_general.call_args
    assert str(datetime.now().year) in query


# --- D9: inferred domain is loud, not silent ------------------------------


def test_inferred_domain_warns(db_path, caplog):
    init_db()
    company_id = _get_or_create_company("general-electric")  # no domain stored
    with caplog.at_level(logging.WARNING, logger="networking_agent.finder"):
        domain = _company_domain(company_id, "general-electric")
    assert domain == "generalelectric.com"  # the (wrong-but-visible) inference
    assert any("no stored domain" in r.message for r in caplog.records)


def test_stored_domain_wins_no_warning(db_path, caplog):
    init_db()
    company_id = _get_or_create_company("acme-corp")
    with with_writer() as conn:
        conn.execute("UPDATE companies SET domain = 'acme.com' WHERE id = ?", (company_id,))
    with caplog.at_level(logging.WARNING, logger="networking_agent.finder"):
        domain = _company_domain(company_id, "acme-corp")
    assert domain == "acme.com"
    assert not caplog.records


# --- D5: no duplicate contact rows on re-run ------------------------------


def _candidate(name: str = "Dana Doe") -> ContactCandidate:
    # persona + focus pre-set so ingest skips the classifier (no client call).
    return ContactCandidate(
        full_name=name,
        title="Composites Engineer",
        linkedin_url="https://linkedin.com/in/dana",
        company_slug="acme-corp",
        persona=Persona.PEER_ENGINEER,
        focus_area=FocusArea.COMPOSITE_DESIGN,
    )


def _count_contacts(company_id: int) -> int:
    conn = get_connection()
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM contacts WHERE company_id = ?", (company_id,)
            ).fetchone()["n"]
        )
    finally:
        conn.close()


def test_rerun_same_linkedin_url_inserts_once(db_path):
    init_db()
    company_id = _get_or_create_company("acme-corp")
    client = Mock()

    first = ingest_contacts([_candidate()], company_id, "acme-corp", anthropic_client=client)
    assert len(first) == 1
    assert _count_contacts(company_id) == 1

    # Re-run: the same URL is ignored, so no dup row and an empty result.
    second = ingest_contacts([_candidate()], company_id, "acme-corp", anthropic_client=client)
    assert second == []
    assert _count_contacts(company_id) == 1


def test_rerun_does_not_duplicate_selected_contact(db_path):
    init_db()
    company_id = _get_or_create_company("acme-corp")
    client = Mock()
    ingest_contacts([_candidate()], company_id, "acme-corp", anthropic_client=client)
    # Promote it past NEW — the idempotency DELETE won't clear it.
    with with_writer() as conn:
        conn.execute("UPDATE contacts SET state = 'SELECTED' WHERE company_id = ?", (company_id,))

    ingest_contacts([_candidate()], company_id, "acme-corp", anthropic_client=client)
    assert _count_contacts(company_id) == 1


def test_null_linkedin_url_contacts_not_deduped(db_path):
    # The partial index only covers non-NULL URLs, so URL-less contacts are
    # free to coexist (there's no key to dedup them on).
    init_db()
    company_id = _get_or_create_company("acme-corp")
    client = Mock()
    a = ContactCandidate(
        full_name="No URL One",
        company_slug="acme-corp",
        persona=Persona.PEER_ENGINEER,
        focus_area=FocusArea.PEER,
    )
    b = ContactCandidate(
        full_name="No URL Two",
        company_slug="acme-corp",
        persona=Persona.PEER_ENGINEER,
        focus_area=FocusArea.PEER,
    )
    ingest_contacts([a, b], company_id, "acme-corp", anthropic_client=client)
    assert _count_contacts(company_id) == 2


def test_ingest_persists_rank_score_and_reasons(db_path):
    # #11: ingest scores each contact and stores the rank + explainable reasons.
    init_db()
    company_id = _get_or_create_company("acme-corp")
    client = Mock()
    strong = ContactCandidate(
        full_name="Strong Lead",
        title="Quality Engineer",
        linkedin_url="https://linkedin.com/in/strong",
        company_slug="acme-corp",
        persona=Persona.PEER_ENGINEER,
        focus_area=FocusArea.MANUFACTURING,
        email="strong@acme.com",
        alumni_confirmed=True,
        connection_degree="1st",
    )
    ingest_contacts([strong], company_id, "acme-corp", anthropic_client=client)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT rank_score, rank_reasons FROM contacts WHERE full_name = 'Strong Lead'"
        ).fetchone()
    finally:
        conn.close()
    # alumni_confirmed(40)+1st(30)+engineer(5)+email(5) = 80.
    assert row["rank_score"] == 80
    assert "confirmed alumnus" in row["rank_reasons"]
    assert "1st-degree connection" in row["rank_reasons"]
