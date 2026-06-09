"""
tests/test_artifact_persona_quality.py
Layer 6d: artifact markdown now surfaces persona + per-draft quality_code
so a reviewer can audit hook/classification without diving into the DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.artifact_writer import write_artifact
from src.core.db import init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", Path(tmp_path / "a.db"))
    init_db()


def _seed(quality_codes: dict[str, str]) -> tuple[int, Path]:
    """Seed one company + one contact with one draft per channel/code."""
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "linkedin_url, email, hook, shared_signals, state) "
            "VALUES (?, 'Alice Eng', 'Composites Engineer', 'PEER_ENGINEER', "
            "'COMPOSITE_DESIGN', 'https://linkedin.com/in/alice', 'a@acme.com', "
            "'shared composites work', 'profile: led wing repair certification', 'DRAFTED')",
            (company_id,),
        )
        contact_id = c.lastrowid
        for ch, code in quality_codes.items():
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, "
                "quality_flag, quality_code) VALUES (?, ?, 'Draft body.', 1, ?, ?)",
                (contact_id, ch, int(code != "OK"), code),
            )
    return company_id, contact_id


def _read(company_id: int, tmp_path: Path) -> str:
    path = write_artifact(company_id, _output_dir=tmp_path / "out")
    return path.read_text()


class TestArtifactRendering:
    def test_persona_and_focus_area_visible(self, tmp_path):
        company_id, _ = _seed({
            "LINKEDIN_CONNECTION": "OK",
            "LINKEDIN_POST_CONNECTION": "OK",
            "COLD_EMAIL": "OK",
        })
        out = _read(company_id, tmp_path)
        assert "**Persona:** PEER_ENGINEER" in out
        assert "**Focus area:** COMPOSITE_DESIGN" in out

    def test_shared_signals_visible(self, tmp_path):
        company_id, _ = _seed({"LINKEDIN_CONNECTION": "OK"})
        out = _read(company_id, tmp_path)
        assert "**Shared signals:**" in out
        assert "led wing repair certification" in out

    def test_ok_drafts_have_no_badge(self, tmp_path):
        company_id, _ = _seed({
            "LINKEDIN_CONNECTION": "OK",
            "LINKEDIN_POST_CONNECTION": "OK",
            "COLD_EMAIL": "OK",
        })
        out = _read(company_id, tmp_path)
        assert "HARD_FAIL" not in out
        assert "CRITIC_HOLD" not in out
        assert "SOFT_FLAG" not in out

    def test_hard_fail_badge_loud(self, tmp_path):
        company_id, _ = _seed({
            "LINKEDIN_CONNECTION": "HARD_FAIL",
            "LINKEDIN_POST_CONNECTION": "OK",
            "COLD_EMAIL": "OK",
        })
        out = _read(company_id, tmp_path)
        assert "HARD_FAIL" in out

    def test_critic_hold_badge_loud(self, tmp_path):
        company_id, _ = _seed({
            "LINKEDIN_CONNECTION": "OK",
            "LINKEDIN_POST_CONNECTION": "CRITIC_HOLD",
            "COLD_EMAIL": "OK",
        })
        out = _read(company_id, tmp_path)
        assert "CRITIC_HOLD" in out

    def test_soft_flag_badge_warns_but_not_blocked(self, tmp_path):
        company_id, _ = _seed({
            "LINKEDIN_CONNECTION": "SOFT_FLAG",
            "LINKEDIN_POST_CONNECTION": "OK",
            "COLD_EMAIL": "OK",
        })
        out = _read(company_id, tmp_path)
        assert "SOFT_FLAG" in out
