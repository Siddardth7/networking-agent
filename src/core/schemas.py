"""
src/core/schemas.py
Cross-module Pydantic v2 data shapes for the networking agent.
Traceability: DESIGN.md §8.3, §2
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    # Enums
    "Persona",
    "FocusArea",
    "Channel",
    "PipelineState",
    "ContactState",
    "ProjectType",
    # Models
    "ContactCandidate",
    "EmailResult",
    "RetryDecision",
    "DraftDispatchRequest",
    "DraftDispatchResponse",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Persona(StrEnum):
    RECRUITER = "RECRUITER"
    SENIOR_MANAGER = "SENIOR_MANAGER"
    PEER_ENGINEER = "PEER_ENGINEER"
    ALUMNI = "ALUMNI"


class FocusArea(StrEnum):
    COMPOSITE_DESIGN = "COMPOSITE_DESIGN"
    STRUCTURAL_ANALYSIS = "STRUCTURAL_ANALYSIS"
    MANUFACTURING = "MANUFACTURING"
    MATERIALS = "MATERIALS"
    ADDITIVE = "ADDITIVE"
    PEER = "PEER"
    ALUMNI_ACADEMIC = "ALUMNI_ACADEMIC"


class Channel(StrEnum):
    LINKEDIN_CONNECTION = "LINKEDIN_CONNECTION"
    LINKEDIN_POST_CONNECTION = "LINKEDIN_POST_CONNECTION"
    COLD_EMAIL = "COLD_EMAIL"


class PipelineState(StrEnum):
    NEW = "NEW"
    FOUND = "FOUND"
    SELECTED = "SELECTED"
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    SENT = "SENT"
    ARCHIVED = "ARCHIVED"


class ContactState(StrEnum):
    NEW = "NEW"
    SELECTED = "SELECTED"
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    SENT = "SENT"


class Outcome(StrEnum):
    """Per-contact outreach feedback (issue #15, A6). The funnel from no response
    through the goal-critical sponsorship answer — the signal that later tunes
    the referral-ranking weights (#12). Orthogonal to pipeline/contact state."""

    NONE = "NONE"  # default — nothing recorded yet
    REPLIED = "REPLIED"  # responded, no stronger signal yet
    POC = "POC"  # yielded a point of contact / referral / intro
    SPONSORSHIP_YES = "SPONSORSHIP_YES"  # confirmed sponsorship available (the goal)
    SPONSORSHIP_NO = "SPONSORSHIP_NO"  # answered: no sponsorship
    DECLINED = "DECLINED"  # not interested / no


class ProjectType(StrEnum):
    """Origin of a resume project. Provenance for fact-attribution rules.

    The drafter must NEVER re-attribute work from a COMPETITION or COURSEWORK
    project to a contact's employer (root-cause audit §2.2 — coursework being
    rewritten as "work at Tata Advanced Systems"). INTERNSHIP and INDUSTRY
    items may be referenced as professional experience.
    """

    COMPETITION = "COMPETITION"  # student design competition (SAMPE, etc.)
    COURSEWORK = "COURSEWORK"  # class projects, MS coursework
    RESEARCH = "RESEARCH"  # academic research, thesis work
    INTERNSHIP = "INTERNSHIP"  # paid/unpaid internship at a company
    INDUSTRY = "INDUSTRY"  # full-time professional work


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ContactCandidate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str
    title: str | None = None
    linkedin_url: str | None = None
    company_slug: str
    persona: Persona | None = None
    focus_area: FocusArea | None = None
    email: str | None = None
    # Raw Serper search snippet (LinkedIn About / recent activity excerpt).
    # Used by the finder classifier to ground persona + extract a specific
    # hook_signal. May be None when not provided by the search API.
    snippet: str | None = None
    # ---- Canonical-input fields (flexible-input design, 2026-06-21) ----
    # ContactCandidate doubles as the canonical record every input source
    # (Serper, Apollo, Apify, Cowork+Chrome, manual files) normalizes to.
    # These are honored when a source supplies them and generated otherwise,
    # so a labeled file skips LLM work while a raw name+URL list is enriched.
    # `hook`: user/source-supplied hook; when None the Finder/importer
    #   generates one via _generate_hook. `location`: campaign/site context.
    hook: str | None = None
    location: str | None = None
    # Producer-supplied campaign/provenance signals (Cowork+Chrome producer,
    # docs/CHROME_PRODUCER_CONTRACT.md). All optional and recorded in
    # shared_signals for the reviewer. `alumni_confirmed` (sourced via the
    # LinkedIn Alumni tool) additionally FORCES the ALUMNI persona — a
    # ground-truth signal stronger than the classifier's guess. `school`:
    # file-level campaign context. `connection_degree` (1st/2nd/3rd): surfaced
    # so the reviewer can prioritize the LinkedIn invite channel.
    school: str | None = None
    alumni_confirmed: bool | None = None
    connection_degree: str | None = None


class EmailResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str | None
    verified: bool
    confidence: int  # 0-100
    source: str  # e.g. "hunter", "HUNTER_EXHAUSTED"


class RetryDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    should_retry: bool
    wait_seconds: float
    reason: str


class DraftDispatchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    contact_id: int
    channel: Channel
    prior_draft_id: int | None = None
    feedback: str | None = None
    voice_doc_path: str | None = None
    max_attempts: int = 2


class DraftDispatchResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: str  # "OK" | "GUARDRAIL_FLAGGED" | "ERROR"
    new_draft_id: int | None = None
    new_version: int | None = None
    body: str | None = None
    subject: str | None = None
    quality_flag: bool = False
    error_message: str | None = None
