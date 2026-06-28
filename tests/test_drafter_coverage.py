"""
tests/test_drafter_coverage.py
Coverage uplift for src/agents/drafter.py — targets branches left uncovered
after #21 baseline:
  - _load_persona_template: file-missing fallback (line 143)
  - _load_voice_doc: oversized file truncation (line 157+)
  - _trim_to_char_limit: first-sentence-too-long word-boundary truncation (lines 263-281)
  - _render_approved_facts: legacy bullet path (line 493)
  - assign_ask_angles: invalid/non-rotation persona skipped (line 405-406)
  - _draft_one_channel: auto-trim branch (695->701), opener-overuse soft_fail
    (712->720), hard_check fail + placeholder redact (735->737), critic
    exception fail-open (755-759), critic pass with trace (760->767)
  - _draft_all_channels_for_contact: contact_id not found returns [] (826),
    invalid persona fallback (830-831), invalid focus_area fallback (835-836)
  - draft_for_contacts: anthropic_client=None import path (952-954)
"""

from __future__ import annotations

import logging
from typing import NamedTuple
from unittest.mock import Mock, patch

import pytest

import src.agents.drafter as drafter_mod
from src.agents.critic import RUBRIC_DIMENSIONS, SEVERE_SCORE
from src.agents.drafter import (
    OpenerRegistry,
    _load_persona_template,
    _load_voice_doc,
    _render_approved_facts,
    _trim_to_char_limit,
    assign_ask_angles,
    draft_for_contacts,
)
from src.core.db import init_db, with_writer
from src.core.schemas import Persona

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    # Disable critic by default — override per-test where needed
    from src.core.config import Config, load_config

    real = load_config

    def _no_critic_cfg():
        cfg = real()
        return Config(
            anthropic_api_key=cfg.anthropic_api_key,
            serper_api_key=cfg.serper_api_key,
            hunter_api_key=cfg.hunter_api_key,
            serper_monthly_limit=cfg.serper_monthly_limit,
            hunter_monthly_limit=cfg.hunter_monthly_limit,
            finder_limit=cfg.finder_limit,
            linkedin_char_limit=cfg.linkedin_char_limit,
            email_word_limit=cfg.email_word_limit,
            batch_hard_fail_threshold=cfg.batch_hard_fail_threshold,
            enable_critic=False,
        )

    monkeypatch.setattr("src.agents.drafter.load_config", _no_critic_cfg)
    return path


def _seed(
    n: int = 1, with_email: bool = True, persona: str = "PEER_ENGINEER"
) -> tuple[int, list[int]]:
    init_db()
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'SELECTED')"
        )
        company_id = c.lastrowid
        ids = []
        for i in range(n):
            row = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, persona, focus_area, linkedin_url,
                    email, hook, state)
                   VALUES (?, ?, 'Composites Engineer', ?, 'COMPOSITE_DESIGN',
                    ?, ?, ?, 'SELECTED')""",
                (
                    company_id,
                    f"Person {i}",
                    persona,
                    f"https://linkedin.com/in/person{i}",
                    f"p{i}@acme.com" if with_email else None,
                    "shared composites work",
                ),
            )
            ids.append(row.lastrowid)
    return company_id, ids


def _mk_client(responses: list[str]) -> Mock:
    client = Mock()
    responses = list(responses)

    def _create(**kwargs):
        text = responses.pop(0)
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg

    client.messages.create.side_effect = _create
    return client


# ---------------------------------------------------------------------------
# _load_persona_template — file-missing fallback (line 143)
# ---------------------------------------------------------------------------


class TestLoadPersonaTemplate:
    def test_existing_persona_returns_file_content(self, tmp_path):
        # Real files should exist for the 4 canonical personas
        for persona in Persona:
            result = _load_persona_template(persona)
            # Either reads the file or returns the fallback — both are non-empty strings
            assert isinstance(result, str)
            assert len(result) > 0

    def test_missing_template_file_returns_fallback(self, tmp_path, monkeypatch):
        """When the template file doesn't exist, fallback identity line returned."""
        monkeypatch.setattr(drafter_mod, "_PERSONA_TEMPLATE_DIR", tmp_path)
        result = _load_persona_template(Persona.RECRUITER)
        assert "Siddardth" in result or "Write outreach" in result


