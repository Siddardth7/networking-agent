"""
tests/test_dispatch_coverage.py
Coverage uplift for src/agents/dispatch.py — targets branches left uncovered
after #21 baseline: anthropic_client=None paths (ValueError / generic Exception),
contact-not-found, no prior_draft_id, invalid persona/focus_area fallbacks,
COLD_EMAIL channel parsing, hard_check fail with placeholder redact,
critic enabled (pass + hold + exception fallback), DB write error,
and regen timeout/generic error on second attempt.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.agents.critic import RUBRIC_DIMENSIONS, SEVERE_SCORE
from src.agents.dispatch import dispatch_revision
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import Channel, DraftDispatchRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", Path(db_path))
    init_db()
    # Critic disabled by default — tests that need it opt-in via critic_enabled_cfg
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

    monkeypatch.setattr("src.agents.dispatch.load_config", _no_critic_cfg)
    yield db_path


def _seed_contact(
    channel: Channel = Channel.LINKEDIN_CONNECTION,
    persona: str = "PEER_ENGINEER",
    focus_area: str = "COMPOSITE_DESIGN",
    with_email: bool = False,
) -> tuple[int, int]:
    """Seed company + contact + draft. Returns (contact_id, draft_id)."""
    with with_writer() as conn:
        c = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'DRAFTED')"
        )
        company_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "linkedin_url, email, hook, state) "
            "VALUES (?, 'Alice', 'Composites Engineer', ?, ?, "
            "'https://linkedin.com/in/alice', ?, 'hook', 'DRAFTED')",
            (company_id, persona, focus_area, "alice@acme.com" if with_email else None),
        )
        contact_id = c.lastrowid
        c = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, quality_flag) "
            "VALUES (?, ?, 'Prior draft.', 1, 0)",
            (contact_id, channel.value),
        )
        draft_id = c.lastrowid
    return contact_id, draft_id


def _make_client(text: str) -> Mock:
    client = Mock()
    msg = Mock()
    msg.content = [Mock(text=text)]
    client.messages.create.return_value = msg
    return client


def _make_critic_client(texts: list[str], critic_scores: dict | None = None) -> Mock:
    """Client that returns plain text for drafter calls and critic tool_use."""
    scores = critic_scores or {dim: 5 for dim in RUBRIC_DIMENSIONS}
    texts = list(texts)

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


# ---------------------------------------------------------------------------
# anthropic_client=None paths (lines 209-216)
# ---------------------------------------------------------------------------


class TestClientInitFailure:
    def test_value_error_from_get_anthropic_client_returns_error(self, tmp_db):
        contact_id, draft_id = _seed_contact()

        with patch(
            "src.core.config.get_anthropic_client",
            side_effect=ValueError("no API key"),
        ):
            req = DraftDispatchRequest(
                contact_id=contact_id,
                channel=Channel.LINKEDIN_CONNECTION,
                prior_draft_id=draft_id,
                feedback="x",
            )
            resp = dispatch_revision(req)  # anthropic_client=None triggers import

        assert resp.status == "ERROR"
        assert "no API key" in resp.error_message

    def test_generic_exception_from_get_anthropic_client_returns_error(self, tmp_db):
        contact_id, draft_id = _seed_contact()

        with patch(
            "src.core.config.get_anthropic_client",
            side_effect=RuntimeError("connection refused"),
        ):
            req = DraftDispatchRequest(
                contact_id=contact_id,
                channel=Channel.LINKEDIN_CONNECTION,
                prior_draft_id=draft_id,
                feedback="x",
            )
            resp = dispatch_revision(req)

        assert resp.status == "ERROR"
        assert "Client init failed" in resp.error_message


# ---------------------------------------------------------------------------
# contact-not-found (lines 223)
# ---------------------------------------------------------------------------


class TestContactNotFound:
    def test_missing_contact_returns_error(self, tmp_db):
        req = DraftDispatchRequest(
            contact_id=99999,
            channel=Channel.LINKEDIN_CONNECTION,
            feedback="x",
        )
        client = _make_client("irrelevant")
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "ERROR"
        assert "not found" in resp.error_message


# ---------------------------------------------------------------------------
# no prior_draft_id (line 229->234 branch — skip _load_prior_draft)
# ---------------------------------------------------------------------------


class TestNoPriorDraftId:
    def test_no_prior_draft_id_still_succeeds(self, tmp_db):
        contact_id, _ = _seed_contact()
        client = _make_client("Clean revision with no prior body.")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=None,  # explicitly None
            feedback="make it warmer",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"
        assert resp.new_version == 2  # v1 existed from seed, so next is 2

    def test_prior_draft_id_not_found_uses_empty_prior_body(self, tmp_db):
        """prior_draft_id is set but the row doesn't exist → prior_body stays ''."""
        contact_id, _ = _seed_contact()
        client = _make_client("Clean revision without a prior.")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=99999,  # non-existent draft
            feedback="make it warmer",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        # Still succeeds — prior_body falls back to ""
        assert resp.status == "OK"
        assert resp.new_version == 2


