"""
src/core/schemas.py
Cross-module Pydantic v2 data shapes for the networking agent.
Traceability: DESIGN.md §8.3, §2
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

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


class Persona(str, Enum):
    RECRUITER = "RECRUITER"
    SENIOR_MANAGER = "SENIOR_MANAGER"
    PEER_ENGINEER = "PEER_ENGINEER"
    ALUMNI = "ALUMNI"


class FocusArea(str, Enum):
    COMPOSITE_DESIGN = "COMPOSITE_DESIGN"
    STRUCTURAL_ANALYSIS = "STRUCTURAL_ANALYSIS"
    MANUFACTURING = "MANUFACTURING"
    MATERIALS = "MATERIALS"
    ADDITIVE = "ADDITIVE"
    PEER = "PEER"
    ALUMNI_ACADEMIC = "ALUMNI_ACADEMIC"


class Channel(str, Enum):
    LINKEDIN_CONNECTION = "LINKEDIN_CONNECTION"
    LINKEDIN_POST_CONNECTION = "LINKEDIN_POST_CONNECTION"
    COLD_EMAIL = "COLD_EMAIL"


class PipelineState(str, Enum):
    NEW = "NEW"
    FOUND = "FOUND"
    SELECTED = "SELECTED"
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    SENT = "SENT"
    ARCHIVED = "ARCHIVED"


class ContactState(str, Enum):
    NEW = "NEW"
    SELECTED = "SELECTED"
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    SENT = "SENT"


class ProjectType(str, Enum):
    """Origin of a resume project. Provenance for fact-attribution rules.

    The drafter must NEVER re-attribute work from a COMPETITION or COURSEWORK
    project to a contact's employer (root-cause audit §2.2 — coursework being
    rewritten as "work at Tata Advanced Systems"). INTERNSHIP and INDUSTRY
    items may be referenced as professional experience.
    """

    COMPETITION = "COMPETITION"   # student design competition (SAMPE, etc.)
    COURSEWORK = "COURSEWORK"     # class projects, MS coursework
    RESEARCH = "RESEARCH"         # academic research, thesis work
    INTERNSHIP = "INTERNSHIP"     # paid/unpaid internship at a company
    INDUSTRY = "INDUSTRY"         # full-time professional work


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ContactCandidate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    company_slug: str
    persona: Optional[Persona] = None
    focus_area: Optional[FocusArea] = None
    email: Optional[str] = None
    # Raw Serper search snippet (LinkedIn About / recent activity excerpt).
    # Used by the finder classifier to ground persona + extract a specific
    # hook_signal. May be None when not provided by the search API.
    snippet: Optional[str] = None


class EmailResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: Optional[str]
    verified: bool
    confidence: int  # 0-100
    source: str      # e.g. "hunter", "HUNTER_EXHAUSTED"


class RetryDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    should_retry: bool
    wait_seconds: float
    reason: str


class DraftDispatchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    contact_id: int
    channel: Channel
    prior_draft_id: Optional[int] = None
    feedback: Optional[str] = None
    voice_doc_path: Optional[str] = None
    max_attempts: int = 2


class DraftDispatchResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: str  # "OK" | "GUARDRAIL_FLAGGED" | "ERROR"
    new_draft_id: Optional[int] = None
    new_version: Optional[int] = None
    body: Optional[str] = None
    subject: Optional[str] = None
    quality_flag: bool = False
    error_message: Optional[str] = None
