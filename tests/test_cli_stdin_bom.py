"""Issue #95: stdin CLIs must tolerate a leading UTF-8 BOM from PowerShell pipes.

All six host-token bridges read stdin through ``src.cli.read_stdin_text``; a
Windows PowerShell pipe prepends a UTF-8 BOM (U+FEFF) that otherwise breaks
``json.loads`` on the first char. These tests pin the shared helper and one
real CLI path end-to-end with a BOM present.
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

from src.cli import read_stdin_text
from src.cli.network_classify_host import run_ingest
from src.core.db import get_connection, init_db
from src.core.schemas import ContactCandidate

BOM = "\ufeff"


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


class TestReadStdinText:
    def test_strips_leading_bom(self, monkeypatch):
        _set_stdin(monkeypatch, BOM + '{"a": 1}')
        assert read_stdin_text() == '{"a": 1}'

    def test_noop_without_bom(self, monkeypatch):
        _set_stdin(monkeypatch, '{"a": 1}')
        assert read_stdin_text() == '{"a": 1}'

    def test_only_leading_bom_stripped(self, monkeypatch):
        # A BOM mid-payload (not from the pipe) is content, left untouched.
        _set_stdin(monkeypatch, BOM + "x" + BOM)
        assert read_stdin_text() == "x" + BOM


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def test_ingest_accepts_bom_prefixed_payload(capsys, monkeypatch, tmp_db):
    """A BOM-prefixed JSON list ingests cleanly (was: invalid-JSON BOM error)."""
    cand = ContactCandidate(
        full_name="Bom Tester", title="Composites Engineer",
        snippet="led 787 wing-box stress team", company_slug="ignored",
    )
    payload = [{
        "candidate": cand.model_dump(mode="json"),
        "classification": {"persona": "PEER_ENGINEER", "focus_area": "COMPOSITE_DESIGN",
                           "hook_signal": "led 787 wing-box stress team"},
    }]
    _set_stdin(monkeypatch, BOM + json.dumps(payload))
    rc = run_ingest(argparse.Namespace(verb="ingest", slug="acme"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ingested"] == 1

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT full_name FROM contacts WHERE full_name = 'Bom Tester'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
