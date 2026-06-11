"""
src/agents/achievement_matcher.py
Resume library loader and achievement matcher with provenance.
Traceability: DESIGN.md §6 (Drafting Subsystem — achievement matching);
              DRAFTER_ROOT_CAUSE_AUDIT.md Layer 2 (no provenance → fabrication).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from src.core.schemas import FocusArea, ProjectType

__all__ = [
    "Bullet",
    "Project",
    "ResumeLibrary",
    "ProvenancedBullet",
    "load_resume_library",
    "match_achievements",
]

# Default resolved lazily via config.resume_library_path() so the
# NETWORKING_AGENT_CONFIG relocation moves the library too (AUDIT-A26).


class Bullet(BaseModel):
    id: str
    text: str
    keywords: list[str]


class Project(BaseModel):
    id: str
    title: str
    # ProjectType is REQUIRED for new entries so the drafter can never
    # re-attribute coursework as employer experience. Pre-Layer-2 libraries
    # default to COURSEWORK (the safer assumption — never inflates to
    # INTERNSHIP/INDUSTRY).
    type: ProjectType = ProjectType.COURSEWORK
    focus_areas: list[FocusArea]
    bullets: list[Bullet]


class ResumeLibrary(BaseModel):
    projects: list[Project]


class ProvenancedBullet(BaseModel):
    """A bullet plus the project it came from.

    The drafter renders ``project_title`` and ``project_type`` alongside
    ``text`` so the model cannot silently re-attribute a SAMPE competition
    bullet as "work at <employer>". See the FACT DISCIPLINE block in
    ``drafter._build_prompt``.
    """

    text: str
    project_title: str
    project_type: ProjectType


def load_resume_library(path: str | None = None) -> ResumeLibrary:
    """Load the resume library YAML.

    Inputs: optional explicit *path*; defaults to resume_library.yaml in
    the config directory (honors NETWORKING_AGENT_CONFIG, AUDIT-A26).
    Output: a validated ResumeLibrary; empty when the file is absent.
    Reads the filesystem; no other side effects. Raises pydantic
    ValidationError on malformed entries.
    """
    from src.core.config import resume_library_path

    p = Path(path) if path is not None else resume_library_path()
    if not p.exists():
        return ResumeLibrary(projects=[])
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ResumeLibrary.model_validate(data or {"projects": []})


def match_achievements(
    contact_focus_area: FocusArea,
    contact_title: str,
    library: ResumeLibrary,
    top_n: int = 3,
) -> list[ProvenancedBullet]:
    """Return the top *top_n* achievement bullets *with provenance*.

    Algorithm (per DESIGN §6):
    1. Filter to projects where ``contact_focus_area in project.focus_areas``.
    2. Score each bullet by keyword overlap with ``contact_title``
       (case-insensitive substring match — no stemming, deferred to v0.1.1).
    3. Return top_n :class:`ProvenancedBullet` objects sorted by score
       descending. Each carries its parent project title and type so the
       drafter can show provenance and forbid re-attribution.
    """
    title_lower = contact_title.lower()

    scored: list[tuple[int, ProvenancedBullet]] = []
    for project in library.projects:
        if contact_focus_area not in project.focus_areas:
            continue
        for bullet in project.bullets:
            score = sum(1 for kw in bullet.keywords if kw.lower() in title_lower)
            scored.append(
                (
                    score,
                    ProvenancedBullet(
                        text=bullet.text,
                        project_title=project.title,
                        project_type=project.type,
                    ),
                )
            )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored[:top_n]]
