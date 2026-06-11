"""
tests/test_critic_calibration.py
AUDIT-A3 / AUDIT-A32: critic decision-rule recalibration.

The fixture set below is the REAL 2026-06-06 Joby Aviation run — 30 drafts
(15 contacts x 2 LinkedIn channels; cold email absent because Hunter quota
was exhausted). Score vectors were captured verbatim from
``drafts.critic_trace`` after the migration-003 backfill. Under the old
rule (any dimension < 3 holds) this run held 28/30 drafts (93%) — the
over-correction failure mode documented in DRAFTER_AUDIT_2026-06-06 §3.

The recalibrated rule must land the hold rate in the 20-40% band on this
exact fixture set WITHOUT re-admitting any P0 failure mode:
- placeholder leaks stay HARD_FAIL (hard gate, not the critic)
- fabrication evidence (grounded_facts <= 1) always holds
- egregious single-dimension failures (any score <= 1) always hold
"""

from __future__ import annotations

import pytest

from src.agents.critic import (
    MAX_WEAK_DIMS,
    MIN_SCORE,
    SEVERE_SCORE,
    evaluate_scores,
)
from src.agents.guardrails import hard_check

# (contact, channel, verdict-or-scores)
# "HARD_FAIL" = deterministic gate fired (placeholder / over-length);
# "OK" = passed the original critic with no backfilled trace;
# dict = real backfilled critic score vector.
JOBY_RUN_2026_06_06: list[tuple[str, str, object]] = [
    (
        "Anuj Pant",
        "CONN",
        {
            "specificity": 3,
            "one_ask": 3,
            "tone": 3,
            "grounded_facts": 1,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Anuj Pant",
        "POST",
        {
            "specificity": 2,
            "one_ask": 2,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Arun Bhure",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 1,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Arun Bhure",
        "POST",
        {
            "specificity": 3,
            "one_ask": 4,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Jaime Preston",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Jaime Preston",
        "POST",
        {
            "specificity": 2,
            "one_ask": 2,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Jimmy Shedden",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 1,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Jimmy Shedden",
        "POST",
        {
            "specificity": 2,
            "one_ask": 1,
            "tone": 4,
            "grounded_facts": 4,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Jose Zarate",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 3,
            "tone": 4,
            "grounded_facts": 2,
            "economy": 4,
            "relevance": 4,
        },
    ),
    (
        "Jose Zarate",
        "POST",
        {
            "specificity": 3,
            "one_ask": 2,
            "tone": 4,
            "grounded_facts": 4,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Marc Le",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 1,
            "economy": 4,
            "relevance": 3,
        },
    ),
    ("Marc Le", "POST", "OK"),
    (
        "Michael Tucker",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 2,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Michael Tucker",
        "POST",
        {
            "specificity": 2,
            "one_ask": 2,
            "tone": 3,
            "grounded_facts": 2,
            "economy": 3,
            "relevance": 3,
        },
    ),
    (
        "Mitchell Garrity",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 4,
            "grounded_facts": 2,
            "economy": 5,
            "relevance": 2,
        },
    ),
    (
        "Mitchell Garrity",
        "POST",
        {
            "specificity": 3,
            "one_ask": 2,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 2,
        },
    ),
    (
        "Morgan Mader",
        "CONN",
        {
            "specificity": 3,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 3,
            "economy": 4,
            "relevance": 3,
        },
    ),
    ("Morgan Mader", "POST", "OK"),
    ("Nathan DeGraaff", "CONN", "HARD_FAIL"),  # placeholder leak
    (
        "Nathan DeGraaff",
        "POST",
        {
            "specificity": 2,
            "one_ask": 3,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Sarah Senay",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 4,
            "grounded_facts": 2,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Sarah Senay",
        "POST",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Tanveer Shakeel",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 2,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Tanveer Shakeel",
        "POST",
        {
            "specificity": 2,
            "one_ask": 3,
            "tone": 4,
            "grounded_facts": 2,
            "economy": 3,
            "relevance": 3,
        },
    ),
    (
        "Valerie Brown",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 2,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Valerie Brown",
        "POST",
        {
            "specificity": 2,
            "one_ask": 3,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    ("Yucheng Luo", "CONN", "HARD_FAIL"),  # over-length note
    (
        "Yucheng Luo",
        "POST",
        {
            "specificity": 3,
            "one_ask": 2,
            "tone": 4,
            "grounded_facts": 3,
            "economy": 3,
            "relevance": 4,
        },
    ),
    (
        "Yueyang Hu",
        "CONN",
        {
            "specificity": 2,
            "one_ask": 4,
            "tone": 3,
            "grounded_facts": 2,
            "economy": 4,
            "relevance": 3,
        },
    ),
    (
        "Yueyang Hu",
        "POST",
        {
            "specificity": 2,
            "one_ask": 1,
            "tone": 3,
            "grounded_facts": 3,
            "economy": 2,
            "relevance": 3,
        },
    ),
]


def _held(verdict: object) -> bool:
    """Apply the full gate (hard checks + recalibrated critic) to one row."""
    if verdict == "HARD_FAIL":
        return True
    if verdict == "OK":
        return False
    passed, _failing = evaluate_scores(verdict)
    return not passed


class TestHoldRateBand:
    def test_hold_rate_lands_in_20_40_band(self):
        held = sum(1 for _, _, v in JOBY_RUN_2026_06_06 if _held(v))
        total = len(JOBY_RUN_2026_06_06)
        rate = held / total
        assert 0.20 <= rate <= 0.40, (
            f"hold rate {rate:.0%} ({held}/{total}) outside the 20-40% band"
        )


class TestNamedRegressionFixtures:
    """The specific drafts called out in DRAFTER_AUDIT_2026-06-06."""

    def _verdict(self, name: str, channel: str) -> object:
        return next(v for n, c, v in JOBY_RUN_2026_06_06 if n == name and c == channel)

    def test_morgan_post_still_passes(self):
        assert not _held(self._verdict("Morgan Mader", "POST"))

    def test_marc_post_still_passes(self):
        assert not _held(self._verdict("Marc Le", "POST"))

    def test_morgan_conn_no_longer_overheld(self):
        # All dimensions >= 3 — the old any-dim rule held it anyway via
        # backfill noise; the new rule must pass it.
        assert not _held(self._verdict("Morgan Mader", "CONN"))

    def test_yueyang_post_still_fails_multi_ask(self):
        # one_ask = 1 is an egregious failure — must keep holding.
        assert _held(self._verdict("Yueyang Hu", "POST"))

    def test_nathan_conn_still_fails_placeholder(self):
        assert _held(self._verdict("Nathan DeGraaff", "CONN"))

    def test_fabrication_evidence_always_holds(self):
        # grounded_facts <= 1 is the P0 fabrication signal — every such
        # draft in the run must still hold (Anuj/Arun/Jimmy/Marc CONN).
        for name in ("Anuj Pant", "Arun Bhure", "Jimmy Shedden", "Marc Le"):
            verdict = self._verdict(name, "CONN")
            assert _held(verdict), f"{name} CONN re-admitted fabrication risk"


class TestDecisionRuleProperties:
    def test_any_severe_dimension_holds(self):
        scores = {
            d: 5
            for d in (
                "specificity",
                "one_ask",
                "tone",
                "grounded_facts",
                "economy",
                "relevance",
            )
        }
        scores["tone"] = SEVERE_SCORE
        passed, failing = evaluate_scores(scores)
        assert not passed
        assert failing == ["tone"]

    def test_single_weak_dimension_passes(self):
        scores = {
            d: 4
            for d in (
                "specificity",
                "one_ask",
                "tone",
                "grounded_facts",
                "economy",
                "relevance",
            )
        }
        scores["specificity"] = MIN_SCORE - 1
        passed, _ = evaluate_scores(scores)
        assert passed

    def test_too_many_weak_dimensions_hold(self):
        scores = {
            d: 4
            for d in (
                "specificity",
                "one_ask",
                "tone",
                "grounded_facts",
                "economy",
                "relevance",
            )
        }
        for dim in ("specificity", "tone", "economy"):
            scores[dim] = MIN_SCORE - 1
        assert len([s for s in scores.values() if s < MIN_SCORE]) > MAX_WEAK_DIMS
        passed, failing = evaluate_scores(scores)
        assert not passed
        assert set(failing) == {"specificity", "tone", "economy"}

    def test_all_clean_passes(self):
        scores = {
            d: 3
            for d in (
                "specificity",
                "one_ask",
                "tone",
                "grounded_facts",
                "economy",
                "relevance",
            )
        }
        passed, failing = evaluate_scores(scores)
        assert passed
        assert failing == []


class TestPlaceholderStringsStillHardFail:
    """P0 non-readmission: the hard gate is independent of critic tuning."""

    @pytest.mark.parametrize(
        "body",
        [
            "Saw your work at [" + "RESEARCH_NEEDED] recently.",
            "Excited about [COMPANY]'s mission and [TEAM].",
            "Your work on [PROGRAM_NAME] stood out.",
        ],
    )
    def test_placeholder_hard_fails(self, body):
        result = hard_check(body, channel="LINKEDIN_POST_CONNECTION")
        assert not result.passed
        assert result.quality_code == "HARD_FAIL"
