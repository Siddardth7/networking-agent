"""
tests/test_ask_rotation.py
Phase 3 (ask-rotation): when several contacts at the SAME company share a
rotation-eligible persona (alumni / peer), each is assigned a DISTINCT ask
angle up front and that angle is injected into its generation prompt, so the
group of short conversations paints a fuller picture instead of repeating one
script. Singletons and non-eligible personas (recruiter / senior manager) get
no assignment and behave exactly as before.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.drafter import (
    _ALUMNI_ASK_ANGLES,
    _PEER_ASK_ANGLES,
    _build_prompt,
    assign_ask_angles,
    draft_for_contacts,
)
from src.core.db import init_db, with_writer


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    monkeypatch.setattr("src.agents.drafter._MAX_WORKERS", 1)  # ordered queue
    from src.core.config import Config, load_config

    real = load_config

    def _no_critic_cfg():
        cfg = real()
        return Config(
            anthropic_api_key=cfg.anthropic_api_key,
            serper_api_key=cfg.serper_api_key,
            hunter_api_key=cfg.hunter_api_key,
            enable_critic=False,
        )

    monkeypatch.setattr("src.agents.drafter.load_config", _no_critic_cfg)
    return path


def _seed(
    n: int,
    persona: str = "ALUMNI",
    *,
    company_slug: str = "acme",
) -> list[int]:
    """Seed *n* SELECTED contacts of *persona* at one company. Returns their ids."""
    init_db()
    with with_writer() as conn:
        row = conn.execute(
            "SELECT id FROM companies WHERE slug = ?", (company_slug,)
        ).fetchone()
        if row is None:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES (?, ?, 'SELECTED')",
                (company_slug, company_slug.title()),
            )
            company_id = c.lastrowid
        else:
            company_id = row["id"]
        ids = []
        for i in range(n):
            c = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, persona, focus_area, linkedin_url,
                    email, hook, state)
                   VALUES (?, ?, 'Engineer', ?, 'PEER', ?, NULL, 'your work', 'SELECTED')""",
                (company_id, f"{company_slug} Person {i}", persona, f"https://li/{company_slug}{i}"),
            )
            ids.append(c.lastrowid)
    return ids


