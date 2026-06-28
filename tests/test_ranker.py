"""
tests/test_ranker.py
Referral-likelihood ranking (#11): deterministic, explainable per-signal scoring.
"""

from __future__ import annotations

from src.agents.ranker import _WEIGHTS, RankScore, _norm_degree, rank_contact
from src.core.schemas import ContactCandidate, FocusArea, Persona


def _cand(**over) -> ContactCandidate:
    base = dict(full_name="X", company_slug="acme")
    base.update(over)
    return ContactCandidate(**base)


class TestSignals:
    def test_no_signals_scores_zero(self):
        s = rank_contact(_cand())
        assert s.total == 0
        assert s.summary() == "no referral signals"

    def test_confirmed_alumnus_beats_classified(self):
        confirmed = rank_contact(_cand(alumni_confirmed=True))
        classified = rank_contact(_cand(persona=Persona.ALUMNI))
        assert confirmed.total == _WEIGHTS["alumni_confirmed"]
        # persona==ALUMNI is neither PEER_ENGINEER nor SENIOR_MANAGER → no baseline.
        assert classified.total == _WEIGHTS["alumni_classified"]
        assert confirmed.total > classified.total

    def test_confirmed_does_not_double_count_classified(self):
        # alumni_confirmed wins; the persona==ALUMNI branch must not also fire.
        s = rank_contact(_cand(alumni_confirmed=True, persona=Persona.ALUMNI))
        alumni_pts = [c.points for c in s.contributions if c.signal == "alumni"]
        assert alumni_pts == [_WEIGHTS["alumni_confirmed"]]

    def test_first_degree_beats_second(self):
        first = rank_contact(_cand(connection_degree="1st"))
        second = rank_contact(_cand(connection_degree="2nd"))
        assert first.total == _WEIGHTS["degree_1st"]
        assert second.total == _WEIGHTS["degree_2nd"]
        assert first.total > second.total

    def test_third_degree_and_unknown_score_no_degree(self):
        assert rank_contact(_cand(connection_degree="3rd")).total == 0
        assert rank_contact(_cand(connection_degree="weird")).total == 0

    def test_recruiter_signal(self):
        s = rank_contact(_cand(persona=Persona.RECRUITER))
        assert any(c.signal == "recruiter" for c in s.contributions)
        assert s.total == _WEIGHTS["recruiter"]

    def test_hiring_post_from_snippet(self):
        s = rank_contact(_cand(snippet="We're hiring composites engineers — join our team!"))
        assert any(c.signal == "hiring_post" for c in s.contributions)

    def test_recent_joiner_from_snippet(self):
        s = rank_contact(_cand(snippet="Recently joined AST after 6 years at Boeing."))
        assert any(c.signal == "recent_joiner" for c in s.contributions)

    def test_target_focus_match_only_when_supplied(self):
        c = _cand(focus_area=FocusArea.COMPOSITE_DESIGN)
        without = rank_contact(c)
        with_target = rank_contact(c, target_focus=FocusArea.COMPOSITE_DESIGN)
        assert not any(x.signal == "target_focus" for x in without.contributions)
        assert any(x.signal == "target_focus" for x in with_target.contributions)
        assert with_target.total == without.total + _WEIGHTS["target_focus_match"]

    def test_target_focus_mismatch_no_points(self):
        c = _cand(focus_area=FocusArea.MANUFACTURING)
        s = rank_contact(c, target_focus=FocusArea.COMPOSITE_DESIGN)
        assert not any(x.signal == "target_focus" for x in s.contributions)

    def test_engineer_and_leader_baseline(self):
        for p in (Persona.PEER_ENGINEER, Persona.SENIOR_MANAGER):
            s = rank_contact(_cand(persona=p))
            assert any(c.signal == "seniority" for c in s.contributions)

    def test_email_reachability_outranks_linkedin_only(self):
        with_email = rank_contact(_cand(email="x@acme.com", linkedin_url="https://li/x"))
        li_only = rank_contact(_cand(linkedin_url="https://li/x"))
        e = next(c for c in with_email.contributions if c.signal == "reachable")
        li = next(c for c in li_only.contributions if c.signal == "reachable")
        assert e.points == _WEIGHTS["email_on_file"]
        assert li.points == _WEIGHTS["linkedin_reachable"]

    def test_email_and_linkedin_not_double_counted(self):
        s = rank_contact(_cand(email="x@acme.com", linkedin_url="https://li/x"))
        reach = [c for c in s.contributions if c.signal == "reachable"]
        assert len(reach) == 1  # elif, not two


class TestExplainabilityAndOrdering:
    def test_total_equals_sum_of_contributions(self):
        s = rank_contact(
            _cand(
                alumni_confirmed=True,
                connection_degree="1st",
                persona=Persona.RECRUITER,
                email="x@acme.com",
            )
        )
        assert s.total == sum(c.points for c in s.contributions)

    def test_summary_lists_every_reason(self):
        s = rank_contact(_cand(alumni_confirmed=True, connection_degree="1st"))
        assert "confirmed alumnus" in s.summary()
        assert "1st-degree connection" in s.summary()

    def test_strong_contact_outranks_weak(self):
        strong = rank_contact(
            _cand(alumni_confirmed=True, connection_degree="1st", email="a@x.com")
        )
        weak = rank_contact(_cand(persona=Persona.PEER_ENGINEER, linkedin_url="https://li/x"))
        assert strong.total > weak.total

    def test_empty_score_is_dataclass_default(self):
        assert RankScore().total == 0
        assert RankScore().contributions == []


def test_norm_degree():
    assert _norm_degree("1st") == 1
    assert _norm_degree("First") == 1
    assert _norm_degree("2") == 2
    assert _norm_degree("3rd") is None
    assert _norm_degree(None) is None
    assert _norm_degree("") is None