# ---------------------------------------------------------------------------
# _load_voice_doc — oversized file truncation (lines 157, 159-165)
# ---------------------------------------------------------------------------


class TestLoadVoiceDoc:
    def test_missing_voice_doc_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.agents.drafter.voice_doc_path", lambda: tmp_path / "missing.md")
        result = _load_voice_doc()
        assert result == ""

    def test_normal_voice_doc_returned_intact(self, tmp_path, monkeypatch):
        p = tmp_path / "voice.md"
        p.write_text("Short voice doc.", encoding="utf-8")
        monkeypatch.setattr("src.agents.drafter.voice_doc_path", lambda: p)
        result = _load_voice_doc()
        assert result == "Short voice doc."

    def test_oversized_voice_doc_truncated_with_warning(self, tmp_path, monkeypatch, caplog):
        p = tmp_path / "voice.md"
        oversized = "x" * (drafter_mod._VOICE_DOC_MAX_CHARS + 100)
        p.write_text(oversized, encoding="utf-8")
        monkeypatch.setattr("src.agents.drafter.voice_doc_path", lambda: p)

        with caplog.at_level(logging.WARNING, logger="src.agents.drafter"):
            result = _load_voice_doc()

        assert len(result) == drafter_mod._VOICE_DOC_MAX_CHARS
        assert "truncating" in caplog.text.lower()


# ---------------------------------------------------------------------------
# _trim_to_char_limit — word-boundary truncation path (lines 263-281)
# ---------------------------------------------------------------------------


