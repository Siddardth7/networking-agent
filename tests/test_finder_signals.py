"""
tests/test_finder_signals.py
Layer 1: finder consumes Serper snippet → classifier extracts hook_signal →
Tier 0 hook → shared_signals persisted; Tier 4 falls back to company news.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.agents.finder import (
    _classify_contact,
    _fetch_company_news_signal,
    _generate_hook,
    find_contacts,
)
from src.core.db import get_connection, init_db
from src.core.schemas import ContactCandidate, EmailResult, FocusArea, Persona

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


def _make_classifier_response(persona: str, focus_area: str, hook_signal: str = "") -> Mock:
    tool = Mock()
    tool.type = "tool_use"
    tool.input = {"persona": persona, "focus_area": focus_area, "hook_signal": hook_signal}
    resp = Mock()
    resp.content = [tool]
    return resp


class TestPersonaFocusOverride:
    """Issue #5: focus_area is deterministically forced for non-engineer personas
    (ALUMNI -> ALUMNI_ACADEMIC, RECRUITER -> PEER), regardless of the model's
    topic guess; engineer personas keep the model's focus."""

    def _cand(self) -> ContactCandidate:
        return ContactCandidate(full_name="A", title="t", company_slug="x")

    def test_alumni_forced_to_academic_focus(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "ALUMNI", "COMPOSITE_DESIGN"
        )
        persona, focus, _ = _classify_contact(self._cand(), "x", client)
        assert persona is Persona.ALUMNI
        assert focus is FocusArea.ALUMNI_ACADEMIC  # overridden, not COMPOSITE_DESIGN

    def test_recruiter_forced_to_peer_focus(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "RECRUITER", "MANUFACTURING"
        )
        persona, focus, _ = _classify_contact(self._cand(), "x", client)
        assert persona is Persona.RECRUITER
        assert focus is FocusArea.PEER  # overridden, not MANUFACTURING

    def test_engineer_focus_preserved(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER", "COMPOSITE_DESIGN"
        )
        persona, focus, _ = _classify_contact(self._cand(), "x", client)
        assert persona is Persona.PEER_ENGINEER
        assert focus is FocusArea.COMPOSITE_DESIGN  # NOT overridden

    def test_senior_manager_focus_preserved(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "SENIOR_MANAGER", "STRUCTURAL_ANALYSIS"
        )
        _, focus, _ = _classify_contact(self._cand(), "x", client)
        assert focus is FocusArea.STRUCTURAL_ANALYSIS  # NOT overridden


# ---------------------------------------------------------------------------
# Hook tier ordering
# ---------------------------------------------------------------------------


class TestHookTiers:
    def _candidate(
        self, title: str = "Stress Engineer", url: str = "https://linkedin.com/in/x"
    ) -> ContactCandidate:
        return ContactCandidate(full_name="A", title=title, linkedin_url=url, company_slug="x")

    def test_tier0_hook_signal_wins_over_title_specialty(self):
        # Even though title would trigger Tier 3 ("structures work"),
        # an explicit hook_signal takes precedence.
        hook = _generate_hook(
            self._candidate(title="Stress Engineer"),
            hook_signal="led 787 empennage stress team",
        )
        assert hook == "led 787 empennage stress team"

    def test_tier0_overrides_uiuc(self):
        # Tier 0 beats Tier 1 too — most-specific wins.
        hook = _generate_hook(
            self._candidate(url="https://linkedin.com/in/uiuc-alum"),
            hook_signal="recent SAMPE paper on bonded repair",
        )
        assert hook == "recent SAMPE paper on bonded repair"

    def test_tier3_used_when_no_signal_or_news(self):
        hook = _generate_hook(self._candidate(title="Composites Engineer"))
        assert hook == "your composites work"

    def test_company_news_never_used_as_hook(self):
        # AUDIT-A4: news is phrasing material in shared_signals, never the
        # hook string. With no better signal the title-derived hook wins.
        hook = _generate_hook(
            ContactCandidate(
                full_name="X",
                title="Project Lead",
                linkedin_url="https://linkedin.com/in/x",
                company_slug="acme",
            ),
            company_news="Acme closed Series D funding for autonomous flight testing.",
        )
        assert hook == "your work as Project Lead"

    def test_generic_when_nothing_matches(self):
        # AUDIT-A5: GENERIC is reachable only when even the title is empty.
        hook = _generate_hook(
            ContactCandidate(
                full_name="X",
                title=None,
                linkedin_url="https://linkedin.com/in/x",
                company_slug="acme",
            ),
        )
        assert hook == "GENERIC"

    def test_empty_hook_signal_falls_through(self):
        # Empty string must be treated like None — not promoted to Tier 0.
        hook = _generate_hook(
            self._candidate(title="Composites Engineer"),
            hook_signal="",
        )
        assert hook == "your composites work"


# ---------------------------------------------------------------------------
# Classifier output shape — returns 3-tuple
# ---------------------------------------------------------------------------


