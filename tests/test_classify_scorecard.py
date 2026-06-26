"""
tests/test_classify_scorecard.py
Unit tests for the classify-accuracy scorecard harness (issue #4).
Deterministic — uses fake classifiers, no API key, runs under the coverage gate.
The live entrypoint (_build_live_classify_fn/main) is pragma: no cover.
"""

from __future__ import annotations

import json

from src.core.schemas import FocusArea, Persona
from src.eval.classify_scorecard import (
    ClassMetrics,
    LabeledContact,
    format_scorecard,
    load_labeled_set,
    score_classifier,
)

# --------------------------------------------------------------------------
# Tiny in-memory fixtures for exact-number assertions
# --------------------------------------------------------------------------


def _lc(persona: Persona, focus: FocusArea, name: str = "X") -> LabeledContact:
    return LabeledContact(
        full_name=name,
        title="t",
        company_slug="acme",
        expected_persona=persona,
        expected_focus_area=focus,
    )


def _perfect(lc: LabeledContact) -> tuple[Persona, FocusArea]:
    return lc.expected_persona, lc.expected_focus_area


def _always_peer(lc: LabeledContact) -> tuple[Persona, FocusArea]:
    return Persona.PEER_ENGINEER, FocusArea.PEER


# --------------------------------------------------------------------------
# ClassMetrics math (incl. zero-division edges)
# --------------------------------------------------------------------------


def test_class_metrics_normal():
    m = ClassMetrics(label="X", support=4, predicted=5, correct=3)
    assert m.precision == 3 / 5
    assert m.recall == 3 / 4
    assert abs(m.f1 - (2 * (3 / 5) * (3 / 4)) / ((3 / 5) + (3 / 4))) < 1e-9


def test_class_metrics_zero_division():
    empty = ClassMetrics(label="X", support=0, predicted=0, correct=0)
    assert empty.precision == 0.0
    assert empty.recall == 0.0
    assert empty.f1 == 0.0
    # predicted>0 but none correct -> precision 0; support>0 none correct -> recall 0
    wrong = ClassMetrics(label="X", support=2, predicted=3, correct=0)
    assert wrong.precision == 0.0
    assert wrong.recall == 0.0
    assert wrong.f1 == 0.0


# --------------------------------------------------------------------------
# score_classifier
# --------------------------------------------------------------------------


def test_perfect_classifier_scores_100():
    labeled = [
        _lc(Persona.ALUMNI, FocusArea.ALUMNI_ACADEMIC),
        _lc(Persona.RECRUITER, FocusArea.PEER),
        _lc(Persona.PEER_ENGINEER, FocusArea.STRUCTURAL_ANALYSIS),
    ]
    card = score_classifier(labeled, _perfect)
    assert card.n == 3
    assert card.persona.accuracy == 1.0
    assert card.focus_area.accuracy == 1.0
    assert card.errors == []
    assert card.persona.macro_f1 == 1.0
    # every active class is perfect
    assert card.persona.per_class["ALUMNI"].precision == 1.0
    assert card.persona.per_class["ALUMNI"].recall == 1.0


def test_confusion_and_errors_tracked():
    labeled = [
        _lc(Persona.ALUMNI, FocusArea.ALUMNI_ACADEMIC, "a"),
        _lc(Persona.RECRUITER, FocusArea.PEER, "b"),
        _lc(Persona.PEER_ENGINEER, FocusArea.PEER, "c"),
    ]
    card = score_classifier(labeled, _always_peer)
    # Only the PEER_ENGINEER/PEER contact is fully correct.
    assert card.persona.accuracy == 1 / 3
    assert card.focus_area.accuracy == 2 / 3  # two have PEER focus gold
    assert len(card.errors) == 2  # a and b mispredicted
    # confusion: ALUMNI gold predicted PEER_ENGINEER
    assert card.persona.confusion["ALUMNI"]["PEER_ENGINEER"] == 1
    assert card.persona.confusion["RECRUITER"]["PEER_ENGINEER"] == 1
    # PEER_ENGINEER predicted 3 times, correct once -> precision 1/3
    assert card.persona.per_class["PEER_ENGINEER"].predicted == 3
    assert card.persona.per_class["PEER_ENGINEER"].correct == 1
    assert card.persona.per_class["PEER_ENGINEER"].precision == 1 / 3
    # ALUMNI never predicted -> precision 0, recall 0
    assert card.persona.per_class["ALUMNI"].recall == 0.0


# --------------------------------------------------------------------------
# format_scorecard (both error/no-error branches)
# --------------------------------------------------------------------------


def test_format_scorecard_with_errors():
    labeled = [_lc(Persona.ALUMNI, FocusArea.ALUMNI_ACADEMIC, "a")]
    out = format_scorecard(score_classifier(labeled, _always_peer))
    assert "Classify accuracy scorecard (n=1)" in out
    assert "Mispredictions (1)" in out
    assert "| contact |" in out  # the error table header
    assert "persona — accuracy" in out


def test_format_scorecard_no_errors():
    labeled = [_lc(Persona.ALUMNI, FocusArea.ALUMNI_ACADEMIC, "a")]
    out = format_scorecard(score_classifier(labeled, _perfect))
    assert "Mispredictions (0)" in out
    assert "every contact classified correctly" in out


# --------------------------------------------------------------------------
# load_labeled_set + dataset quality (qa-expert-supervised ground truth)
# --------------------------------------------------------------------------


def test_load_labeled_set_default():
    labeled = load_labeled_set()
    assert len(labeled) >= 24  # expanded in #5 for ≥95% discriminating power
    # every persona is represented
    personas = {lc.expected_persona for lc in labeled}
    assert personas == set(Persona)
    # every focus area is represented
    focuses = {lc.expected_focus_area for lc in labeled}
    assert focuses == set(FocusArea)


def test_load_labeled_set_custom_path(tmp_path):
    p = tmp_path / "tiny.json"
    p.write_text(
        json.dumps(
            [
                {
                    "full_name": "Z",
                    "title": "Engineer",
                    "company_slug": "acme",
                    "expected_persona": "PEER_ENGINEER",
                    "expected_focus_area": "PEER",
                }
            ]
        )
    )
    labeled = load_labeled_set(p)
    assert len(labeled) == 1
    assert labeled[0].expected_persona is Persona.PEER_ENGINEER
