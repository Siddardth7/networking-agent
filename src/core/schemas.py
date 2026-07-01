"""
src/core/schemas.py
Cross-module Pydantic v2 data shapes for the networking agent.
Traceability: DESIGN.md §8.3, §2
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from src.core.slug import slugify

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
    "Application",
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


class NextMove(StrEnum):
    """The reply-aware next move (issue #19, A8 — 'they replied, now what?').

    Picked from the reply text + recorded outcome by `drafter.classify_next_move`,
    then drafted in voice. Ordered by goal-advancing precedence: a concrete intro
    offer beats answering a sponsorship mention beats scheduling beats asking for
    a referral; an unread warm reply defaults to proposing a short call."""

    THANK_INTRO = "THANK_INTRO"  # they offered an intro / POC → thank + take it
    SPONSORSHIP_QUESTION = "SPONSORSHIP_QUESTION"  # they raised visa/sponsorship → ask it
    SCHEDULE_CALL = "SCHEDULE_CALL"  # warm/open reply → propose a brief chat (default)
    REFERRAL_ASK = "REFERRAL_ASK"  # they mention hiring/roles → ask for the referral


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
    # A focus-area label from the active profile's taxonomy (#61). The default
    # profile's labels are the FocusArea enum values; custom profiles define
    # their own, so this is a string — validated at classification time.
    focus_area: str | None = None
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


class Application(BaseModel):
    """A single job posting from an application-feed (Phase B, #58).

    The Application-mode unit of work: a per-posting referral target. `job_id`
    is the linkage key that ties discovered contacts, drafts, and outcomes back
    to *this* posting so the consumer can ask "referral for this req yet?".
    `contacts` reuses :class:`ContactCandidate` verbatim for any pre-captured
    leads (usually empty — the agent finds them). `company_slug` is derived from
    `company` via the canonical :func:`src.core.slug.slugify` when the feed omits
    it, so Campaign and Application modes cross-link to the same `companies` row.

    Traceability: docs/APPLICATION_FEED_INPUT_DESIGN_2026-06-30.md §4.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # Required per posting (feed §4 field rules).
    job_id: str
    company: str
    role_title: str
    # Optional — derived / provenance / targeting. `function` + `target_keywords`
    # are free-form (they resolve against the active profile's taxonomy in P4),
    # not a fixed enum, to keep the feed field-agnostic.
    company_slug: str = ""  # derived from company when absent (see validator)
    function: str | None = None
    job_url: str | None = None
    location: str | None = None
    target_keywords: list[str] = []
    score: int | None = None
    deadline: str | None = None
    source: str | None = None
    contacts: list[ContactCandidate] = []

    @model_validator(mode="after")
    def _derive_company_slug(self) -> Application:
        """Fill `company_slug` from `company` when the feed omits it (or gives
        blank/whitespace, which str_strip_whitespace has already trimmed to "")."""
        if not self.company_slug:
            self.company_slug = slugify(self.company)
        return self


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
