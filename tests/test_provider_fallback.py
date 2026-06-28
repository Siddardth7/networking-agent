"""
tests/test_provider_fallback.py
Discovery and email fallback chains in the Finder:
  - _discover: Apify (primary) → Serper (fallback)
  - _resolve_email: Hunter (primary) → Apollo (fallback)
Pure unit tests with stub providers — no DB, no network, no Anthropic.
"""

from __future__ import annotations

import pytest

from src.agents.finder import _discover, _resolve_email
from src.core.schemas import ContactCandidate, EmailResult
from src.providers.retry import QuotaExhausted


def _cand(name: str = "Jane Doe", email: str | None = None) -> ContactCandidate:
    return ContactCandidate(full_name=name, company_slug="acme", email=email)


class _Search:
    """Stub SearchProvider: returns a fixed list or raises a fixed exception."""

    def __init__(self, result=None, raises: Exception | None = None):
        self._result = result or []
        self._raises = raises
        self.calls = 0

    def search_linkedin_profiles(self, company, role_keywords, limit, location=None):
        self.calls += 1
        self.last_location = location
        if self._raises is not None:
            raise self._raises
        return list(self._result)


class _Email:
    """Stub EmailProvider: returns a fixed EmailResult or raises."""

    def __init__(self, email: str | None = None, raises: Exception | None = None):
        self._email = email
        self._raises = raises
        self.calls = 0

    def find_email(self, full_name, company_domain):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return EmailResult(email=self._email, verified=False, confidence=0, source="stub")


# --- _discover ------------------------------------------------------------


def test_accumulates_across_providers_to_limit():
    # v0.6.0 best-effort-to-N: primary returns fewer than the limit, so the
    # fallback tops up (the change from "first non-empty wins").
    apify = _Search(result=[_cand("A")])
    serper = _Search(result=[_cand("B"), _cand("C")])
    out = _discover([apify, serper], company="acme", role_keywords=[], limit=5)
    assert [c.full_name for c in out] == ["A", "B", "C"]
    assert serper.calls == 1


def test_primary_meeting_limit_leaves_fallback_untouched():
    apify = _Search(result=[_cand("A"), _cand("B"), _cand("C")])
    serper = _Search(result=[_cand("D")])
    out = _discover([apify, serper], company="acme", role_keywords=[], limit=3)
    assert [c.full_name for c in out] == ["A", "B", "C"]
    assert serper.calls == 0  # limit already met → fallback never invoked


def test_dedup_across_providers():
    apify = _Search(result=[_cand("A")])
    serper = _Search(result=[_cand("A"), _cand("B")])  # "A" duplicate by name
    out = _discover([apify, serper], company="acme", role_keywords=[], limit=5)
    assert [c.full_name for c in out] == ["A", "B"]


def test_blank_identity_candidate_skipped():
    blank = ContactCandidate(full_name="", company_slug="acme")
    apify = _Search(result=[blank, _cand("A")])
    out = _discover([apify], company="acme", role_keywords=[], limit=5)
    assert [c.full_name for c in out] == ["A"]


def test_shortfall_is_logged(caplog):
    apify = _Search(result=[_cand("A")])
    serper = _Search(result=[])
    with caplog.at_level("WARNING", logger="networking_agent.finder"):
        out = _discover([apify, serper], company="acme", role_keywords=[], limit=5)
    assert [c.full_name for c in out] == ["A"]
    assert any("best-effort 1/5" in r.getMessage() for r in caplog.records)


def test_provider_error_logged_not_silently_swallowed(caplog):
    # FINDER_AUDIT D1: primary errors, fallback runs clean-and-empty → [] but the
    # error is surfaced via the log, not silently dropped.
    apify = _Search(raises=RuntimeError("bad apify key"))
    serper = _Search(result=[])
    with caplog.at_level("WARNING", logger="networking_agent.finder"):
        out = _discover([apify, serper], company="acme", role_keywords=[], limit=5)
    assert out == []
    assert any("bad apify key" in r.getMessage() for r in caplog.records)


def test_all_failed_non_quota_reraises():
    apify = _Search(raises=RuntimeError("boom"))
    serper = _Search(raises=RuntimeError("boom2"))
    with pytest.raises(RuntimeError):
        _discover([apify, serper], company="acme", role_keywords=[], limit=5)


