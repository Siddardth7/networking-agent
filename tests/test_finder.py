"""
Tests for src/agents/finder.py — 5-phase Finder pipeline.
Covers: classifications written to DB, hook generation, company state transitions,
HUNTER_EXHAUSTED path, and empty-result handling.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.finder import _generate_hook, find_contacts
from src.core.db import get_connection, init_db
from src.core.schemas import ContactCandidate, EmailResult, FocusArea, Persona
from src.providers.retry import QuotaExhausted

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_response(persona: str, focus_area: str) -> Mock:
    tool_block = Mock()
    tool_block.type = "tool_use"
    tool_block.input = {"persona": persona, "focus_area": focus_area}
    response = Mock()
    response.content = [tool_block]
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


@pytest.fixture
def mock_serper():
    provider = Mock()
    provider.search_linkedin_profiles.return_value = [
        ContactCandidate(
            full_name="Alice Smith",
            title="Composites Engineer",
            linkedin_url="https://linkedin.com/in/alice",
            company_slug="acme-corp",
        ),
        ContactCandidate(
            full_name="Bob Jones",
            title="Senior Manager, Structures",
            linkedin_url="https://linkedin.com/in/bob",
            company_slug="acme-corp",
        ),
        ContactCandidate(
            full_name="Carol Lee",
            title="Technical Recruiter",
            linkedin_url="https://linkedin.com/in/carol",
            company_slug="acme-corp",
        ),
    ]
    return provider


@pytest.fixture
def mock_hunter():
    provider = Mock()
    provider.find_email.side_effect = [
        EmailResult(email="alice@acme.com", verified=True, confidence=90, source="hunter"),
        EmailResult(email="bob@acme.com", verified=False, confidence=70, source="hunter"),
        EmailResult(email="carol@acme.com", verified=True, confidence=85, source="hunter"),
    ]
    return provider


@pytest.fixture
def mock_anthropic():
    client = Mock()
    client.messages.create.side_effect = [
        _make_tool_response("PEER_ENGINEER", "COMPOSITE_DESIGN"),
        _make_tool_response("SENIOR_MANAGER", "STRUCTURAL_ANALYSIS"),
        _make_tool_response("RECRUITER", "MANUFACTURING"),
    ]
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFindContacts:
    def test_returns_enriched_candidates(self, db_path, mock_serper, mock_hunter, mock_anthropic):
        init_db()
        results = find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )
        assert len(results) == 3
        for r in results:
            assert isinstance(r, ContactCandidate)

    def test_persona_and_focus_area_classified_correctly(
        self, db_path, mock_serper, mock_hunter, mock_anthropic
    ):
        init_db()
        results = find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )
        assert results[0].persona == Persona.PEER_ENGINEER
        assert results[0].focus_area == FocusArea.COMPOSITE_DESIGN
        assert results[1].persona == Persona.SENIOR_MANAGER
        assert results[1].focus_area == FocusArea.STRUCTURAL_ANALYSIS
        assert results[2].persona == Persona.RECRUITER
        assert results[2].focus_area == FocusArea.MANUFACTURING

    def test_emails_assigned(self, db_path, mock_serper, mock_hunter, mock_anthropic):
        init_db()
        results = find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )
        assert results[0].email == "alice@acme.com"
        assert results[1].email == "bob@acme.com"
        assert results[2].email == "carol@acme.com"

    def test_company_state_transitions_to_found(
        self, db_path, mock_serper, mock_hunter, mock_anthropic
    ):
        init_db()
        find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )
        conn = get_connection()
        try:
            row = conn.execute("SELECT state FROM companies WHERE slug = 'acme-corp'").fetchone()
            assert row["state"] == "FOUND"
        finally:
            conn.close()

    def test_contacts_written_to_db_with_hooks(
        self, db_path, mock_serper, mock_hunter, mock_anthropic
    ):
        init_db()
        find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )
        conn = get_connection()
        try:
            company = conn.execute("SELECT id FROM companies WHERE slug = 'acme-corp'").fetchone()
            contacts = conn.execute(
                "SELECT full_name, persona, focus_area, hook, email "
                "FROM contacts WHERE company_id = ? ORDER BY id",
                (company["id"],),
            ).fetchall()
        finally:
            conn.close()

        assert len(contacts) == 3
        assert contacts[0]["persona"] == "PEER_ENGINEER"
        assert contacts[0]["focus_area"] == "COMPOSITE_DESIGN"
        assert contacts[0]["hook"] == "your composites work"
        assert contacts[0]["email"] == "alice@acme.com"
        assert contacts[1]["hook"] == "your structures work"

    def test_hunter_exhausted_marks_remaining_contacts(self, db_path, mock_serper, mock_anthropic):
        init_db()
        hunter = Mock()
        hunter.find_email.side_effect = [
            EmailResult(email="alice@acme.com", verified=True, confidence=90, source="hunter"),
            QuotaExhausted("hunter", 25, 25),
        ]
        # anthropic fixture only has 3 responses; reset to match
        mock_anthropic.messages.create.side_effect = [
            _make_tool_response("PEER_ENGINEER", "COMPOSITE_DESIGN"),
            _make_tool_response("SENIOR_MANAGER", "STRUCTURAL_ANALYSIS"),
            _make_tool_response("RECRUITER", "MANUFACTURING"),
        ]
        results = find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=hunter,
            anthropic_client=mock_anthropic,
        )
        assert len(results) == 3
        assert results[0].email == "alice@acme.com"
        assert results[1].email is None
        assert results[2].email is None

        conn = get_connection()
        try:
            company = conn.execute("SELECT id FROM companies WHERE slug = 'acme-corp'").fetchone()
            contacts = conn.execute(
                "SELECT email, source_provider FROM contacts WHERE company_id = ? ORDER BY id",
                (company["id"],),
            ).fetchall()
        finally:
            conn.close()

        assert contacts[0]["source_provider"] == "hunter"
        assert contacts[1]["email"] is None
        assert contacts[1]["source_provider"] == "HUNTER_EXHAUSTED"
        assert contacts[2]["source_provider"] == "HUNTER_EXHAUSTED"

    def test_empty_serper_results_returns_empty_list(self, db_path):
        init_db()
        serper = Mock()
        serper.search_linkedin_profiles.return_value = []
        hunter = Mock()
        anthropic = Mock()
        results = find_contacts(
            "empty-corp",
            limit=5,
            serper_provider=serper,
            hunter_provider=hunter,
            anthropic_client=anthropic,
        )
        assert results == []
        conn = get_connection()
        try:
            row = conn.execute("SELECT state FROM companies WHERE slug = 'empty-corp'").fetchone()
            assert row["state"] == "FOUND"
        finally:
            conn.close()

    def test_idempotent_clears_previous_new_contacts(
        self, db_path, mock_serper, mock_hunter, mock_anthropic
    ):
        """Second call clears NEW contacts from a previous partial run."""
        init_db()
        # First run
        find_contacts(
            "acme-corp",
            limit=3,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )

        # Reset mocks for second run
        mock_serper.search_linkedin_profiles.return_value = [
            ContactCandidate(
                full_name="Dave Brown",
                title="Quality Engineer",
                linkedin_url="https://linkedin.com/in/dave",
                company_slug="acme-corp",
            )
        ]
        mock_hunter.find_email.side_effect = [
            EmailResult(email="dave@acme.com", verified=True, confidence=80, source="hunter")
        ]
        mock_anthropic.messages.create.side_effect = [
            _make_tool_response("PEER_ENGINEER", "MANUFACTURING"),
        ]

        # First run left company in FOUND state; reset it to NEW to simulate a retry
        from src.core.db import with_writer

        with with_writer() as conn:
            conn.execute("UPDATE companies SET state = 'NEW' WHERE slug = 'acme-corp'")
            # Manually mark existing contacts as NEW to simulate partial run
            conn.execute("UPDATE contacts SET state = 'NEW' WHERE 1")

        results = find_contacts(
            "acme-corp",
            limit=1,
            serper_provider=mock_serper,
            hunter_provider=mock_hunter,
            anthropic_client=mock_anthropic,
        )
        assert len(results) == 1
        assert results[0].full_name == "Dave Brown"


class TestGenerateHook:
    def test_composites_tier3(self):
        c = ContactCandidate(
            full_name="A", title="Composites Engineer", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "your composites work"

    def test_structural_tier3(self):
        c = ContactCandidate(
            full_name="A", title="Stress Engineer", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "your structures work"

    def test_manufacturing_tier3(self):
        c = ContactCandidate(
            full_name="A", title="Supplier Quality Engineer", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "your manufacturing and quality background"

    def test_materials_tier3(self):
        c = ContactCandidate(
            full_name="A", title="Materials Scientist", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "your materials science background"

    def test_additive_tier3(self):
        c = ContactCandidate(
            full_name="A", title="Additive Manufacturing Lead", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "your additive manufacturing work"

    def test_generic_fallback(self):
        c = ContactCandidate(
            full_name="A", title="Technical Recruiter", linkedin_url="", company_slug="x"
        )
        # AUDIT-A5: a real title now yields a title-derived hook instead of
        # the GENERIC sentinel.
        assert _generate_hook(c) == "your work as Technical Recruiter"

    def test_uiuc_tier1_in_title(self):
        c = ContactCandidate(
            full_name="A", title="UIUC Aerospace PhD", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "we share a UIUC background"

    def test_shared_employer_tier2(self):
        c = ContactCandidate(
            full_name="A", title="ex-Boeing Quality Engineer", linkedin_url="", company_slug="x"
        )
        assert _generate_hook(c) == "you also spent time at Boeing"
