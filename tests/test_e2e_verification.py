"""
tests/test_e2e_verification.py
End-to-end verification: exercise the full Finder → Drafter → Marketer →
Artifact path with mocked providers. The headline assertions match the
§7 definition-of-done — placeholders never reach approval, fabricated
metrics never reach approval, CRITIC_HOLD blocks like HARD_FAIL, and the
artifact surfaces persona + per-draft quality_code.

This is the harness-level proof that the six layers compose. The
live-API verification run (paid Sonnet/Haiku/Serper/Hunter calls) is the
final step and is invoked separately.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.agents.artifact_writer import write_artifact
from src.agents.critic import RUBRIC_DIMENSIONS, MIN_SCORE
from src.agents.drafter import draft_for_contacts
from src.agents.finder import find_contacts
from src.agents.marketer import run_approval_loop
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate, EmailResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    path = tmp_path / "e2e.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    init_db()
    yield path


def _classifier_response(persona: str, focus_area: str, hook_signal: str) -> Mock:
    tool = Mock()
    tool.type = "tool_use"
    tool.input = {"persona": persona, "focus_area": focus_area, "hook_signal": hook_signal}
    resp = Mock()
    resp.content = [tool]
    return resp


def _critic_response(scores: dict[str, int]) -> Mock:
    tool = Mock()
    tool.type = "tool_use"
    payload = {dim: scores.get(dim, 5) for dim in RUBRIC_DIMENSIONS}
    payload["issues"] = []
    tool.input = payload
    resp = Mock()
    resp.content = [tool]
    return resp


def _text_response(text: str) -> Mock:
    msg = Mock()
    msg.content = [Mock(text=text)]
    return msg


class _StagedClient:
    """Anthropic client that returns a queued response per call.

    Use queue items of type "classifier" / "critic" / "draft".
    """

    def __init__(self):
        self.queue: list[tuple[str, object]] = []
        self.messages = Mock()
        self.messages.create.side_effect = self._create
        self.call_log: list[str] = []

    def enqueue_classifier(self, persona: str, focus: str, hook: str = ""):
        self.queue.append(("classifier", (persona, focus, hook)))

    def enqueue_critic_pass(self, scores: dict | None = None):
        self.queue.append(("critic", scores or {d: 5 for d in RUBRIC_DIMENSIONS}))

    def enqueue_critic_fail(self, dimension: str):
        bad = {d: 5 for d in RUBRIC_DIMENSIONS}
        bad[dimension] = MIN_SCORE - 1
        self.queue.append(("critic", bad))

    def enqueue_draft(self, text: str):
        self.queue.append(("draft", text))

    def _create(self, **kwargs):
        if not self.queue:
            raise RuntimeError("StagedClient queue exhausted")
        kind, payload = self.queue.pop(0)
        self.call_log.append(kind)
        if kind == "classifier":
            persona, focus, hook = payload
            return _classifier_response(persona, focus, hook)
        if kind == "critic":
            return _critic_response(payload)
        # kind == "draft"
        return _text_response(payload)


# ---------------------------------------------------------------------------
# Test: full Finder run produces snippet + hook_signal + shared_signals
# ---------------------------------------------------------------------------

class TestFinderE2E:
    def test_finder_writes_hook_and_shared_signals(self):
        serper = Mock()
        serper.search_linkedin_profiles.return_value = [
            ContactCandidate(
                full_name="Jane Doe", title="Senior MRB Engineer",
                linkedin_url="https://linkedin.com/in/janedoe",
                company_slug="acme-corp",
                snippet="Senior MRB engineer; led bonded composite repair certification.",
            ),
        ]
        serper.search_general.return_value = "Acme expands composites facility in 2026."

        hunter = Mock()
        hunter.find_email.return_value = EmailResult(
            email="jane@acme.com", verified=True, confidence=90, source="hunter",
        )

        client = _StagedClient()
        client.enqueue_classifier(
            "SENIOR_MANAGER", "MANUFACTURING",
            "led bonded composite repair certification",
        )

        find_contacts(
            "acme-corp", limit=1,
            serper_provider=serper, hunter_provider=hunter,
            anthropic_client=client,
        )

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT persona, hook, shared_signals FROM contacts "
                "WHERE full_name = 'Jane Doe'"
            ).fetchone()
        finally:
            conn.close()

        # Senior IC correctly bucketed (not PEER_ENGINEER — the §3.6 fix).
        assert row["persona"] == "SENIOR_MANAGER"
        # Hook is the specific signal — not GENERIC, not a title category.
        assert row["hook"] == "led bonded composite repair certification"
        # Both raw sources audited in shared_signals.
        assert "profile:" in row["shared_signals"]
        assert "company_news:" in row["shared_signals"]


# ---------------------------------------------------------------------------
# Test: HARD_FAIL + CRITIC_HOLD never reach APPROVED
# ---------------------------------------------------------------------------

class TestNoFlaggedDraftReachesApproval:
    def _seed_selected(self, n: int = 3) -> list[int]:
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
                       VALUES (?, ?, 'Composites Engineer', 'PEER_ENGINEER',
                               'COMPOSITE_DESIGN', ?, ?, 'shared composites work',
                               'SELECTED')""",
                    (
                        company_id, f"Person {i}",
                        f"https://linkedin.com/in/person{i}",
                        f"p{i}@acme.com",
                    ),
                )
                ids.append(c.lastrowid)
        return ids

    def test_placeholder_and_fabrication_drafts_cannot_be_approved(self, capsys, monkeypatch):
        # Force serial execution so the staged response queue matches
        # the per-contact draft order. Parallel workers race for the
        # queue and make the test flaky.
        monkeypatch.setattr("src.agents.drafter._MAX_WORKERS", 1)

        ids = self._seed_selected(3)

        client = _StagedClient()
        # Contact 0 — placeholder leak survives the corrective regen
        # (AUDIT-A1) → HARD_FAIL on LinkedIn connection.
        client.enqueue_draft("Hey [RESEARCH_NEEDED] — wanted to connect.")
        client.enqueue_draft("Hey [RESEARCH_NEEDED] — regen still dirty.")
        client.enqueue_draft("Clean post-connection.")
        client.enqueue_critic_pass()
        client.enqueue_draft("Subject: Hi\n\nClean cold email body.")
        client.enqueue_critic_pass()
        # Contact 1 — fabricated metric on cold email (no source_facts → skipped,
        # so fabrication slips past hard_check; we let critic catch via grounded_facts).
        client.enqueue_draft("Clean connection note.")
        client.enqueue_critic_pass()
        client.enqueue_draft("Clean follow-up note.")
        client.enqueue_critic_pass()
        client.enqueue_draft("Subject: x\n\nClaim of 47% improvement on cycle time.")
        client.enqueue_critic_fail("grounded_facts")
        # Contact 2 — fully clean.
        for _ in range(3):
            client.enqueue_draft("Clean draft body.")
            client.enqueue_critic_pass()

        draft_for_contacts(ids, anthropic_client=client)

        # Try to APPROVE all — both flagged contacts must be blocked.
        inputs = iter(["APPROVE all", "SKIP 1", "SKIP 2", "SKIP 3"])
        result = run_approval_loop(
            company_id=1, _input_fn=lambda _: next(inputs),
        )

        # Only contact 2 (fully clean) should reach approved.
        assert ids[2] in result.approved_contact_ids
        assert ids[0] not in result.approved_contact_ids  # blocked by HARD_FAIL
        assert ids[1] not in result.approved_contact_ids  # blocked by CRITIC_HOLD

        # Gate output is loud.
        out = capsys.readouterr().out
        assert "refusing to approve" in out.lower()

        # Outreach_log only carries the clean contact's drafts.
        conn = get_connection()
        try:
            logs = conn.execute(
                "SELECT DISTINCT contact_id FROM outreach_log"
            ).fetchall()
        finally:
            conn.close()
        assert {r["contact_id"] for r in logs} == {ids[2]}