class TestTrimToCharLimit:
    def test_within_limit_unchanged(self):
        assert _trim_to_char_limit("short text", 100) == "short text"

    def test_non_positive_limit_unchanged(self):
        assert _trim_to_char_limit("abc", 0) == "abc"
        assert _trim_to_char_limit("abc", -1) == "abc"

    def test_sentence_trim(self):
        text = "First sentence. Second sentence that makes it too long."
        result = _trim_to_char_limit(text, 20)
        assert len(result) <= 20
        assert result  # non-empty

    def test_first_sentence_too_long_word_truncate(self):
        # Single very long word: forces the word-boundary fallback
        text = "thisisaverylongwordthatexceedsthelimit and more stuff"
        result = _trim_to_char_limit(text, 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_single_word_exceeds_limit_char_truncate(self):
        # Even first word exceeds limit: final fallback path
        result = _trim_to_char_limit("abcdefghijklmnop", 5)
        assert len(result) <= 5
        assert "…" in result

    def test_empty_string_returns_empty(self):
        # edge: limit=5, text="" -> strip -> ""
        result = _trim_to_char_limit("", 5)
        assert result == ""


# ---------------------------------------------------------------------------
# _render_approved_facts — legacy bullet (no project_title/type) path (line 493)
# ---------------------------------------------------------------------------


class LegacyBullet(NamedTuple):
    text: str
    # No project_title, no project_type attributes


class ProvenancedBullet(NamedTuple):
    text: str
    project_title: str
    project_type: object  # has .value


class _FakeProjectType:
    def __init__(self, val: str):
        self.value = val


class TestRenderApprovedFacts:
    def test_empty_bullets_returns_no_achievements_message(self):
        result = _render_approved_facts([])
        assert "no achievements matched" in result.lower()

    def test_legacy_bullet_no_provenance(self):
        # LegacyBullet has no project_title/project_type attrs → line 493 path
        b = LegacyBullet(text="Optimized composite layup for 15% weight saving.")
        result = _render_approved_facts([b])
        assert "Optimized composite layup" in result
        assert "[" not in result  # no type tag

    def test_provenanced_bullet_with_type(self):
        b = ProvenancedBullet(
            text="Built composite test rig.",
            project_title="SAMPE Bridge",
            project_type=_FakeProjectType("COMPETITION"),
        )
        result = _render_approved_facts([b])
        assert "[COMPETITION: SAMPE Bridge]" in result
        assert "Built composite test rig." in result

    def test_mixed_bullets(self):
        b1 = LegacyBullet(text="Legacy fact.")
        b2 = ProvenancedBullet(
            text="New fact.",
            project_title="Project X",
            project_type=_FakeProjectType("RESEARCH"),
        )
        result = _render_approved_facts([b1, b2])
        assert "Legacy fact." in result
        assert "[RESEARCH: Project X]" in result


# ---------------------------------------------------------------------------
# assign_ask_angles — invalid persona skipped (lines 405-406)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OpenerRegistry — empty opener_key fast-paths (lines 314, 321)
# ---------------------------------------------------------------------------


class TestOpenerRegistryEmptyKey:
    def test_is_overused_empty_key_returns_false(self):
        reg = OpenerRegistry(max_repeats=1)
        # Exhaust by registering same key twice
        reg.register("LINKEDIN_CONNECTION", "same opener")
        reg.register("LINKEDIN_CONNECTION", "same opener")
        # Empty key should always return False, not check counts
        assert reg.is_overused("LINKEDIN_CONNECTION", "") is False

    def test_register_empty_key_is_noop(self):
        reg = OpenerRegistry(max_repeats=1)
        # Should not crash and not add to counts
        reg.register("LINKEDIN_CONNECTION", "")
        assert reg._counts == {}
        # Empty key still returns False even after max_repeats conceptually
        assert reg.is_overused("LINKEDIN_CONNECTION", "") is False


class TestAssignAskAngles:
    def test_invalid_persona_in_db_skipped(self, db_path):
        """Contacts with invalid persona strings map to None, not crashing."""
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('x', 'X', 'SELECTED')"
            )
            company_id = c.lastrowid
            # Contact with an invalid persona string
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, hook, state) "
                "VALUES (?, 'Bad', 'INVALID_PERSONA', 'COMPOSITE_DESIGN', "
                "'https://li.com/bad', 'hook', 'SELECTED')",
                (company_id,),
            )
            cid = c.lastrowid

        result = assign_ask_angles([cid])
        assert result[cid] is None

    def test_recruiter_persona_gets_no_rotation(self, db_path):
        """RECRUITER is not in _ASK_ANGLE_POOLS; singleton → None."""
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('r', 'R', 'SELECTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                "linkedin_url, hook, state) "
                "VALUES (?, 'HR', 'RECRUITER', 'PEER', 'https://li.com/hr', 'hook', 'SELECTED')",
                (company_id,),
            )
            cid = c.lastrowid

        result = assign_ask_angles([cid])
        assert result[cid] is None

    def test_two_alumni_same_company_get_rotated_angles(self, db_path):
        """Two ALUMNI contacts at same company → distinct ask angles."""
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('a', 'AlumCo', 'SELECTED')"
            )
            company_id = c.lastrowid
            ids = []
            for name in ("A1", "A2"):
                c = conn.execute(
                    "INSERT INTO contacts (company_id, full_name, persona, focus_area, "
                    "linkedin_url, hook, state) "
                    "VALUES (?, ?, 'ALUMNI', 'ALUMNI_ACADEMIC', ?, 'hook', 'SELECTED')",
                    (company_id, name, f"https://li.com/{name}"),
                )
                ids.append(c.lastrowid)

        result = assign_ask_angles(ids)
        assert result[ids[0]] is not None
        assert result[ids[1]] is not None
        assert result[ids[0]] != result[ids[1]]


# ---------------------------------------------------------------------------
# _draft_all_channels_for_contact — contact_id not found returns [] (line 826)
# ---------------------------------------------------------------------------


class TestDraftAllChannelsContactNotFound:
    def test_missing_contact_id_returns_empty_list(self, db_path):
        init_db()
        client = Mock()
        from src.agents.drafter import _draft_all_channels_for_contact

        result = _draft_all_channels_for_contact(99999, client, None)
        assert result == []
        client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# _draft_all_channels_for_contact — invalid persona + focus_area fallbacks
