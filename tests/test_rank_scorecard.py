"""
tests/test_rank_scorecard.py
Ranking-validation scorecard (#12): ordering metrics over a gold-tiered set.
Pure — the ranker is deterministic and offline.
"""

from __future__ import annotations

from src.core.schemas import Persona
from src.eval.rank_scorecard import (
    HelpTier,
    LabeledRankContact,
    format_scorecard,
    load_labeled_set,
    score_ranking,
)


def _lc(name: str, tier: str, **signals) -> LabeledRankContact:
    return LabeledRankContact(full_name=name, expected_tier=tier, **signals)


class TestLabeledSet:
    def test_tier_accepts_name_string(self):
        lc = _lc("A", "HIGH", alumni_confirmed=True)
        assert lc.expected_tier is HelpTier.HIGH

    def test_tier_accepts_ordinal_int(self):
        lc = LabeledRankContact(full_name="A", expected_tier=3)
        assert lc.expected_tier is HelpTier.HIGH

    def test_to_candidate_carries_signals(self):
        cand = _lc("A", "HIGH", connection_degree="1st", email="a@x.com").to_candidate()
        assert cand.connection_degree == "1st"
        assert cand.email == "a@x.com"

    def test_real_labeled_set_loads_and_passes(self):
        # The shipped labeled set must validate the ranker: clean tier separation.
        card = score_ranking(load_labeled_set())
        assert card.verdict == "PASS"
        assert card.concordance == 1.0
        assert card.top_k_precision == 1.0
        assert card.inversions == []


class TestScoring:
    def test_clean_separation_passes(self):
        labeled = [
            _lc("Strong", "HIGH", alumni_confirmed=True, connection_degree="1st"),
            _lc("Mid", "MED", connection_degree="2nd", persona=Persona.PEER_ENGINEER),
            _lc("Weak", "LOW", linkedin_url="https://li/x"),
        ]
        card = score_ranking(labeled)
        assert card.verdict == "PASS"
        assert card.differing_pairs == 3  # every pair differs in tier
        assert card.concordance == 1.0
        assert card.scored[0].name == "Strong"  # sorted best-first
        assert card.tier_ranges["HIGH"][0] >= card.tier_ranges["MED"][1]

    def test_inversion_detected_and_flags_review(self):
        # Gold says HIGH, but it carries no signal → a LOW-signal contact outranks it.
        labeled = [
            _lc("MislabeledHigh", "HIGH"),  # score 0
            _lc("StrongLow", "LOW", alumni_confirmed=True),  # score 40
        ]
        card = score_ranking(labeled)
        assert len(card.inversions) == 1
        lower, higher = card.inversions[0]
        assert lower.name == "StrongLow" and higher.name == "MislabeledHigh"
        assert card.verdict.startswith("REVIEW")

    def test_top_k_precision_below_one_flags_review(self):
        # Two HIGH gold, but one HIGH has no signal so a MED outranks it → top-2
        # isn't all HIGH.
        labeled = [
            _lc("RealHigh", "HIGH", alumni_confirmed=True, connection_degree="1st"),  # 70
            _lc("EmptyHigh", "HIGH"),  # 0
            _lc("SolidMed", "MED", connection_degree="2nd"),  # 15
        ]
        card = score_ranking(labeled)
        assert card.top_k == 2
        assert card.top_k_precision < 1.0
        assert card.verdict.startswith("REVIEW")

    def test_equal_score_across_tiers_is_a_wash(self):
        # Same score, different tier → neither concordant nor an inversion.
        labeled = [
            _lc("A", "HIGH", linkedin_url="https://li/a"),  # 2
            _lc("B", "LOW", linkedin_url="https://li/b"),  # 2
        ]
        card = score_ranking(labeled)
        assert card.differing_pairs == 1
        assert card.concordant_pairs == 0
        assert card.inversions == []

    def test_no_differing_pairs_concordance_is_one(self):
        labeled = [
            _lc("A", "MED", connection_degree="2nd"),
            _lc("B", "MED", connection_degree="2nd"),
        ]
        card = score_ranking(labeled)
        assert card.differing_pairs == 0
        assert card.concordance == 1.0


class TestRender:
    def test_markdown_pass_path(self):
        labeled = [
            _lc("Strong", "HIGH", alumni_confirmed=True),
            _lc("Weak", "LOW", linkedin_url="https://li/x"),
        ]
        md = format_scorecard(score_ranking(labeled))
        assert "Verdict: PASS" in md
        assert "Ranked order" in md
        assert "Strong" in md
        assert "### Inversions" not in md  # no inversion section when clean

    def test_markdown_inversion_section(self):
        labeled = [_lc("EmptyHigh", "HIGH"), _lc("StrongLow", "LOW", alumni_confirmed=True)]
        md = format_scorecard(score_ranking(labeled))
        assert "### Inversions" in md
        assert "StrongLow" in md

    def test_render_markdown_method_matches_formatter(self):
        card = score_ranking([_lc("A", "HIGH", alumni_confirmed=True)])
        assert card.render_markdown() == format_scorecard(card)