# ---------------------------------------------------------------------------
# invalid persona / focus_area fall back to defaults (lines 240-241, 244-245)
# ---------------------------------------------------------------------------


class TestInvalidPersonaFocusAreaFallback:
    def test_invalid_persona_falls_back_to_peer_engineer(self, tmp_db):
        contact_id, draft_id = _seed_contact(persona="INVALID_PERSONA")
        client = _make_client("Brief clean note.")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)
        assert resp.status == "OK"

    def test_invalid_focus_area_falls_back_to_peer(self, tmp_db):
        contact_id, draft_id = _seed_contact(focus_area="INVALID_FOCUS")
        client = _make_client("Brief clean note.")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)
        assert resp.status == "OK"


# ---------------------------------------------------------------------------
# COLD_EMAIL body/subject parsing (lines 323-324)
# ---------------------------------------------------------------------------


class TestColdEmailChannel:
    def test_cold_email_subject_extracted(self, tmp_db):
        contact_id, draft_id = _seed_contact(channel=Channel.COLD_EMAIL, with_email=True)
        client = _make_client(
            "Subject: RE: Aerospace structures\n\nHi Alice, following up on your work."
        )

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.COLD_EMAIL,
            prior_draft_id=draft_id,
            feedback="be shorter",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"
        assert resp.subject == "RE: Aerospace structures"
        assert "following up" in resp.body
        assert "Subject:" not in resp.body


# ---------------------------------------------------------------------------
# Hard_check FAIL with placeholder redact (lines 345-346)
# ---------------------------------------------------------------------------


class TestHardFailWithPlaceholder:
    def test_hard_fail_with_placeholder_redacted_in_body(self, tmp_db):
        """When hard_check fails AND the body has a placeholder, it's redacted."""
        contact_id, draft_id = _seed_contact()
        # Very long body with a placeholder — hard_check fires for both
        overlong_placeholder = "[RESEARCH_NEEDED] " + "x " * 400
        client = _make_client(overlong_placeholder)

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="expand it",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "GUARDRAIL_FLAGGED"
        # Placeholder must be redacted in the saved body
        conn = get_connection()
        row = conn.execute(
            "SELECT body, quality_code FROM drafts WHERE id = ?", (resp.new_draft_id,)
        ).fetchone()
        conn.close()
        assert "[RESEARCH_NEEDED]" not in row["body"]
        assert row["quality_code"] == "HARD_FAIL"

    def test_hard_fail_without_placeholder_no_redaction(self, tmp_db):
        """hard_check fails (overlong email) but no placeholder → False branch at 345."""
        contact_id, draft_id = _seed_contact(channel=Channel.COLD_EMAIL, with_email=True)
        # 160 words, no placeholder → email_word_limit=150 triggers HARD_FAIL
        overlong_no_placeholder = "Subject: hi\n\n" + " ".join(["word"] * 160)
        client = _make_client(overlong_no_placeholder)

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.COLD_EMAIL,
            prior_draft_id=draft_id,
            feedback="add more detail",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "GUARDRAIL_FLAGGED"
        conn = get_connection()
        row = conn.execute(
            "SELECT body, quality_code FROM drafts WHERE id = ?", (resp.new_draft_id,)
        ).fetchone()
        conn.close()
        assert row["quality_code"] == "HARD_FAIL"
        assert "[" not in row["body"]  # no placeholder to redact


# ---------------------------------------------------------------------------
# Critic enabled — pass / hold / exception (lines 349-371)
# ---------------------------------------------------------------------------


@pytest.fixture
def critic_enabled_cfg(monkeypatch):
    """Ensure dispatch uses enable_critic=True for these tests."""
    from src.core.config import Config, load_config

    real = load_config

    def _critic_cfg():
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

    monkeypatch.setattr("src.agents.dispatch.load_config", _critic_cfg)


