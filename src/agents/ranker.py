"""
src/agents/ranker.py
Referral-likelihood ranking (issue #11, ROADMAP A4 — "5 to the right people > 50
generic"). Scores a captured contact by how likely they are to actually help,
from deterministic signals already on the ContactCandidate. No LLM — the score
is reproducible and every point is explainable (per-signal contributions), so
the selection gate can order by it and say *why*.

Signals (issue #11): alumni, 1st/2nd-degree connection, recruiter-for-req,
posts-about-hiring, recent joiner, team-matches-target-role, plus reachability.
Weights are a v1 heuristic in `_WEIGHTS` — tune from the #12 ranking scorecard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.schemas import ContactCandidate, Persona

__all__ = ["SignalContribution", "RankScore", "rank_contact"]

# Point weights, roughly in the issue's stated priority order (alumni highest).
# A flat dict so the whole model reads in one place and #12 can tune it.
_WEIGHTS: dict[str, int] = {
    "alumni_confirmed": 40,  # ground-truth shared school — strongest willingness
    "alumni_classified": 25,  # persona==ALUMNI (no hard confirmation)
    "degree_1st": 30,  # already connected → very likely to reply
    "degree_2nd": 15,
    "recruiter": 20,  # literally paid to respond about reqs
    "hiring_post": 12,  # actively talking about open roles
    "recent_joiner": 10,  # new hires remember the job hunt, often refer
    "target_focus_match": 10,  # on the team for the role being targeted
    "engineer_or_leader": 5,  # peer/senior who can give or route a referral
    "email_on_file": 5,  # reachable off-platform
    "linkedin_reachable": 2,  # at least a LinkedIn handle
}

_HIRING_RE = re.compile(
    r"(?i)\b(we'?re hiring|now hiring|open roles?|open positions?|join (?:our|the) team"
    r"|hiring|we are hiring|join us)\b"
)
_RECENT_JOINER_RE = re.compile(
    r"(?i)\b(recently joined|just joined|new to (?:the )?(?:team|company|role)"
    r"|started (?:at|as)\b.*\b(?:this|last) (?:month|week))\b"
)


@dataclass
class SignalContribution:
    signal: str
    points: int
    reason: str


@dataclass
class RankScore:
    total: int = 0
    contributions: list[SignalContribution] = field(default_factory=list)

    def summary(self) -> str:
        """Compact reason string for storage / the selection gate, e.g.
        'confirmed alumnus, 1st-degree connection, recruiter (hiring channel)'."""
        return ", ".join(c.reason for c in self.contributions) or "no referral signals"


def _norm_degree(value: str | None) -> int | None:
    """Map a connection-degree string to 1, 2, or None (3rd/unknown/absent)."""
    if not value:
        return None
    v = value.strip().lower()
    if v in ("1", "1st", "first"):
        return 1
    if v in ("2", "2nd", "second"):
        return 2
    return None


def rank_contact(
    candidate: ContactCandidate, *, target_focus: str | None = None
) -> RankScore:
    """Score *candidate* by referral likelihood. Pure — no I/O, no network.

    ``target_focus`` is the run's target role focus — a focus-area label from
    the active profile's taxonomy (a FocusArea value for the default profile).
    When supplied, a contact whose ``focus_area`` matches it scores the
    team-match signal. Application mode resolves it from a posting's
    function/target_keywords (#61); pass ``None`` to skip the signal
    (Campaign mode's behavior, unchanged).
    """
    score = RankScore()

    def add(signal: str, key: str, reason: str) -> None:
        pts = _WEIGHTS[key]
        score.contributions.append(SignalContribution(signal, pts, reason))
        score.total += pts

    # Alumni — the strongest single referral signal.
    if candidate.alumni_confirmed:
        add("alumni", "alumni_confirmed", "confirmed alumnus")
    elif candidate.persona is Persona.ALUMNI:
        add("alumni", "alumni_classified", "alumni (classified)")

    # Connection degree.
    degree = _norm_degree(candidate.connection_degree)
    if degree == 1:
        add("degree", "degree_1st", "1st-degree connection")
    elif degree == 2:
        add("degree", "degree_2nd", "2nd-degree connection")

    # Recruiter for the req.
    if candidate.persona is Persona.RECRUITER:
        add("recruiter", "recruiter", "recruiter (hiring channel)")

    # Snippet-derived activity.
    snippet = candidate.snippet or ""
    if _HIRING_RE.search(snippet):
        add("hiring_post", "hiring_post", "mentions hiring/open roles")
    if _RECENT_JOINER_RE.search(snippet):
        add("recent_joiner", "recent_joiner", "recent joiner")

    # Team matches the role being targeted (only when a target is supplied).
    if target_focus is not None and candidate.focus_area == target_focus:
        add("target_focus", "target_focus_match", "focus matches target role")

    # Engineer/leader baseline — can give or route a referral.
    if candidate.persona in (Persona.PEER_ENGINEER, Persona.SENIOR_MANAGER):
        add("seniority", "engineer_or_leader", "engineer/leader who can refer")

    # Reachability — "likely to help" needs a way to reach them.
    if candidate.email:
        add("reachable", "email_on_file", "email on file")
    elif candidate.linkedin_url:
        add("reachable", "linkedin_reachable", "LinkedIn reachable")

    return score