class TestClassifierTuple:
    def test_returns_persona_focus_signal(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "COMPOSITE_DESIGN",
            hook_signal="led bonded repair certification effort",
        )
        candidate = ContactCandidate(
            full_name="J",
            title="Composites Engineer",
            linkedin_url="https://linkedin.com/in/j",
            company_slug="x",
            snippet="Composites engineer; led bonded repair certification effort at OEM X.",
        )
        persona, focus, signal = _classify_contact(candidate, "x", client)
        assert persona == Persona.PEER_ENGINEER
        assert focus == FocusArea.COMPOSITE_DESIGN
        assert signal == "led bonded repair certification effort"

    def test_empty_signal_returns_none(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "PEER",
            hook_signal="",
        )
        candidate = ContactCandidate(
            full_name="J",
            title="Engineer",
            linkedin_url="https://linkedin.com/in/j",
            company_slug="x",
        )
        _, _, signal = _classify_contact(candidate, "x", client)
        assert signal is None

    def test_oversized_signal_truncated_to_80_chars(self):
        long = "x" * 200
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "PEER",
            hook_signal=long,
        )
        candidate = ContactCandidate(
            full_name="J",
            title="Engineer",
            linkedin_url="https://linkedin.com/in/j",
            company_slug="x",
        )
        _, _, signal = _classify_contact(candidate, "x", client)
        assert signal is not None
        assert len(signal) == 80


class TestTrimHookSignal:
    """#10 trial residual: an overshooting hook signal must trim on a word
    boundary, not mid-word ('…large assembly stru')."""

    def test_short_unchanged(self):
        from src.agents.finder import _trim_hook_signal

        assert _trim_hook_signal("led 787 stress team") == "led 787 stress team"

    def test_exactly_at_ceiling_unchanged(self):
        from src.agents.finder import _MAX_HOOK_SIGNAL_LEN, _trim_hook_signal

        text = "a" * _MAX_HOOK_SIGNAL_LEN
        assert _trim_hook_signal(text) == text

    def test_overshoot_trims_on_word_boundary(self):
        from src.agents.finder import _MAX_HOOK_SIGNAL_LEN, _trim_hook_signal

        text = (
            "Four years in aerospace manufacturing, quality assurance "
            "and large assembly structures"
        )
        out = _trim_hook_signal(text)
        assert len(out) <= _MAX_HOOK_SIGNAL_LEN
        assert not out.endswith("stru")  # no mid-word cut
        assert out == text[: len(out)]  # a clean prefix, ending on a whole word
        assert not out.endswith(" ")

    def test_single_overlong_token_hard_cut(self):
        from src.agents.finder import _MAX_HOOK_SIGNAL_LEN, _trim_hook_signal

        out = _trim_hook_signal("x" * 200)
        assert len(out) == _MAX_HOOK_SIGNAL_LEN

    def test_snippet_passed_into_user_message(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "PEER",
            hook_signal="",
        )
        candidate = ContactCandidate(
            full_name="J",
            title="Engineer",
            linkedin_url="https://linkedin.com/in/j",
            company_slug="x",
            snippet="UNIQUESNIPPETMARKER123 about composites.",
        )
        _classify_contact(candidate, "x", client)
        # Inspect the user message that was sent to the model.
        kwargs = client.messages.create.call_args.kwargs
        user_msg = kwargs["messages"][0]["content"]
        assert "UNIQUESNIPPETMARKER123" in user_msg

    def test_missing_snippet_uses_none_marker(self):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "PEER",
            hook_signal="",
        )
        candidate = ContactCandidate(
            full_name="J",
            title="Engineer",
            linkedin_url="https://linkedin.com/in/j",
            company_slug="x",
            snippet=None,
        )
        _classify_contact(candidate, "x", client)
        user_msg = client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "(none available)" in user_msg


# ---------------------------------------------------------------------------
# Tier 4: _fetch_company_news_signal
# ---------------------------------------------------------------------------


class TestFetchCompanyNews:
    def test_returns_first_non_empty_snippet(self):
        sp = Mock()
        sp.search_general.return_value = "Joby announced eVTOL certification milestone."
        result = _fetch_company_news_signal("joby-aviation", sp)
        assert "Joby announced" in result

    def test_oversized_news_snippet_truncated(self):
        sp = Mock()
        sp.search_general.return_value = "x" * 500
        result = _fetch_company_news_signal("acme", sp)
        assert result is not None
        assert result.endswith("...")
        assert len(result) <= 120

    def test_none_when_no_signal(self):
        sp = Mock()
        sp.search_general.return_value = None
        assert _fetch_company_news_signal("acme", sp) is None

    def test_swallows_provider_errors(self):
        sp = Mock()
        sp.search_general.side_effect = RuntimeError("boom")
        assert _fetch_company_news_signal("acme", sp) is None

    def test_unexpected_type_yields_none(self):
        sp = Mock()
        # Default Mock attribute return (not a string) must degrade silently.
        del sp.search_general.return_value  # restore Mock-default behavior
        assert _fetch_company_news_signal("acme", sp) is None


# ---------------------------------------------------------------------------
# find_contacts integration: shared_signals persisted with snippet + news
# ---------------------------------------------------------------------------