# ---------------------------------------------------------------------------
# Test: artifact reflects the gate's verdict
# ---------------------------------------------------------------------------

class TestArtifactE2E:
    def test_artifact_surfaces_quality_code_and_persona(self, tmp_path):
        with with_writer() as conn:
            c = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES ('acme', 'Acme', 'DRAFTED')"
            )
            company_id = c.lastrowid
            c = conn.execute(
                "INSERT INTO contacts (company_id, full_name, title, persona, "
                "focus_area, linkedin_url, email, hook, shared_signals, state) "
                "VALUES (?, 'Jane', 'Composites Engineer', 'SENIOR_MANAGER', "
                "'COMPOSITE_DESIGN', 'https://linkedin.com/x', 'j@a.com', "
                "'led bonded repair cert', 'profile: led bonded repair cert', 'DRAFTED')",
                (company_id,),
            )
            contact_id = c.lastrowid
            for ch, code in [
                ("LINKEDIN_CONNECTION", "OK"),
                ("LINKEDIN_POST_CONNECTION", "HARD_FAIL"),
                ("COLD_EMAIL", "CRITIC_HOLD"),
            ]:
                conn.execute(
                    "INSERT INTO drafts (contact_id, channel, body, version, "
                    "quality_flag, quality_code) VALUES (?, ?, 'x', 1, ?, ?)",
                    (contact_id, ch, int(code != "OK"), code),
                )

        path = write_artifact(company_id, _output_dir=tmp_path / "out")
        text = path.read_text()

        assert "**Persona:** SENIOR_MANAGER" in text
        assert "**Focus area:** COMPOSITE_DESIGN" in text
        assert "HARD_FAIL" in text
        assert "CRITIC_HOLD" in text
