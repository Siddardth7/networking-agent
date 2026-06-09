"""
tests/test_dispatch.py
Mandatory 3 tests for src/agents/dispatch.py per PLAN.md Step 7.2 exit gate:
  (1) clean revision → status=OK + version=2
  (2) double guardrail → status=GUARDRAIL_FLAGGED + quality_flag=True
  (3) simulated 91s LLM hang → status=ERROR
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

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
    # Layer 4 critic is OFF for these tests — they cover dispatch flow,
    # status mapping, and DB persistence. Critic-on dispatch coverage
    # lives in tests/test_dispatch_grounding.py.
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


def _seed_contact_with_draft(channel=Channel.LINKEDIN_CONNECTION):
    """Insert company + contact + one draft. Returns (contact_id, draft_id)."""
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme Corp', 'DRAFTED')"
        )
        company_id = cursor.lastrowid

        cursor = conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "linkedin_url, hook, state) "
            "VALUES (?, 'Alice Eng', 'Engineer', 'PEER_ENGINEER', 'COMPOSITE_DESIGN', "
            "'https://linkedin.com/in/alice', 'UIUC alum', 'DRAFTED')",
            (company_id,),
        )
        contact_id = cursor.lastrowid

        cursor = conn.execute(
            "INSERT INTO drafts (contact_id, channel, body, version, quality_flag) "
            "VALUES (?, ?, 'Initial draft body.', 1, 0)",
            (contact_id, channel.value),
        )
        draft_id = cursor.lastrowid

    return contact_id, draft_id


def _make_client(response_text: str):
    """Build a mock Anthropic client returning *response_text* for every call."""
    client = Mock()
    msg = Mock()
    msg.content = [Mock(text=response_text)]
    client.messages.create.return_value = msg
    return client


# ---------------------------------------------------------------------------
# Test 1: Clean revision → status=OK + version=2 inserted
# ---------------------------------------------------------------------------

class TestCleanRevision:
    def test_ok_status_and_version_incremented(self):
        contact_id, draft_id = _seed_contact_with_draft()
        client = _make_client("Hi Alice — reaching out because of our shared composites work.")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="Make it warmer and more specific",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"
        assert resp.quality_flag is False
        assert resp.new_version == 2
        assert resp.new_draft_id is not None

        # Verify version=2 row in DB
        conn = get_connection()
        row = conn.execute(
            "SELECT version, quality_flag FROM drafts WHERE id = ?",
            (resp.new_draft_id,),
        ).fetchone()
        conn.close()
        assert row["version"] == 2
        assert row["quality_flag"] == 0

    def test_idempotent_second_revise_yields_version_3(self):
        """Second REVISE on same (contact, channel) → version=3."""
        contact_id, draft_id = _seed_contact_with_draft()
        client = _make_client("Clean text without blocked phrases.")

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="First revision",
        )
        resp1 = dispatch_revision(req, anthropic_client=client)
        assert resp1.status == "OK"
        assert resp1.new_version == 2

        req2 = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=resp1.new_draft_id,
            feedback="Second revision",
        )
        resp2 = dispatch_revision(req2, anthropic_client=client)
        assert resp2.status == "OK"
        assert resp2.new_version == 3


# ---------------------------------------------------------------------------
# Test 2: Double guardrail → GUARDRAIL_FLAGGED + quality_flag=True
# ---------------------------------------------------------------------------

class TestDoubleGuardrail:
    def test_guardrail_flagged_when_both_attempts_fail(self):
        contact_id, draft_id = _seed_contact_with_draft()

        # Both attempts return a blocked phrase
        client = Mock()
        flagged_text = "I admire your work at Acme and noticed your profile."
        msg = Mock()
        msg.content = [Mock(text=flagged_text)]
        client.messages.create.return_value = msg

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="Be more enthusiastic",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "GUARDRAIL_FLAGGED"
        assert resp.quality_flag is True
        assert resp.new_draft_id is not None

        # Draft IS written to DB (flagged but saved)
        conn = get_connection()
        row = conn.execute(
            "SELECT quality_flag FROM drafts WHERE id = ?",
            (resp.new_draft_id,),
        ).fetchone()
        conn.close()
        assert row["quality_flag"] == 1

    def test_single_guardrail_then_clean_is_ok(self):
        """First attempt blocked, second attempt clean → status=OK."""
        contact_id, draft_id = _seed_contact_with_draft()

        call_count = 0
        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            msg = Mock()
            if call_count == 1:
                msg.content = [Mock(text="I noticed your profile and admire it.")]
            else:
                msg.content = [Mock(text="Hi Alice — quick note about our composites work.")]
            return msg

        client = Mock()
        client.messages.create.side_effect = fake_create

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="Try again",
        )
        resp = dispatch_revision(req, anthropic_client=client)

        assert resp.status == "OK"
        assert resp.quality_flag is False
        assert call_count == 2


# ---------------------------------------------------------------------------
# Test 3: 91s LLM hang → status=ERROR
# ---------------------------------------------------------------------------

class TestTimeoutReturnsError:
    def test_timeout_returns_error_status(self):
        contact_id, draft_id = _seed_contact_with_draft()

        import time

        def slow_create(**kwargs):
            time.sleep(5)  # won't complete before _timeout=0.05 fires
            msg = Mock()
            msg.content = [Mock(text="Won't matter")]
            return msg

        client = Mock()
        client.messages.create.side_effect = slow_create

        req = DraftDispatchRequest(
            contact_id=contact_id,
            channel=Channel.LINKEDIN_CONNECTION,
            prior_draft_id=draft_id,
            feedback="Some feedback",
        )
        # Use a tiny timeout (50ms) to simulate the 91s hang
        resp = dispatch_revision(req, anthropic_client=client, _timeout=0.05)

        assert resp.status == "ERROR"
        assert resp.error_message is not None
        assert "timeout" in resp.error_message.lower() or "exceeded" in resp.error_message.lower()

        # No new draft should have been written
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE contact_id = ? AND version > 1",
            (contact_id,),
        ).fetchone()[0]
        conn.close()
        assert count == 0
