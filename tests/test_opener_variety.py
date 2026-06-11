"""
tests/test_opener_variety.py
AUDIT-A6 (Layer 1-A): cross-contact opener diversification. The same
normalized opener may be used by at most ``opener_max_repeats`` contacts
per run (default 2); the next contact that would repeat it gets one
corrective regen, and a draft that STILL repeats it is SOFT_FLAGged.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.drafter import OpenerRegistry, draft_for_contacts, normalize_opener
from src.core.db import init_db, with_writer

REPEATED = "Saw your work in aerospace quality. MS AE at UIUC. Would value connecting."
VARIED = "Your MRB background caught my eye — I work on composites at UIUC."


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


def _seed(n: int) -> list[int]:
    init_db()
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        company_id = c.lastrowid
        ids = []
        for i in range(n):
            c = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, persona, focus_area, linkedin_url,
                    email, hook, state)
                   VALUES (?, ?, 'Quality Engineer', 'PEER_ENGINEER', 'MANUFACTURING',
                           ?, NULL, 'your quality work', 'SELECTED')""",
                (company_id, f"Person {i}", f"https://linkedin.com/in/p{i}"),
            )
            ids.append(c.lastrowid)
    return ids


def _mk_client(responses: list[str]):
    client = Mock()
    captured: list[str] = []

    def _create(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        msg = Mock()
        msg.content = [Mock(text=responses.pop(0))]
        return msg

    client.messages.create.side_effect = _create
    client.captured_prompts = captured
    return client


class TestNormalizeOpener:
    def test_first_sentence_lowercased_and_stripped(self):
        a = normalize_opener("Saw your work on X! Rest of message.")
        b = normalize_opener("saw your work on x —  rest differs entirely.")
        assert a == b == "saw your work on x"

    def test_empty_text(self):
        assert normalize_opener("") == ""

    def test_long_opener_capped_to_word_window(self):
        text = " ".join(f"w{i}" for i in range(40)) + ". Next."
        result = normalize_opener(text)
        assert len(result.split()) <= 12


class TestOpenerRegistry:
    def test_not_overused_below_limit(self):
        reg = OpenerRegistry(max_repeats=2)
        key = normalize_opener(REPEATED)
        assert not reg.is_overused("LINKEDIN_CONNECTION", key)
        reg.register("LINKEDIN_CONNECTION", key)
        assert not reg.is_overused("LINKEDIN_CONNECTION", key)
        reg.register("LINKEDIN_CONNECTION", key)
        assert reg.is_overused("LINKEDIN_CONNECTION", key)

    def test_channels_tracked_independently(self):
        reg = OpenerRegistry(max_repeats=1)
        key = normalize_opener(REPEATED)
        reg.register("LINKEDIN_CONNECTION", key)
        assert reg.is_overused("LINKEDIN_CONNECTION", key)
        assert not reg.is_overused("LINKEDIN_POST_CONNECTION", key)


class TestDrafterVariety:
    def test_third_contact_with_same_opener_regens(self, db_path):
        ids = _seed(3)
        client = _mk_client(
            [
                REPEATED,
                "Follow-up A.",  # contact 1: CONN, POST
                REPEATED,
                "Follow-up B.",  # contact 2: CONN repeats (allowed)
                REPEATED,  # contact 3: CONN gen 1 — overused
                VARIED,  # contact 3: CONN regen
                "Follow-up C.",  # contact 3: POST
            ]
        )
        results = draft_for_contacts(ids, anthropic_client=client)

        conn3 = next(d for d in results[ids[2]] if d.channel == "LINKEDIN_CONNECTION")
        assert conn3.body == VARIED
        assert conn3.quality_code == "OK"
        # The regen prompt names the overused opener.
        regen_prompt = client.captured_prompts[5]
        assert "Saw your work in aerospace quality" in regen_prompt

    def test_persistent_repeat_soft_flagged(self, db_path):
        ids = _seed(3)
        client = _mk_client(
            [
                REPEATED,
                "Follow-up A.",
                REPEATED,
                "Follow-up B.",
                REPEATED,  # contact 3 gen 1 — overused
                REPEATED,  # contact 3 regen — still the same opener
                "Follow-up C.",
            ]
        )
        results = draft_for_contacts(ids, anthropic_client=client)
        conn3 = next(d for d in results[ids[2]] if d.channel == "LINKEDIN_CONNECTION")
        assert conn3.quality_code == "SOFT_FLAG"

    def test_varied_openers_never_regen(self, db_path):
        ids = _seed(3)
        client = _mk_client(
            [
                "Opener one here. X.",
                "Follow-up A.",
                "Different opener two. Y.",
                "Follow-up B.",
                "Yet another opener. Z.",
                "Follow-up C.",
            ]
        )
        draft_for_contacts(ids, anthropic_client=client)
        assert client.messages.create.call_count == 6


class TestConfigKnob:
    def test_opener_max_repeats_default(self):
        from src.core.config import Config

        assert Config().opener_max_repeats == 2

    def test_opener_max_repeats_from_yaml(self, tmp_path, monkeypatch):
        import os

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("quality:\n  opener_max_repeats: 4\n")
        os.chmod(cfg_file, 0o600)
        monkeypatch.setenv("NETWORKING_AGENT_CONFIG", str(cfg_file))
        from src.core.config import load_config

        assert load_config().opener_max_repeats == 4