def _mk_client():
    """Mock client returning a unique opener per call (so opener-variety never
    regenerates and the response stream never runs dry)."""
    client = Mock()
    captured: list[str] = []
    counter = {"n": 0}

    def _create(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        n = counter["n"]
        counter["n"] += 1
        msg = Mock()
        msg.content = [Mock(text=f"Unique opener number {n}. Would value connecting.")]
        return msg

    client.messages.create.side_effect = _create
    client.captured_prompts = captured
    return client


# ---------------------------------------------------------------------------
# assign_ask_angles — the pure assignment logic
# ---------------------------------------------------------------------------
class TestAssignAskAngles:
    def test_three_alumni_same_company_get_distinct_angles(self, db_path):
        ids = _seed(3, "ALUMNI")
        angles = assign_ask_angles(ids)
        assigned = [angles[i] for i in ids]
        assert assigned == list(_ALUMNI_ASK_ANGLES[:3])
        assert len(set(assigned)) == 3  # all distinct

    def test_peer_group_uses_peer_pool(self, db_path):
        ids = _seed(2, "PEER_ENGINEER")
        angles = assign_ask_angles(ids)
        assert [angles[i] for i in ids] == list(_PEER_ASK_ANGLES[:2])

    def test_singleton_gets_none(self, db_path):
        ids = _seed(1, "ALUMNI")
        angles = assign_ask_angles(ids)
        assert angles[ids[0]] is None

    def test_recruiter_group_not_rotated(self, db_path):
        ids = _seed(3, "RECRUITER")
        angles = assign_ask_angles(ids)
        assert all(angles[i] is None for i in ids)

    def test_senior_manager_group_not_rotated(self, db_path):
        ids = _seed(3, "SENIOR_MANAGER")
        angles = assign_ask_angles(ids)
        assert all(angles[i] is None for i in ids)

    def test_separate_companies_grouped_independently(self, db_path):
        acme = _seed(2, "ALUMNI", company_slug="acme")
        beta = _seed(2, "ALUMNI", company_slug="beta")
        angles = assign_ask_angles(acme + beta)
        # Each company's group restarts the rotation at pool[0].
        assert [angles[i] for i in acme] == list(_ALUMNI_ASK_ANGLES[:2])
        assert [angles[i] for i in beta] == list(_ALUMNI_ASK_ANGLES[:2])

    def test_group_larger_than_pool_wraps(self, db_path):
        n = len(_ALUMNI_ASK_ANGLES) + 1
        ids = _seed(n, "ALUMNI")
        angles = assign_ask_angles(ids)
        assigned = [angles[i] for i in ids]
        assert assigned[-1] == _ALUMNI_ASK_ANGLES[0]  # wrapped back to start

    def test_empty_input(self, db_path):
        assert assign_ask_angles([]) == {}


# ---------------------------------------------------------------------------
# _build_prompt — angle injection
# ---------------------------------------------------------------------------
class TestPromptInjection:
    _contact = {
        "full_name": "Jane Doe",
        "company_name": "Sierra Space",
        "title": "Engineer",
        "persona": "ALUMNI",
        "hook": "shared UIUC",
    }

    def _prompt(self, ask_angle):
        from src.core.schemas import Channel, Persona

        return _build_prompt(
            self._contact,
            Channel.LINKEDIN_CONNECTION,
            Persona.ALUMNI,
            bullets=[],
            persona_template="ALUMNI TEMPLATE",
            voice_doc="",
            ask_angle=ask_angle,
        )

    def test_angle_section_present_when_assigned(self):
        angle = _ALUMNI_ASK_ANGLES[1]
        prompt = self._prompt(angle)
        assert "ASSIGNED ASK ANGLE" in prompt
        assert angle in prompt

    def test_no_angle_section_when_none(self):
        prompt = self._prompt(None)
        assert "ASSIGNED ASK ANGLE" not in prompt

    def test_company_name_in_prompt(self):
        # Without the company name the model fabricates an employer
        # (Phase 3 validation, 2026-06-20). It must appear in Contact Info.
        prompt = self._prompt(None)
        assert "Company: Sierra Space" in prompt


# ---------------------------------------------------------------------------
# draft_for_contacts — end-to-end wiring
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def test_three_alumni_each_prompt_carries_a_distinct_angle(self, db_path):
        ids = _seed(3, "ALUMNI")
        client = _mk_client()
        draft_for_contacts(ids, anthropic_client=client)

        prompts = client.captured_prompts
        # Exactly the first three pool angles should appear; the unused ones
        # must not — that proves round-robin distinctness end to end.
        for angle in _ALUMNI_ASK_ANGLES[:3]:
            assert any(angle in p for p in prompts), angle
        for angle in _ALUMNI_ASK_ANGLES[3:]:
            assert not any(angle in p for p in prompts), angle

    def test_singleton_alumni_no_angle_injected(self, db_path):
        ids = _seed(1, "ALUMNI")
        client = _mk_client()
        draft_for_contacts(ids, anthropic_client=client)
        assert all("ASSIGNED ASK ANGLE" not in p for p in client.captured_prompts)

    def test_rotation_disabled_injects_nothing(self, db_path, monkeypatch):
        from src.core.config import Config

        def _cfg_no_rotation():
            return Config(enable_critic=False, enable_ask_rotation=False)

        monkeypatch.setattr("src.agents.drafter.load_config", _cfg_no_rotation)
        ids = _seed(3, "ALUMNI")
        client = _mk_client()
        draft_for_contacts(ids, anthropic_client=client)
        assert all("ASSIGNED ASK ANGLE" not in p for p in client.captured_prompts)


class TestConfigKnob:
    def test_enable_ask_rotation_default_true(self):
        from src.core.config import Config

        assert Config().enable_ask_rotation is True

    def test_enable_ask_rotation_from_yaml(self, tmp_path, monkeypatch):
        import os

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("quality:\n  enable_ask_rotation: false\n")
        os.chmod(cfg_file, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg_file))
        from src.core.config import load_config

        assert load_config().enable_ask_rotation is False
