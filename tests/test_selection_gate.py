"""
Unit tests for src/cli/selection_gate.py
"""

from __future__ import annotations

import pytest

from src.cli.selection_gate import _parse_selection, run_selection_gate
from src.core.db import get_connection, init_db, with_writer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


def _seed_db(n_contacts: int = 3) -> tuple[int, list[int]]:
    """Insert a FOUND company + n contacts, return (company_id, [contact_id, ...])."""
    init_db()
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('test-co', 'Test Co', 'FOUND')"
        )
        company_id = cursor.lastrowid
        contact_ids = []
        for i in range(n_contacts):
            cursor = conn.execute(
                "INSERT INTO contacts (company_id, full_name, title, hook, "
                "state) VALUES (?, ?, ?, ?, 'NEW')",
                (company_id, f"Contact {i + 1}", f"Engineer {i + 1}", f"hook_{i + 1}"),
            )
            contact_ids.append(cursor.lastrowid)
    return company_id, contact_ids


# ---------------------------------------------------------------------------
# _parse_selection unit tests
# ---------------------------------------------------------------------------


class TestParseSelection:
    def test_comma_separated(self):
        assert _parse_selection("1,3", 4) == [1, 3]

    def test_single_index(self):
        assert _parse_selection("2", 3) == [2]

    def test_all(self):
        assert _parse_selection("all", 3) == [1, 2, 3]

    def test_all_case_insensitive(self):
        assert _parse_selection("ALL", 3) == [1, 2, 3]

    def test_none(self):
        assert _parse_selection("none", 3) == []

    def test_none_case_insensitive(self):
        assert _parse_selection("NONE", 3) == []

    def test_invalid_out_of_range(self):
        assert _parse_selection("5", 3) is None

    def test_invalid_zero(self):
        assert _parse_selection("0", 3) is None

    def test_invalid_garbage(self):
        assert _parse_selection("garbage", 3) is None

    def test_empty_string(self):
        assert _parse_selection("", 3) is None

    def test_whitespace_only(self):
        assert _parse_selection("   ", 3) is None

    def test_with_extra_spaces(self):
        assert _parse_selection(" 1 , 2 ", 3) == [1, 2]


class TestRankOrdering:
    """#11: the gate presents contacts highest referral-likelihood first."""

    def _seed_ranked(self, scores: list[int]) -> tuple[int, list[int]]:
        init_db()
        with with_writer() as conn:
            cid = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('rk', 'Rk', 'FOUND')"
            ).lastrowid
            ids = []
            for i, sc in enumerate(scores):
                r = conn.execute(
                    "INSERT INTO contacts (company_id, full_name, rank_score, "
                    "rank_reasons, linkedin_url, state) VALUES (?, ?, ?, ?, ?, 'NEW')",
                    (cid, f"C{i}", sc, f"reasons {sc}", f"https://li/{i}"),
                )
                ids.append(r.lastrowid)
        return cid, ids

    def test_highest_rank_presented_first(self, db_path):
        # Seeded in ascending score order; index 1 must be the score=50 contact.
        cid, ids = self._seed_ranked([10, 50, 30])
        selected = run_selection_gate(cid, _input_fn=lambda _: "1")
        assert selected == [ids[1]]

    def test_full_order_is_rank_desc(self, db_path):
        cid, ids = self._seed_ranked([10, 50, 30])
        selected = run_selection_gate(cid, _input_fn=lambda _: "all")
        assert selected == [ids[1], ids[2], ids[0]]  # 50, 30, 10

    def test_equal_rank_breaks_ties_by_id(self, db_path):
        cid, ids = self._seed_ranked([20, 20])
        selected = run_selection_gate(cid, _input_fn=lambda _: "all")
        assert selected == [ids[0], ids[1]]  # stable by id when scores tie

    def test_partial_out_of_range(self):
        assert _parse_selection("1,5", 3) is None


# ---------------------------------------------------------------------------
# run_selection_gate integration tests
# ---------------------------------------------------------------------------


class TestRunSelectionGate:
    def test_comma_separated_selection(self, db_path):
        company_id, contact_ids = _seed_db()
        result = run_selection_gate(company_id, _input_fn=lambda _: "1,3")
        assert result == [contact_ids[0], contact_ids[2]]

        conn = get_connection()
        try:
            c1 = conn.execute(
                "SELECT selected, state FROM contacts WHERE id = ?", (contact_ids[0],)
            ).fetchone()
            c2 = conn.execute(
                "SELECT selected, state FROM contacts WHERE id = ?", (contact_ids[1],)
            ).fetchone()
            co = conn.execute("SELECT state FROM companies WHERE id = ?", (company_id,)).fetchone()
        finally:
            conn.close()

        assert c1["selected"] == 1
        assert c1["state"] == "SELECTED"
        assert c2["selected"] == 0
        assert c2["state"] == "NEW"
        assert co["state"] == "SELECTED"

    def test_all_selection(self, db_path):
        company_id, contact_ids = _seed_db()
        result = run_selection_gate(company_id, _input_fn=lambda _: "all")
        assert result == contact_ids

        conn = get_connection()
        try:
            co = conn.execute("SELECT state FROM companies WHERE id = ?", (company_id,)).fetchone()
            selected = conn.execute(
                "SELECT COUNT(*) AS n FROM contacts WHERE company_id = ? AND selected = 1",
                (company_id,),
            ).fetchone()
        finally:
            conn.close()

        assert co["state"] == "SELECTED"
        assert selected["n"] == 3

    def test_none_selection_does_not_update_db(self, db_path):
        company_id, _ = _seed_db()
        result = run_selection_gate(company_id, _input_fn=lambda _: "none")
        assert result == []

        conn = get_connection()
        try:
            co = conn.execute("SELECT state FROM companies WHERE id = ?", (company_id,)).fetchone()
            selected = conn.execute(
                "SELECT COUNT(*) AS n FROM contacts WHERE company_id = ? AND selected = 1",
                (company_id,),
            ).fetchone()
        finally:
            conn.close()

        # Company stays FOUND; no contacts marked selected
        assert co["state"] == "FOUND"
        assert selected["n"] == 0

    def test_invalid_then_valid_reprompts(self, db_path, capsys):
        company_id, contact_ids = _seed_db()
        inputs = iter(["garbage", "1,2"])
        call_count = 0

        def mock_input(_prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return next(inputs)

        result = run_selection_gate(company_id, _input_fn=mock_input)
        assert call_count == 2
        assert result == [contact_ids[0], contact_ids[1]]

        captured = capsys.readouterr()
        assert "Invalid selection" in captured.out

    def test_multiple_invalid_reprompts(self, db_path, capsys):
        company_id, contact_ids = _seed_db()
        inputs = iter(["99", "0", "2"])
        result = run_selection_gate(company_id, _input_fn=lambda _: next(inputs))
        assert result == [contact_ids[1]]

        captured = capsys.readouterr()
        assert captured.out.count("Invalid selection") == 2

    def test_no_contacts_returns_empty(self, db_path, capsys):
        init_db()
        with with_writer() as conn:
            cursor = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('empty-co', 'Empty Co', 'FOUND')"
            )
            company_id = cursor.lastrowid

        result = run_selection_gate(company_id, _input_fn=lambda _: "all")
        assert result == []

        captured = capsys.readouterr()
        assert "No contacts found" in captured.out

    def test_single_contact_selection(self, db_path):
        company_id, contact_ids = _seed_db(n_contacts=1)
        result = run_selection_gate(company_id, _input_fn=lambda _: "1")
        assert result == [contact_ids[0]]
