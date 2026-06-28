"""
tests/test_orchestrator.py
Tests for src/orchestrator.py — state-machine resume paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.db import get_connection, init_db, with_writer
from src.orchestrator import run_pipeline

# ---------------------------------------------------------------------------
# DB isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB to an isolated temp file for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", Path(db_path))
    init_db()
    yield db_path


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_company(slug: str, state: str) -> int:
    """Insert a company row and return its id."""
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, ?)",
            (slug, slug.replace("-", " ").title(), state),
        )
        return cursor.lastrowid


def _seed_contact(company_id: int, state: str, name: str = "Test Contact") -> int:
    """Insert a contact row and return its id."""
    # URL is unique per name so several contacts in one company don't collide on
    # the (company_id, linkedin_url) unique index (migration 005).
    url = f"https://linkedin.com/in/{name.lower().replace(' ', '-')}"
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO contacts "
            "(company_id, full_name, title, persona, focus_area, linkedin_url, email, state) "
            "VALUES (?, ?, 'Engineer', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "?, 'test@co.com', ?)",
            (company_id, name, url, state),
        )
        return cursor.lastrowid


def _make_stubs(**overrides):
    """Return a dict of no-op stubs for all 6 pipeline steps."""
    stubs = {
        "_run_checks": MagicMock(return_value=0),
        "_find_contacts": MagicMock(return_value=[]),
        "_run_selection_gate": MagicMock(return_value=[]),
        "_draft_for_contacts": MagicMock(return_value={}),
        "_run_approval_loop": MagicMock(),
        "_write_artifact": MagicMock(return_value=Path("/tmp/artifact.md")),
    }
    stubs.update(overrides)
    return stubs


# ---------------------------------------------------------------------------
# Test 1: NEW state → full pipeline runs in order
# ---------------------------------------------------------------------------


def test_new_state_full_pipeline_called_in_order(capsys):
    """NEW company triggers preflight → find → select → draft → approve → artifact."""
    slug = "acme-corp"
    _seed_company(slug, "NEW")
    selected_ids = [1, 2]

    call_order = []

    def mock_checks():
        call_order.append("checks")
        return 0

    def mock_find(company_slug, limit=None, anthropic_client=None, location=None):
        call_order.append("find")
        return []

    def mock_gate(cid):
        call_order.append("gate")
        return selected_ids

    def mock_draft(ids, client):
        call_order.append("draft")
        return {}

    def mock_approve(cid):
        call_order.append("approve")

    def mock_artifact(cid):
        call_order.append("artifact")
        return Path("/tmp/a.md")

    run_pipeline(
        slug,
        _run_checks=mock_checks,
        _find_contacts=mock_find,
        _run_selection_gate=mock_gate,
        _draft_for_contacts=mock_draft,
        _run_approval_loop=mock_approve,
        _write_artifact=mock_artifact,
    )

    assert call_order == ["checks", "find", "gate", "draft", "approve", "artifact"]


# ---------------------------------------------------------------------------
# Test 2: DRAFTED state → approve + artifact, resume message printed
# ---------------------------------------------------------------------------


def test_drafted_state_resumes_from_approval(capsys):
    """DRAFTED company prints resume message and runs approve → artifact only."""
    slug = "beta-inc"
    company_id = _seed_company(slug, "DRAFTED")

    stubs = _make_stubs()
    run_pipeline(slug, **stubs)

    out = capsys.readouterr().out
    assert "Resuming pipeline" in out
    assert "state=DRAFTED" in out

    # Only approve + artifact should fire; checks and find must NOT be called
    stubs["_run_checks"].assert_not_called()
    stubs["_find_contacts"].assert_not_called()
    stubs["_run_selection_gate"].assert_not_called()
    stubs["_draft_for_contacts"].assert_not_called()
    stubs["_run_approval_loop"].assert_called_once_with(company_id)
    stubs["_write_artifact"].assert_called_once_with(company_id)


# ---------------------------------------------------------------------------
# Test 3: APPROVED state → "Nothing to do", no pipeline steps called
# ---------------------------------------------------------------------------


def test_approved_state_no_op(capsys):
    """APPROVED company prints 'Nothing to do' and exits without calling any step."""
    slug = "gamma-llc"
    _seed_company(slug, "APPROVED")

    stubs = _make_stubs()
    run_pipeline(slug, **stubs)

    out = capsys.readouterr().out
    assert "Nothing to do" in out

    for name, mock in stubs.items():
        mock.assert_not_called(), f"Expected {name} not to be called for APPROVED state"


# ---------------------------------------------------------------------------
# Test 4: FOUND state → selection_gate called, checks + find skipped
# ---------------------------------------------------------------------------


def test_found_state_skips_preflight_and_finder(capsys):
    """FOUND company resumes at selection gate; checks and find are skipped."""
    slug = "delta-tech"
    company_id = _seed_company(slug, "FOUND")

    stubs = _make_stubs()
    run_pipeline(slug, **stubs)

    out = capsys.readouterr().out
    assert "Resuming pipeline" in out
    assert "state=FOUND" in out

    stubs["_run_checks"].assert_not_called()
    stubs["_find_contacts"].assert_not_called()
    stubs["_run_selection_gate"].assert_called_once_with(company_id)


# ---------------------------------------------------------------------------
# Test 5: Mid-drafter kill — only SELECTED contacts re-drafted on resume
# ---------------------------------------------------------------------------


def test_selected_state_only_drafts_missing_contacts():
    """
    Simulate a mid-drafter kill: company is SELECTED, 2 contacts remain in
    SELECTED state, 1 is already DRAFTED. Only the 2 SELECTED contacts should
    be passed to draft_for_contacts.
    """
    slug = "epsilon-co"
    company_id = _seed_company(slug, "SELECTED")

    # 2 contacts still needing drafts, 1 already drafted
    sel_id_1 = _seed_contact(company_id, "SELECTED", "Alice Selected")
    sel_id_2 = _seed_contact(company_id, "SELECTED", "Bob Selected")
    _drafted_id = _seed_contact(company_id, "DRAFTED", "Carol Drafted")

    drafted_with: list[list[int]] = []

    def mock_draft(ids, client):
        drafted_with.append(list(ids))
        return {}

    stubs = _make_stubs(_draft_for_contacts=mock_draft)
    run_pipeline(slug, **stubs)

    assert drafted_with == [[sel_id_1, sel_id_2]], (
        f"Expected draft called with only SELECTED contact IDs, got {drafted_with}"
    )
    # Approve + artifact should still run
    stubs["_run_approval_loop"].assert_called_once_with(company_id)
    stubs["_write_artifact"].assert_called_once_with(company_id)


# ---------------------------------------------------------------------------
# Test 6: NEW slug not in DB → company row created with state=NEW, full pipeline
# ---------------------------------------------------------------------------


def test_new_company_created_in_db():
    """run_pipeline with an unseen slug inserts a NEW company row and runs all steps."""
    slug = "zeta-aerospace"

    # Do NOT seed the company — orchestrator must create it
    stubs = _make_stubs(
        _run_selection_gate=MagicMock(return_value=[7, 8]),
    )

    run_pipeline(slug, **stubs)

    # Verify the row was created in the DB with state=NEW
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT slug, name, state FROM companies WHERE slug = ?", (slug,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Company row should have been created"
    assert row["slug"] == slug
    assert row["state"] == "NEW"
    assert row["name"] == "Zeta Aerospace"

    # All pipeline steps should have fired
    stubs["_run_checks"].assert_called_once()
    stubs["_find_contacts"].assert_called_once()
    stubs["_run_selection_gate"].assert_called()
    stubs["_draft_for_contacts"].assert_called_once()
    stubs["_run_approval_loop"].assert_called_once()
    stubs["_write_artifact"].assert_called_once()


# ---------------------------------------------------------------------------
# Test 7: NEW state, preflight failure → pipeline halts after _run_checks
# ---------------------------------------------------------------------------


def test_new_state_preflight_failure_halts_pipeline(capsys):
    """When _run_checks returns 1, subsequent pipeline steps must NOT be called."""
    slug = "eta-systems"
    _seed_company(slug, "NEW")

    stubs = _make_stubs(_run_checks=MagicMock(return_value=1))
    run_pipeline(slug, **stubs)

    err = capsys.readouterr().err
    assert "Preflight checks failed" in err

    stubs["_find_contacts"].assert_not_called()
    stubs["_run_selection_gate"].assert_not_called()
    stubs["_draft_for_contacts"].assert_not_called()
    stubs["_run_approval_loop"].assert_not_called()
    stubs["_write_artifact"].assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: NEW state, selection returns [] → draft skipped, approve+artifact run
# ---------------------------------------------------------------------------


def test_new_state_no_selection_skips_draft():
    """When _run_selection_gate returns [], _draft_for_contacts must NOT be called."""
    slug = "theta-dynamics"
    _seed_company(slug, "NEW")

    stubs = _make_stubs(
        _run_checks=MagicMock(return_value=0),
        _run_selection_gate=MagicMock(return_value=[]),
    )
    run_pipeline(slug, **stubs)

    stubs["_draft_for_contacts"].assert_not_called()
    stubs["_run_approval_loop"].assert_called_once()
    stubs["_write_artifact"].assert_called_once()


# ---------------------------------------------------------------------------
# Test 9: FOUND state with contacts selected → draft IS called
# ---------------------------------------------------------------------------


def test_found_state_with_selection_calls_draft():
    """FOUND company + non-empty selection → _draft_for_contacts must be called."""
    slug = "iota-avionics"
    company_id = _seed_company(slug, "FOUND")

    selected_ids = [10, 20]
    stubs = _make_stubs(_run_selection_gate=MagicMock(return_value=selected_ids))
    run_pipeline(slug, **stubs)

    stubs["_draft_for_contacts"].assert_called_once_with(selected_ids, None)
    stubs["_run_approval_loop"].assert_called_once_with(company_id)
    stubs["_write_artifact"].assert_called_once_with(company_id)


# ---------------------------------------------------------------------------
# Test 10: SELECTED state, no SELECTED contacts → draft skipped
# ---------------------------------------------------------------------------


def test_selected_state_no_selected_contacts_skips_draft():
    """SELECTED company with zero SELECTED contacts → draft not called (branch 199->201)."""
    slug = "kappa-labs"
    company_id = _seed_company(slug, "SELECTED")

    # All contacts already DRAFTED — none in SELECTED state
    _seed_contact(company_id, "DRAFTED", "Alice Drafted")

    stubs = _make_stubs()
    run_pipeline(slug, **stubs)

    stubs["_draft_for_contacts"].assert_not_called()
    stubs["_run_approval_loop"].assert_called_once_with(company_id)
    stubs["_write_artifact"].assert_called_once_with(company_id)


# ---------------------------------------------------------------------------
# Test 11: Lazy imports (None → real module) — covered by patching the modules
# ---------------------------------------------------------------------------


def test_lazy_imports_resolved_when_none(monkeypatch):
    """All six injection params defaulting to None causes lazy imports (lines 154-176).

    We monkeypatch the real module functions so no actual API/DB work happens.
    """
    import src.agents.artifact_writer as aw_mod
    import src.agents.drafter as drafter_mod
    import src.agents.finder as finder_mod
    import src.agents.marketer as marketer_mod
    import src.cli.network_check as nc_mod
    import src.cli.selection_gate as gate_mod

    slug = "lambda-robotics"
    _seed_company(slug, "NEW")

    monkeypatch.setattr(nc_mod, "run_checks", lambda: 0)
    monkeypatch.setattr(finder_mod, "find_contacts",
                        lambda slug, limit=None, anthropic_client=None, location=None: [])
    monkeypatch.setattr(gate_mod, "run_selection_gate", lambda company_id: [])
    monkeypatch.setattr(drafter_mod, "draft_for_contacts",
                        lambda ids, client: {})
    monkeypatch.setattr(marketer_mod, "run_approval_loop", lambda company_id: None)
    monkeypatch.setattr(aw_mod, "write_artifact", lambda company_id: Path("/tmp/x.md"))

    # Call with NO injection overrides — triggers all lazy imports
    run_pipeline(slug)  # should not raise


def test_run_pipeline_threads_location_to_finder():
    """Issue #8: run_pipeline forwards --location end-to-end to the Finder."""
    from unittest.mock import Mock

    slug = "mu-space"
    _seed_company(slug, "NEW")
    find = Mock(return_value=[])
    run_pipeline(
        slug,
        location="Dayton, OH",
        _run_checks=Mock(return_value=0),
        _find_contacts=find,
        _run_selection_gate=Mock(return_value=[]),
        _draft_for_contacts=Mock(return_value={}),
        _run_approval_loop=Mock(return_value=None),
        _write_artifact=Mock(return_value=None),
    )
    find.assert_called_once()
    assert find.call_args.kwargs["location"] == "Dayton, OH"