def test_primary_exhausted_falls_back_to_serper():
    apify = _Search(raises=QuotaExhausted("apify", 40, 40))
    serper = _Search(result=[_cand("B")])
    out = _discover([apify, serper], company="acme", role_keywords=[], limit=5)
    assert [c.full_name for c in out] == ["B"]


def test_empty_primary_falls_through_to_fallback():
    apify = _Search(result=[])
    serper = _Search(result=[_cand("B")])
    out = _discover([apify, serper], company="acme", role_keywords=[], limit=5)
    assert [c.full_name for c in out] == ["B"]


def test_all_empty_returns_empty_no_raise():
    out = _discover([_Search([]), _Search([])], company="acme", role_keywords=[], limit=5)
    assert out == []


def test_all_exhausted_reraises_quota():
    apify = _Search(raises=QuotaExhausted("apify", 40, 40))
    serper = _Search(raises=QuotaExhausted("serper", 100, 100))
    with pytest.raises(QuotaExhausted):
        _discover([apify, serper], company="acme", role_keywords=[], limit=5)


# --- _resolve_email -------------------------------------------------------


def _state():
    return {"hunter_exhausted": False, "apollo_exhausted": False}


def test_source_supplied_email_trusted():
    r = _resolve_email(_cand(email="x@acme.com"), _Email(), _Email(), "acme.com", _state())
    assert r.email == "x@acme.com"
    assert r.source == "IMPORT"


def test_hunter_hit_skips_apollo():
    apollo = _Email(email="apollo@acme.com")
    r = _resolve_email(_cand(), _Email(email="h@acme.com"), apollo, "acme.com", _state())
    assert r.email == "h@acme.com"
    assert apollo.calls == 0


def test_hunter_miss_falls_back_to_apollo():
    apollo = _Email(email="a@acme.com")
    r = _resolve_email(_cand(), _Email(email=None), apollo, "acme.com", _state())
    assert r.email == "a@acme.com"
    assert r.source == "stub"  # apollo stub's source; real provider sets "apollo"


def test_hunter_exhausted_uses_apollo_and_stays_exhausted():
    hunter = _Email(raises=QuotaExhausted("hunter", 25, 25))
    apollo = _Email(email="a@acme.com")
    state = _state()
    r1 = _resolve_email(_cand(), hunter, apollo, "acme.com", state)
    assert r1.email == "a@acme.com"
    assert state["hunter_exhausted"] is True
    # Second candidate: hunter is skipped entirely now.
    r2 = _resolve_email(_cand(), hunter, apollo, "acme.com", state)
    assert r2.email == "a@acme.com"
    assert hunter.calls == 1  # not retried after exhaustion


def test_hunter_exhausted_no_apollo_yields_sentinel():
    hunter = _Email(raises=QuotaExhausted("hunter", 25, 25))
    r = _resolve_email(_cand(), hunter, None, "acme.com", _state())
    assert r.email is None
    assert r.source == "HUNTER_EXHAUSTED"


def test_no_providers_email_disabled():
    r = _resolve_email(_cand(), None, None, "acme.com", _state())
    assert r.source == "EMAIL_DISABLED"


def test_apollo_exhausted_no_hunter_yields_apollo_sentinel():
    # D10: Hunter absent, Apollo hits its cap → APOLLO_EXHAUSTED, not "apollo"
    # (which would imply Apollo searched and simply found nothing).
    apollo = _Email(raises=QuotaExhausted("apollo", 0, 0))
    state = _state()
    r = _resolve_email(_cand(), None, apollo, "acme.com", state)
    assert r.email is None
    assert r.source == "APOLLO_EXHAUSTED"
    assert state["apollo_exhausted"] is True
    # Second candidate: Apollo is skipped, still APOLLO_EXHAUSTED.
    r2 = _resolve_email(_cand(), None, apollo, "acme.com", state)
    assert r2.source == "APOLLO_EXHAUSTED"
    assert apollo.calls == 1  # not retried after exhaustion


def test_apollo_ran_empty_still_labeled_apollo():
    # Apollo actually searched and found nothing (not exhausted) → "apollo".
    apollo = _Email(email=None)
    r = _resolve_email(_cand(), None, apollo, "acme.com", _state())
    assert r.email is None
    assert r.source == "apollo"
    assert apollo.calls == 1


def test_location_forwarded_to_provider():
    apify = _Search(result=[_cand("A")])
    _discover([apify], company="acme", role_keywords=[], limit=5, location="Dayton, OH")
    assert apify.last_location == "Dayton, OH"