class TestFindContactsPersistsSignals:
    def _candidate(self, name: str, snippet: str) -> ContactCandidate:
        return ContactCandidate(
            full_name=name,
            title="Composites Engineer",
            linkedin_url=f"https://linkedin.com/in/{name.lower()}",
            company_slug="acme-corp",
            snippet=snippet,
        )

    def test_shared_signals_written_with_profile_and_news(self, db_path):
        init_db()
        sp = Mock()
        sp.search_linkedin_profiles.return_value = [
            self._candidate("Alice", "Led bonded composite repair certification at OEM X."),
        ]
        sp.search_general.return_value = "Acme announced new composites factory in 2026."

        hp = Mock()
        hp.find_email.return_value = EmailResult(
            email="a@acme.com",
            verified=True,
            confidence=90,
            source="hunter",
        )

        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "COMPOSITE_DESIGN",
            hook_signal="led bonded composite repair certification",
        )

        find_contacts(
            "acme-corp", limit=1, serper_provider=sp, hunter_provider=hp, anthropic_client=client
        )

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT hook, shared_signals FROM contacts WHERE full_name = 'Alice'"
            ).fetchone()
        finally:
            conn.close()

        # Hook is the specific signal — not "GENERIC", not a category.
        assert row["hook"] == "led bonded composite repair certification"
        # shared_signals carries both raw sources for auditability.
        assert row["shared_signals"] is not None
        assert "profile:" in row["shared_signals"]
        assert "company_news:" in row["shared_signals"]

    def test_no_snippet_no_news_still_writes_hook(self, db_path):
        init_db()
        sp = Mock()
        sp.search_linkedin_profiles.return_value = [
            ContactCandidate(
                full_name="Bob",
                title="Composites Engineer",
                linkedin_url="https://linkedin.com/in/bob",
                company_slug="acme-corp",
                snippet=None,
            ),
        ]
        sp.search_general.return_value = None

        hp = Mock()
        hp.find_email.return_value = EmailResult(
            email=None,
            verified=False,
            confidence=0,
            source="hunter",
        )

        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "COMPOSITE_DESIGN",
            hook_signal="",
        )

        find_contacts(
            "acme-corp", limit=1, serper_provider=sp, hunter_provider=hp, anthropic_client=client
        )

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT hook, shared_signals FROM contacts WHERE full_name = 'Bob'"
            ).fetchone()
        finally:
            conn.close()

        # Tier 3 falls back to title bucket — never GENERIC for a composites title.
        assert row["hook"] == "your composites work"
        # No raw sources, so shared_signals is NULL.
        assert row["shared_signals"] is None


class TestImportedContactTier0Hook:
    """D7 (#9): an imported contact with persona + focus pre-set still mines
    its snippet for a Tier-0 hook instead of dropping to a title bucket."""

    def _company(self) -> int:
        from src.core.db import with_writer

        init_db()
        with with_writer() as conn:
            cur = conn.execute(
                "INSERT INTO companies (slug, name, state) VALUES (?, ?, 'FOUND')",
                ("acme-corp", "Acme Corp"),
            )
            return int(cur.lastrowid)

    def _candidate(self, **over) -> ContactCandidate:
        base = dict(
            full_name="Imported Person",
            title="Composites Engineer",
            linkedin_url="https://linkedin.com/in/imported",
            company_slug="acme-corp",
            persona=Persona.PEER_ENGINEER,
            focus_area=FocusArea.COMPOSITE_DESIGN,
            snippet="Led the bonded composite repair certification at OEM X.",
        )
        base.update(over)
        return ContactCandidate(**base)

    def _hook_for(self, db_path, candidate, client) -> str:
        from src.agents.finder import ingest_contacts

        company_id = self._company()
        ingest_contacts(
            [candidate], company_id, "acme-corp", anthropic_client=client
        )
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT hook FROM contacts WHERE full_name = ?",
                (candidate.full_name,),
            ).fetchone()["hook"]
        finally:
            conn.close()

    def test_snippet_mined_for_tier0_despite_forced_labels(self, db_path):
        client = Mock()
        client.messages.create.return_value = _make_classifier_response(
            "PEER_ENGINEER",
            "COMPOSITE_DESIGN",
            hook_signal="led bonded composite repair certification",
        )
        hook = self._hook_for(db_path, self._candidate(), client)
        # The classifier ran (purely to mine the signal) and Tier-0 landed.
        assert client.messages.create.call_count == 1
        assert hook == "led bonded composite repair certification"

    def test_explicit_hook_skips_classifier(self, db_path):
        client = Mock()
        hook = self._hook_for(
            db_path, self._candidate(hook="we met at SAMPE 2025"), client
        )
        # An explicit hook means no reason to call the model at all.
        assert client.messages.create.call_count == 0
        assert hook == "we met at SAMPE 2025"

    def test_no_snippet_no_classifier_call(self, db_path):
        client = Mock()
        hook = self._hook_for(db_path, self._candidate(snippet=None), client)
        # Nothing to mine → classifier skipped → deterministic title bucket.
        assert client.messages.create.call_count == 0
        assert hook == "your composites work"