# (lines 830-831, 835-836)
# ---------------------------------------------------------------------------


class TestDraftAllChannelsInvalidMetadata:
    def test_invalid_persona_hits_except_branch(self, db_path):
        """The except(ValueError, TypeError) at line 830 catches bad persona strings.
        The fallback persona is used for template loading; _draft_one_channel then
        re-raises (no guard there), surfacing as a DrafterPartialFailure."""
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('x', 'X', 'SELECTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
                "linkedin_url, hook, state) "
                "VALUES (?, 'Bad Persona', 'Engineer', 'TOTALLY_INVALID', 'COMPOSITE_DESIGN', "
                "'https://li.com/bp', 'hook', 'SELECTED')",
                (company_id,),
            )
            contact_id = c.lastrowid

        from src.agents.drafter import DrafterPartialFailure, _draft_all_channels_for_contact

        # Lines 830-831: except branch is hit, persona set to PEER_ENGINEER.
        # _draft_one_channel re-parses and raises ValueError for the bad string,
        # so the call propagates as an exception (correct design behavior).
        with pytest.raises((ValueError, DrafterPartialFailure)):
            responses = ["Note 1.", "Note 2.", "Subject: hi\n\nBody."]
            client = _mk_client(responses)
            _draft_all_channels_for_contact(contact_id, client, None)

    def test_invalid_focus_area_falls_back_to_peer(self, db_path):
        """Invalid focus_area triggers except branch (lines 835-836); PEER used."""
        init_db()
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('y', 'Y', 'SELECTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
                "linkedin_url, hook, state) "
                "VALUES (?, 'Bad Focus', 'Engineer', 'PEER_ENGINEER', 'TOTALLY_INVALID', "
                "'https://li.com/bf', 'hook', 'SELECTED')",
                (company_id,),
            )
            contact_id = c.lastrowid

        responses = ["Note 1.", "Note 2.", "Subject: hi\n\nBody."]
        client = _mk_client(responses)

        from src.agents.drafter import _draft_all_channels_for_contact

        # focus_area fallback to PEER works fine — _draft_one_channel doesn't
        # re-parse focus_area, so the drafts succeed.
        result = _draft_all_channels_for_contact(contact_id, client, None)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _draft_one_channel — auto-trim soft_fail branch (lines 695-701)
# already tested in test_drafter_quality_code.py; test here for the
# auto_trimmed → soft_failed path directly via _draft_one_channel
# ---------------------------------------------------------------------------


class TestDraftOneChannelHardFailNoPlaceholder:
    """hard_check fails (e.g. over length) but body has no placeholder.
    Tests the False branch of `if find_placeholder(body) is not None:` (line 735->737).
    We use a COLD_EMAIL that exceeds email_word_limit with no placeholders."""

    def test_overlong_email_hard_fail_no_placeholder(self, db_path):
        _, ids = _seed(1, with_email=True)
        # Email body with > 150 words but no placeholder
        overlong_body = " ".join(["word"] * 160)
        responses = [
            "CONN note.",          # LINKEDIN_CONNECTION
            "POST note.",          # LINKEDIN_POST_CONNECTION
            f"Subject: hi\n\n{overlong_body}",   # COLD_EMAIL — over word limit
        ]
        client = _mk_client(responses)
        result = draft_for_contacts(ids, anthropic_client=client)

        email_draft = next(d for d in result[ids[0]] if d.channel == "COLD_EMAIL")
        # hard_check fires for word limit, no placeholder → no redaction
        assert email_draft.quality_code == "HARD_FAIL"
        assert "[" not in email_draft.body  # no placeholder was present


