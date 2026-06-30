"""
tests/test_exit_gate.py
Phase A exit-gate aggregation (#20): verdict logic + render. Pure, offline.
The live entrypoint (run_exit_gate_trial) is pragma-no-cover (network + LLM).
"""

from __future__ import annotations

from src.eval.exit_gate import LoopStage, evaluate_exit_gate
from src.eval.finder_scorecard import ContactRow, FinderScorecard


def _pass_card() -> FinderScorecard:
    card = FinderScorecard(company_slug="acme", limit=5, rows=[ContactRow(full_name="Alice")])
    card.whitelist_pass = 1  # == found, generic/verbatim default 0 → verdict PASS
    return card


def _review_card() -> FinderScorecard:
    # found > 0 but hook bar not met → "REVIEW — hook bar not met"
    return FinderScorecard(company_slug="acme", limit=5, rows=[ContactRow(full_name="Bob")])


def _full_loop() -> list[LoopStage]:
    return [
        LoopStage("reach", True, "3 drafts"),
        LoopStage("follow-up", True, "1 scheduled"),
        LoopStage("continue", True, "move=SCHEDULE_CALL"),
        LoopStage("outcome", True, "1/1"),
    ]


class TestVerdict:
    def test_pass_when_finder_bar_and_loop_complete(self):
        r = evaluate_exit_gate("acme", _pass_card(), _full_loop(), ranked=True)
        assert r.finder_bar_met is True
        assert r.loop_complete is True
        assert r.verdict == "PASS"

    def test_review_when_not_ranked(self):
        r = evaluate_exit_gate("acme", _pass_card(), _full_loop(), ranked=False)
        assert r.finder_bar_met is False
        assert r.verdict.startswith("REVIEW")

    def test_review_when_finder_card_review(self):
        r = evaluate_exit_gate("acme", _review_card(), _full_loop(), ranked=True)
        assert r.finder_bar_met is False
        assert r.verdict.startswith("REVIEW")

    def test_review_when_finder_card_none(self):
        r = evaluate_exit_gate("acme", None, _full_loop(), ranked=True)
        assert r.finder_bar_met is False
        assert r.verdict.startswith("REVIEW")

    def test_review_when_a_blocking_stage_failed(self):
        stages = _full_loop()
        stages[1] = LoopStage("follow-up", False, "0 scheduled")
        r = evaluate_exit_gate("acme", _pass_card(), stages, ranked=True)
        assert r.loop_complete is False
        assert r.verdict.startswith("REVIEW")

    def test_loop_incomplete_when_no_stages(self):
        r = evaluate_exit_gate("acme", _pass_card(), [], ranked=True)
        assert r.loop_complete is False

    def test_non_blocking_failure_does_not_break_loop(self):
        stages = _full_loop()
        stages.append(LoopStage("optional-extra", False, "skipped", blocking=False))
        r = evaluate_exit_gate("acme", _pass_card(), stages, ranked=True)
        assert r.loop_complete is True
        assert r.verdict == "PASS"


class TestRender:
    def test_render_has_sections_and_verdict(self):
        md = evaluate_exit_gate("acme", _pass_card(), _full_loop(), ranked=True).render_markdown()
        assert "# Phase A exit-gate validation — acme" in md
        assert "**Verdict: PASS**" in md
        assert "Gate 1 — Finder quality bar" in md
        assert "Gate 2 — Loop completeness" in md
        assert "reach" in md and "outcome" in md

    def test_render_with_no_finder_card(self):
        md = evaluate_exit_gate("acme", None, _full_loop(), ranked=False).render_markdown()
        assert "n/a" in md  # finder scorecard row degrades gracefully
        assert "REVIEW" in md

    def test_render_marks_failed_blocking_stage(self):
        stages = [LoopStage("reach", False, "0 drafts")]
        md = evaluate_exit_gate("acme", _pass_card(), stages, ranked=True).render_markdown()
        assert "| reach | FAIL |" in md

    def test_render_marks_nonblocking_as_info(self):
        stages = [
            LoopStage("reach", True, "3 drafts"),
            LoopStage("extra", False, "n/a", blocking=False),
        ]
        md = evaluate_exit_gate("acme", _pass_card(), stages, ranked=True).render_markdown()
        assert "| extra | info |" in md