class TestCriticInDispatch:
    def test_critic_pass_yields_ok(self, tmp_db, critic_enabled_cfg):
        contact_id, draft_id = _seed_contact()
        client = _make_critic_client(
            texts=["Good brief note."],
            critic_scores={dim: 5 for dim in RUBRIC_DIMENSIONS},
        )
        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"
        assert resp.quality_flag is False

        conn = get_connection()
        row = conn.execute(
            "SELECT quality_code, critic_trace FROM drafts WHERE id = ?", (resp.new_draft_id,)
        ).fetchone()
        conn.close()
        assert row["quality_code"] == "OK"
        assert row["critic_trace"] is not None  # trace persisted even on pass

    def test_critic_hold_yields_guardrail_flagged(self, tmp_db, critic_enabled_cfg):
        contact_id, draft_id = _seed_contact()
        bad_scores = {dim: 5 for dim in RUBRIC_DIMENSIONS}
        bad_scores["specificity"] = SEVERE_SCORE
        client = _make_critic_client(texts=["Generic note."], critic_scores=bad_scores)

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "GUARDRAIL_FLAGGED"
        assert resp.quality_flag is True

        conn = get_connection()
        row = conn.execute(
            "SELECT quality_code FROM drafts WHERE id = ?", (resp.new_draft_id,)
        ).fetchone()
        conn.close()
        assert row["quality_code"] == "CRITIC_HOLD"

    def test_critic_exception_is_fail_open(self, tmp_db, critic_enabled_cfg):
        """When the critic call raises, the draft is still saved as OK (fail-open)."""
        contact_id, draft_id = _seed_contact()

        # Normal text client for the draft call; critic will raise
        texts = ["Clean brief note."]
        client = Mock()

        def _create(**kwargs):
            if "tools" in kwargs and kwargs.get("tools"):
                raise RuntimeError("critic offline")
            text = texts.pop(0)
            msg = Mock()
            msg.content = [Mock(text=text)]
            return msg

        client.messages.create.side_effect = _create

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"

        conn = get_connection()
        row = conn.execute(
            "SELECT quality_code FROM drafts WHERE id = ?", (resp.new_draft_id,)
        ).fetchone()
        conn.close()
        assert row["quality_code"] == "OK"


# ---------------------------------------------------------------------------
# DB write error (lines 386-390)
# ---------------------------------------------------------------------------


class TestDbWriteError:
    def test_db_write_failure_returns_error_status(self, tmp_db, monkeypatch):
        contact_id, draft_id = _seed_contact()
        client = _make_client("Clean note.")

        import src.agents.dispatch as dispatch_mod

        monkeypatch.setattr(
            dispatch_mod,
            "_insert_revised_draft",
            Mock(side_effect=RuntimeError("disk full")),
        )

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "ERROR"
        assert "DB write failed" in resp.error_message


# ---------------------------------------------------------------------------
# Regen timeout and generic error on second LLM call (lines 313-319)
# ---------------------------------------------------------------------------


class TestRegenErrors:
    def test_regen_timeout_returns_error(self, tmp_db):
        """When the regen (2nd) call times out, status=ERROR is returned."""
        contact_id, draft_id = _seed_contact()

        import time

        call_count = 0

        def _create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call returns a blocklist phrase → triggers regen
                msg = Mock()
                msg.content = [Mock(text="I noticed your impressive work.")]
                return msg
            # Second call (regen) hangs
            time.sleep(5)
            msg = Mock()
            msg.content = [Mock(text="never")]
            return msg

        client = Mock()
        client.messages.create.side_effect = _create

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="be warmer",
        )
        resp = dispatch_revision(req, anthropic_client=client, _timeout=0.05)

        assert resp.status == "ERROR"
        assert resp.error_message is not None

    def test_regen_generic_exception_returns_error(self, tmp_db):
        """When the regen call raises a generic error, status=ERROR."""
        contact_id, draft_id = _seed_contact()

        call_count = 0

        def _create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = Mock()
                msg.content = [Mock(text="I noticed your impressive profile.")]
                return msg
            raise ConnectionError("network gone")

        client = Mock()
        client.messages.create.side_effect = _create

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "ERROR"
        assert "LLM regen failed" in resp.error_message


# ---------------------------------------------------------------------------
# First LLM call generic exception (lines 284-288)
# ---------------------------------------------------------------------------


class TestFirstCallGenericException:
    def test_first_call_exception_returns_error(self, tmp_db):
        contact_id, draft_id = _seed_contact()

        client = Mock()
        client.messages.create.side_effect = ConnectionError("no network")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="x",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "ERROR"
        assert "LLM call failed" in resp.error_message
