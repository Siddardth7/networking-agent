"""
src/agents/achievement_matcher.py
Resume library loader and achievement matcher.
Traceability: DESIGN.md §6 (Drafting Subsystem — achievement matching)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.core.schemas import FocusArea

__all__ = ["Bullet", "Project", "ResumeLibrary", "load_resume_library", "match_achievements"]

_DEFAULT_LIBRARY_PATH = Path.home() / ".networking-agent" / "resume_library.yaml"


class Bullet(BaseModel):
    id: str
    text: str
    keywords: list[str]


class Project(BaseModel):
    id: str
    title: str
    focus_areas: list[FocusArea]
    bullets: list[Bullet]


class ResumeLibrary(BaseModel):
    projects: list[Project]


def load_resume_library(path: Optional[str] = None) -> ResumeLibrary:
    """Load resume library YAML from *path* (defaults to ~/.networking-agent/resume_library.yaml).

    Returns an empty library if the file does not exist.
    """
    p = Path(path) if path is not None else _DEFAULT_LIBRARY_PATH
    if not p.exists():
        return ResumeLibrary(projects=[])
    with p.open() as f:
        data = yaml.safe_load(f)
    return ResumeLibrary.model_validate(data or {"projects": []})


def match_achievements(
    contact_focus_area: FocusArea,
    contact_title: str,
    library: ResumeLibrary,
    top_n: int = 3,
) -> list[Bullet]:
    """Return the top *top_n* achievement bullets for a contact.

    Algorithm (per DESIGN §6):
    1. Filter to projects where ``contact_focus_area in project.focus_areas``.
    2. Score each bullet by keyword overlap with ``contact_title`` (case-insensitive
       substring match — no stemming, deferred to v0.1.1).
    3. Return top_n bullets sorted by score descending.
    """
    title_lower = contact_title.lower()

    scored: list[tuple[int, Bullet]] = []
    for project in library.projects:
        if contact_focus_area not in project.focus_areas:
            continue
        for bullet in project.bullets:
            score = sum(1 for kw in bullet.keywords if kw.lower() in title_lower)
            scored.append((score, bullet))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored[:top_n]]