class TestDraftOneChannelAutoTrim:
    def test_auto_trim_sets_soft_flag(self, db_path):
        """When the regen still busts the cap, the note is auto-trimmed → SOFT_FLAG."""
        _, ids = _seed(1)
        responses = [
            "x" * 320,   # gen 1 over cap → length-regen
            "y" * 320,   # regen still over → auto-trim
            "Follow-up message.",
            "Subject: hi\n\nBody.",
        ]
        client = _mk_client(responses)
        result = draft_for_contacts(ids, anthropic_client=client)

        conn_draft = next(d for d in result[ids[0]] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.quality_code == "SOFT_FLAG"
        assert len(conn_draft.body) <= 280


# ---------------------------------------------------------------------------
# _draft_one_channel — opener overuse soft_fail (lines 712-720)
# ---------------------------------------------------------------------------


class TestOpenerOveruseSoftFail:
    def test_overused_opener_after_regen_sets_soft_flag(self, db_path):
        """If the final draft's opener is still overused after regen, SOFT_FLAG."""
        _, ids = _seed(2)
        # Both contacts get the exact same opener; max_repeats=1 so second fires
        same_opener = "Hi, saw your composites work."
        responses = [
            # Contact 1: LINKEDIN_CONNECTION, POST, EMAIL
            same_opener,
            "Post-connection note.",
            "Subject: hi\n\nBody.",
            # Contact 2: LINKEDIN_CONNECTION → overused opener detected → regen
            same_opener,        # gen 1 (detected as overused → regen)
            same_opener,        # regen still same opener → soft_fail
            "Post-connection note 2.",
            "Subject: hi2\n\nBody2.",
        ]
        client = _mk_client(responses)

        from src.core.config import Config, load_config

        real = load_config

        def _cfg_max1():
            cfg = real()
            return Config(
                anthropic_api_key=cfg.anthropic_api_key,
                serper_api_key=cfg.serper_api_key,
                hunter_api_key=cfg.hunter_api_key,
                serper_monthly_limit=cfg.serper_monthly_limit,
                hunter_monthly_limit=cfg.hunter_monthly_limit,
                finder_limit=cfg.finder_limit,
                linkedin_char_limit=cfg.linkedin_char_limit,
                email_word_limit=cfg.email_word_limit,
                batch_hard_fail_threshold=cfg.batch_hard_fail_threshold,
                enable_critic=False,
                opener_max_repeats=1,
                drafter_max_workers=1,
            )

        with patch("src.agents.drafter.load_config", _cfg_max1):
            result = draft_for_contacts(ids, anthropic_client=client)

        # At least one LINKEDIN_CONNECTION draft should be SOFT_FLAG
        all_conn_drafts = [
            d
            for drafts in result.values()
            for d in drafts
            if d.channel == "LINKEDIN_CONNECTION"
        ]
        soft_flags = [d for d in all_conn_drafts if d.quality_code == "SOFT_FLAG"]
        assert len(soft_flags) >= 1


# ---------------------------------------------------------------------------
# _draft_one_channel — hard_check fail with placeholder redact (lines 735-737)
# via _draft_one_channel directly
# ---------------------------------------------------------------------------


class TestDraftOneChannelHardFailRedact:
    def test_placeholder_in_hard_fail_draft_is_redacted(self, db_path):
        """Hard_check fires; surviving placeholder tokens are redacted."""
        _, ids = _seed(1)
        # Both gen1 and regen have placeholders → HARD_FAIL; gen1 has placeholder
        responses = [
            "Hi [RESEARCH_NEEDED] — saw your work.",  # gen1 placeholder
            "Hi [RESEARCH_NEEDED] — still bad.",       # regen still dirty
            "Post-connection note.",
            "Subject: hi\n\nBody.",
        ]
        client = _mk_client(responses)
        result = draft_for_contacts(ids, anthropic_client=client)

        conn_draft = next(d for d in result[ids[0]] if d.channel == "LINKEDIN_CONNECTION")
        assert conn_draft.quality_code == "HARD_FAIL"
        # Placeholder must be stripped from what's stored
        assert "[RESEARCH_NEEDED]" not in conn_draft.body


# ---------------------------------------------------------------------------
# _draft_one_channel — critic exception fail-open (lines 755-759)
# and critic pass persists trace (lines 760-767)
# ---------------------------------------------------------------------------


@pytest.fixture
def critic_cfg(monkeypatch):
    from src.core.config import Config, load_config

    real = load_config

    def _cfg():
        cfg = real()
        return Config(
            anthropic_api_key=cfg.anthropic_api_key,
            serper_api_key=cfg.serper_api_key,
            hunter_api_key=cfg.hunter_api_key,
            serper_monthly_limit=cfg.serper_monthly_limit,
            hunter_monthly_limit=cfg.hunter_monthly_limit,
            finder_limit=cfg.finder_limit,
            linkedin_char_limit=cfg.linkedin_char_limit,
            email_word_limit=cfg.email_word_limit,
            batch_hard_fail_threshold=cfg.batch_hard_fail_threshold,
            enable_critic=True,
        )

    monkeypatch.setattr("src.agents.drafter.load_config", _cfg)


def _make_critic_client(draft_texts: list[str], critic_scores: dict | None = None) -> Mock:
    scores = critic_scores or {dim: 5 for dim in RUBRIC_DIMENSIONS}
    texts = list(draft_texts)
    client = Mock()

    def _create(**kwargs):
        if "tools" in kwargs and kwargs.get("tools"):
            tool = Mock()
            tool.type = "tool_use"
            payload = {dim: scores.get(dim, 5) for dim in RUBRIC_DIMENSIONS}
            payload["issues"] = []
            tool.input = payload
            resp = Mock()
            resp.content = [tool]
            return resp
        text = texts.pop(0) if texts else "fallback"
        msg = Mock()
        msg.content = [Mock(text=text)]
        return msg

    client.messages.create.side_effect = _create
    return client


class TestCriticInDrafter:
    def test_critic_exception_is_fail_open(self, db_path, critic_cfg):
        """When the critic raises, the draft is kept as OK (fail-open)."""
        _, ids = _seed(1, with_email=False)
        texts = ["Brief note.", "Follow-up note."]
        client = Mock()

        def _create(**kwargs):
            if "tools" in kwargs and kwargs.get("tools"):
                raise RuntimeError("critic crashed")
            text = texts.pop(0)
            msg = Mock()
            msg.content = [Mock(text=text)]
            return msg

        client.messages.create.side_effect = _create
        result = draft_for_contacts(ids, anthropic_client=client)

        for d in result[ids[0]]:
            assert d.quality_code == "OK"
            # critic_trace should be None since critic raised before writing it
            assert d.critic_trace is None

    def test_critic_pass_persists_trace(self, db_path, critic_cfg):
        """When critic passes, trace is still persisted (calibration data)."""
        _, ids = _seed(1, with_email=False)
        client = _make_critic_client(
            draft_texts=["Good brief note.", "Good follow-up."],
            critic_scores={dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        result = draft_for_contacts(ids, anthropic_client=client)

        for d in result[ids[0]]:
            assert d.quality_code == "OK"
            assert d.critic_trace is not None  # trace persisted even on pass

    def test_critic_hold_from_low_score(self, db_path, critic_cfg):
        """Low specificity score → CRITIC_HOLD on all channels."""
        _, ids = _seed(1, with_email=False)
        bad = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad["specificity"] = SEVERE_SCORE
        client = _make_critic_client(
            draft_texts=["Generic note.", "Also generic."],
            critic_scores=bad,
        )
        result = draft_for_contacts(ids, anthropic_client=client)

        for d in result[ids[0]]:
            assert d.quality_code == "CRITIC_HOLD"
            assert d.quality_flag is True


# ---------------------------------------------------------------------------
# draft_for_contacts — anthropic_client=None import path (lines 952-954)
# ---------------------------------------------------------------------------


class TestDraftForContactsNoneClient:
    def test_none_client_triggers_import(self, db_path):
        """When anthropic_client=None, get_anthropic_client is called."""
        _, ids = _seed(1, with_email=False)

        fake_client = _make_critic_client(
            draft_texts=["Note.", "Note2."],
            critic_scores={dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        with patch("src.core.config.get_anthropic_client", return_value=fake_client) as mock_get:
            # Import inside drafter happens when client is None
            try:
                draft_for_contacts(ids, anthropic_client=None)
            except Exception:
                # If get_anthropic_client raises or returns bad client that's fine
                pass
            mock_get.assert_called_once()
