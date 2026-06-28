"""
tests/test_hook_quality.py
AUDIT-A4 / AUDIT-A5: hook generation quality.

- Raw company-news strings are NEVER returned as a hook (the June-6 run
  pasted "May 15 2026. Joby's Commitment to Sustainable Aviation · ..."
  verbatim into two contacts' hooks).
- Hooks must match an acceptable shape (is_acceptable_hook whitelist).
- A title-derived hook is preferred over GENERIC when no better signal
  exists, so the drafter always has something real to anchor on before
  it would ever reach for a placeholder.
"""

from __future__ import annotations

from src.agents.finder import (
    _generate_hook,
    is_acceptable_hook,
    looks_like_verbatim_news,
)
from src.core.schemas import ContactCandidate

# The literal hook string that shipped in the 2026-06-06 run (Michael
# Tucker / Tanveer) — the regression this module exists to prevent.
JUNE6_NEWS_HOOK = (
    "May 15 2026. Joby's Commitment to Sustainable Aviation · May 5 2026. "
    "Joby Reports First Quarter 2026 Financial Results."
)


def _candidate(title: str = "Project Lead") -> ContactCandidate:
    return ContactCandidate(
        full_name="X",
        title=title,
        linkedin_url="https://linkedin.com/in/x",
        company_slug="acme",
    )


class TestVerbatimNewsDetector:
    def test_june6_offender_detected(self):
        assert looks_like_verbatim_news(JUNE6_NEWS_HOOK)

    def test_dateline_detected(self):
        assert looks_like_verbatim_news("May 15 2026. Acme opens new facility")

    def test_headline_separator_detected(self):
        assert looks_like_verbatim_news("Acme expands · Acme hires CFO")

    def test_financial_results_phrase_detected(self):
        assert looks_like_verbatim_news("Acme Reports First Quarter 2026 Financial Results")

    def test_funding_news_detected(self):
        assert looks_like_verbatim_news(
            "Acme closed Series D funding for autonomous flight testing."
        )

    def test_personal_signals_not_flagged(self):
        for signal in (
            "led 787 empennage stress team",
            "certified Six Sigma Black Belt",
            "MS at Georgia Tech in composites",
            "recent SAMPE paper on bonded repair",
            "20+ years in design and manufacturing",
            "led launch vehicle structures team",
        ):
            assert not looks_like_verbatim_news(signal), signal


class TestAcceptableHookShapes:
    def test_generic_sentinel_rejected(self):
        assert not is_acceptable_hook("GENERIC")

    def test_empty_rejected(self):
        assert not is_acceptable_hook("")
        assert not is_acceptable_hook(None)

    def test_news_string_rejected(self):
        assert not is_acceptable_hook(JUNE6_NEWS_HOOK)

    def test_overlong_rejected(self):
        assert not is_acceptable_hook("x" * 200)

    def test_multiline_rejected(self):
        assert not is_acceptable_hook("line one\nline two")

    def test_real_signals_accepted(self):
        for hook in (
            "led 787 empennage stress team",
            "we share a UIUC background",
            "your composites work",
            "your work as Senior MRB Engineer",
        ):
            assert is_acceptable_hook(hook), hook

    def test_single_marker_personal_signal_accepted(self):
        # D6: the strict news check tripped on a single marker, demoting real
        # signals. One marker (a "reports to…" or a lone "May 5") is normal
        # phrasing and must now pass the gate.
        for hook in (
            "reports to the VP of Structures",
            "led 787 stress team since May 5",
        ):
            assert is_acceptable_hook(hook), hook

    def test_two_marker_headline_still_rejected(self):
        # Two co-occurring markers ("Reports" + "Financial Results") is a
        # pasted headline, not a personal signal — still rejected (D6).
        assert not is_acceptable_hook(
            "Acme Reports First Quarter 2026 Financial Results"
        )

    def test_separator_headline_still_rejected(self):
        assert not is_acceptable_hook("Acme expands · Acme hires CFO")


class TestHookGeneration:
    def test_news_never_returned_as_hook(self):
        hook = _generate_hook(_candidate(), company_news=JUNE6_NEWS_HOOK)
        assert hook != JUNE6_NEWS_HOOK
        assert not looks_like_verbatim_news(hook)

    def test_title_derived_hook_before_generic(self):
        # "Project Lead" matches no specialty bucket; with no signal and no
        # news the hook anchors on the real title instead of GENERIC.
        hook = _generate_hook(_candidate(title="Project Lead"))
        assert hook == "your work as Project Lead"

    def test_generic_only_when_no_title(self):
        hook = _generate_hook(_candidate(title=""))
        assert hook == "GENERIC"

    def test_news_like_classifier_signal_rejected(self):
        # If the classifier extracted a news headline as the "personal"
        # signal, it must not be promoted to Tier 0.
        hook = _generate_hook(
            _candidate(title="Composites Engineer"),
            hook_signal="Acme Reports First Quarter 2026 Financial Results",
        )
        assert hook == "your composites work"

    def test_specialty_bucket_still_preferred_over_title_echo(self):
        hook = _generate_hook(_candidate(title="Composites Engineer"))
        assert hook == "your composites work"

    def test_shared_employer_matched_via_company_slug(self):
        # D11: a current Boeing employee titled "Structures Engineer" (no
        # employer in the title) trips Tier-2 via the company slug.
        cand = ContactCandidate(
            full_name="X",
            title="Structures Engineer",
            linkedin_url="https://linkedin.com/in/x",
            company_slug="boeing",
        )
        assert _generate_hook(cand) == "you also spent time at Boeing"

    def test_multiword_employer_slug_matched(self):
        # Slug dashes become spaces so "general-electric" matches the "general
        # electric" employer (D11).
        cand = ContactCandidate(
            full_name="X",
            title="Reliability Engineer",
            linkedin_url="https://linkedin.com/in/x",
            company_slug="general-electric",
        )
        assert _generate_hook(cand) == "you also spent time at General Electric"

    def test_long_title_truncated_in_hook(self):
        long_title = "Senior Principal Staff " + "Program " * 20 + "Lead"
        hook = _generate_hook(_candidate(title=long_title))
        assert hook.startswith("your work as ")
        assert len(hook) <= 120
