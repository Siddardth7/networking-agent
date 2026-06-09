"""
tests/test_orchestrator_checkpoint.py
Layer 5: orchestrator emits a batch-quality warning when the HARD_FAIL
fraction exceeds the configured threshold, but never aborts the pipeline.
"""

from __future__ import annotations

import pytest

from src.orchestrator import (
    _batch_quality_checkpoint,
    _batch_quality_report,
)
from src.core.db import init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr("src.core.db._DB_PATH", Path(tmp_path / "x.db"))
    init_db()


def _seed_drafts(quality_codes: list[str]) -> int:
    """Seed one company + one contact + one draft per entry in quality_codes.

    Returns the company id.
    """
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('a', 'A', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, state) "
            "VALUES (?, 'X', 'DRAFTED')",
            (company_id,),
        )
        contact_id = c.lastrowid
        for i, code in enumerate(quality_codes):
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, "
                "quality_flag, quality_code) VALUES (?, ?, ?, 1, ?, ?)",
                (contact_id, f"CH_{i}", "body", int(code != "OK"), code),
            )
    return company_id


# ---------------------------------------------------------------------------
# _batch_quality_report
# ---------------------------------------------------------------------------

class TestBatchReport:
    def test_counts_hard_fails(self):
        company_id = _seed_drafts(["OK", "HARD_FAIL", "OK", "HARD_FAIL"])
        hard, total = _batch_quality_report(company_id)
        assert hard == 2
        assert total == 4

    def test_empty_company_returns_zero(self):
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('e', 'E', 'NEW')"
            )
            cid = c.lastrowid
        hard, total = _batch_quality_report(cid)
        assert (hard, total) == (0, 0)

    def test_treats_null_quality_code_as_ok(self):
        # Legacy rows pre-migration 002 — should not be counted as HARD_FAIL.
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('a', 'A', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, state) "
                "VALUES (?, 'X', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            # Insert without specifying quality_code (uses 'OK' default).
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version) "
                "VALUES (?, 'L', 'b', 1)",
                (contact_id,),
            )
        hard, total = _batch_quality_report(company_id)
        assert hard == 0
        assert total == 1


# ---------------------------------------------------------------------------
# _batch_quality_checkpoint — warn-and-continue, never abort
# ---------------------------------------------------------------------------

class TestCheckpointWarning:
    def test_warns_above_threshold(self, capsys):
        company_id = _seed_drafts(["HARD_FAIL", "HARD_FAIL", "OK", "OK"])
        # Default threshold is 0.0 → 50% triggers the warning.
        _batch_quality_checkpoint(company_id)
        out = capsys.readouterr().out
        assert "Batch quality warning" in out
        assert "2/4" in out

    def test_silent_at_or_below_threshold(self, capsys, monkeypatch):
        company_id = _seed_drafts(["OK", "OK", "OK"])
        _batch_quality_checkpoint(company_id)
        out = capsys.readouterr().out
        assert "Batch quality warning" not in out

    def test_empty_company_silent(self, capsys):
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('e', 'E', 'NEW')"
            )
            cid = c.lastrowid
        _batch_quality_checkpoint(cid)
        assert capsys.readouterr().out == ""

    def test_never_raises(self):
        # The checkpoint is warn-and-continue: bad batches must not abort.
        company_id = _seed_drafts(["HARD_FAIL"] * 5)
        # Should not raise.
        _batch_quality_checkpoint(company_id)
