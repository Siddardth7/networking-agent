"""Issue #97: off-company candidates (semantic matches who have left the target)
must not be filed under the target company.

Covers the employer-match predicate, the Apify extraction of the current
employer, and the end-to-end ingest gate (drop + host-visible count).
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

from src.agents.finder import _employer_matches
from src.cli.network_classify_host import run_ingest
from src.core.db import get_connection, init_db
from src.core.schemas import ContactCandidate
from src.providers.apify import _parse_item


class TestEmployerMatches:
    @pytest.mark.parametrize(("employer", "slug", "expected"), [
        ("Caterpillar", "caterpillar", True),
        ("Caterpillar Inc.", "caterpillar", True),   # decorated suffix still matches
        ("Joby Aviation", "joby", True),             # target is a token of the employer
        ("Optunity Ltd", "caterpillar", False),      # unrelated company
        ("GE Aerospace", "caterpillar", False),      # moved on
        ("", "caterpillar", False),                  # empty employer
        ("Caterpillar", "", False),                  # empty target
    ])
    def test_matches(self, employer, slug, expected):
        assert _employer_matches(employer, slug) is expected


def test_apify_parse_captures_current_employer():
    item = {
        "firstName": "Ada", "lastName": "Byron",
        "currentPosition": {"companyName": "Caterpillar Inc.", "position": "Engineer"},
    }
    candidate = _parse_item(item, "caterpillar")
    assert candidate is not None
    assert candidate.current_employer == "Caterpillar Inc."


def test_apify_parse_no_employer_is_none():
    item = {"firstName": "Ada", "lastName": "Byron"}
    candidate = _parse_item(item, "caterpillar")
    assert candidate is not None
    assert candidate.current_employer is None


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _item(name, employer):
    cand = ContactCandidate(
        full_name=name, title="Composites Engineer", snippet="led a stress team",
        company_slug="ignored", current_employer=employer,
    )
    return {
        "candidate": cand.model_dump(mode="json"),
        "classification": {"persona": "PEER_ENGINEER", "focus_area": "COMPOSITE_DESIGN",
                           "hook_signal": "led a stress team"},
    }


def test_off_company_candidate_dropped(capsys, monkeypatch, tmp_db):
    """A candidate whose current employer != target is excluded and counted."""
    payload = [
        _item("Stays Here", "Caterpillar Inc."),   # current employee → kept
        _item("Left Already", "GE Aerospace"),      # moved on → dropped
        _item("Unknown Employer", None),            # unknown → kept (no false drop)
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = run_ingest(argparse.Namespace(verb="ingest", slug="caterpillar"))
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    assert out["ingested"] == 2
    assert out["off_company_dropped"] == 1
    assert set(out["contacts"]) == {"Stays Here", "Unknown Employer"}

    conn = get_connection()
    try:
        names = {r["full_name"] for r in conn.execute(
            "SELECT full_name FROM contacts"
        ).fetchall()}
    finally:
        conn.close()
    assert "Left Already" not in names
