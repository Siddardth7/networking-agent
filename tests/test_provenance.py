"""
tests/test_provenance.py
Layer 2: ProvenancedBullet carries project_title + project_type; the
drafter prompt surfaces provenance and enforces FACT DISCIPLINE.
"""

from __future__ import annotations

import pytest

from src.agents.achievement_matcher import (
    Bullet,
    Project,
    ProvenancedBullet,
    ResumeLibrary,
    match_achievements,
)
from src.agents.drafter import _build_prompt, _render_approved_facts
from src.core.schemas import Channel, FocusArea, Persona, ProjectType


# ---------------------------------------------------------------------------
# Matcher returns ProvenancedBullet with project title + type
# ---------------------------------------------------------------------------

class TestMatcherProvenance:
    def _lib_mixed_types(self) -> ResumeLibrary:
        return ResumeLibrary(projects=[
            Project(
                id="sampe", title="SAMPE Composite Fuselage", type=ProjectType.COMPETITION,
                focus_areas=[FocusArea.COMPOSITE_DESIGN],
                bullets=[Bullet(id="s1", text="Designed CFRP shell.", keywords=["composite", "cfrp"])],
            ),
            Project(
                id="tata", title="Tata Internship", type=ProjectType.INTERNSHIP,
                focus_areas=[FocusArea.COMPOSITE_DESIGN],
                bullets=[Bullet(id="t1", text="Drove process improvements on the layup line.",
                                keywords=["composite", "process", "manufacturing"])],
            ),
            Project(
                id="course", title="Aero Structures Course",  # type defaults to COURSEWORK
                focus_areas=[FocusArea.COMPOSITE_DESIGN],
                bullets=[Bullet(id="c1", text="Did FEA on a wing.", keywords=["composite", "fea"])],
            ),
        ])

    def test_each_result_is_provenanced_bullet(self):
        results = match_achievements(
            FocusArea.COMPOSITE_DESIGN, "Composites Engineer",
            self._lib_mixed_types(), top_n=3,
        )
        assert len(results) == 3
        for b in results:
            assert isinstance(b, ProvenancedBullet)
            assert b.project_title
            assert isinstance(b.project_type, ProjectType)

    def test_project_type_preserved_through_matching(self):
        results = match_achievements(
            FocusArea.COMPOSITE_DESIGN, "Composites Engineer",
            self._lib_mixed_types(), top_n=3,
        )
        type_by_title = {b.project_title: b.project_type for b in results}
        assert type_by_title["SAMPE Composite Fuselage"] == ProjectType.COMPETITION
        assert type_by_title["Tata Internship"] == ProjectType.INTERNSHIP
        assert type_by_title["Aero Structures Course"] == ProjectType.COURSEWORK

    def test_unspecified_type_defaults_to_coursework(self):
        # Pre-Layer-2 libraries that lack a `type:` field default to the
        # safer COURSEWORK label — never silently promoted to INTERNSHIP.
        proj = Project(
            id="p", title="No-Type Project",
            focus_areas=[FocusArea.COMPOSITE_DESIGN],
            bullets=[Bullet(id="b", text="x", keywords=["composite"])],
        )
        assert proj.type == ProjectType.COURSEWORK


# ---------------------------------------------------------------------------
# _render_approved_facts: provenance line shape
# ---------------------------------------------------------------------------

class TestRenderApprovedFacts:
    def test_provenance_tag_in_each_line(self):
        bullets = [
            ProvenancedBullet(
                text="Built a thing.",
                project_title="SAMPE Composite Fuselage",
                project_type=ProjectType.COMPETITION,
            ),
            ProvenancedBullet(
                text="Drove process improvements.",
                project_title="Tata Internship",
                project_type=ProjectType.INTERNSHIP,
            ),
        ]
        rendered = _render_approved_facts(bullets)
        assert "[COMPETITION: SAMPE Composite Fuselage] Built a thing." in rendered
        assert "[INTERNSHIP: Tata Internship] Drove process improvements." in rendered

    def test_empty_bullets_yields_grounded_disclaimer(self):
        rendered = _render_approved_facts([])
        # The disclaimer steers the model to a brief, non-fabricated message.
        assert "do NOT invent" in rendered


# ---------------------------------------------------------------------------
# _build_prompt: APPROVED FACTS section + FACT DISCIPLINE block
# ---------------------------------------------------------------------------

class TestPromptContainsDiscipline:
    def _contact(self) -> dict:
        return {
            "full_name": "Jane Doe",
            "title": "Stress Engineer",
            "linkedin_url": "https://linkedin.com/in/jd",
            "email": "jd@example.com",
            "hook": "shared UIUC alumni",
            "persona": "PEER_ENGINEER",
        }

    def test_prompt_includes_approved_facts_header(self):
        bullets = [ProvenancedBullet(
            text="Did x.", project_title="P", project_type=ProjectType.INTERNSHIP,
        )]
        prompt = _build_prompt(
            self._contact(), Channel.COLD_EMAIL, Persona.PEER_ENGINEER,
            bullets, "PERSONA TEMPLATE", "VOICE DOC",
        )
        assert "## APPROVED FACTS" in prompt
        assert "the only achievements you may state" in prompt

    def test_prompt_includes_fact_discipline_rules(self):
        prompt = _build_prompt(
            self._contact(), Channel.COLD_EMAIL, Persona.PEER_ENGINEER,
            [], "PERSONA TEMPLATE", "",
        )
        # Each of the four key disciplines must be present so the model
        # cannot plausibly miss them.
        assert "FACT DISCIPLINE" in prompt
        assert "No invented numbers" in prompt
        assert "No re-attribution" in prompt
        assert "No placeholders" in prompt
        assert "Specificity floor" in prompt

    def test_prompt_does_not_use_legacy_reference_label(self):
        # Pre-Layer-2 the section was "Relevant Achievements to Reference"
        # which the model interpreted as "facts that you may rewrite".
        prompt = _build_prompt(
            self._contact(), Channel.COLD_EMAIL, Persona.PEER_ENGINEER,
            [], "PERSONA TEMPLATE", "",
        )
        assert "Relevant Achievements to Reference" not in prompt

    def test_anti_phrases_accepts_list(self):
        prompt = _build_prompt(
            self._contact(), Channel.COLD_EMAIL, Persona.PEER_ENGINEER,
            [], "PERSONA TEMPLATE", "",
            anti_phrases=["I noticed", "I admire"],
        )
        assert '"I noticed"' in prompt
        assert '"I admire"' in prompt

    def test_no_anti_phrases_omits_section(self):
        prompt = _build_prompt(
            self._contact(), Channel.COLD_EMAIL, Persona.PEER_ENGINEER,
            [], "PERSONA TEMPLATE", "",
        )
        assert "DO NOT USE THESE PHRASES" not in prompt


# ---------------------------------------------------------------------------
# ProjectType: enum + serialization sanity
# ---------------------------------------------------------------------------

class TestProjectTypeEnum:
    def test_all_expected_members(self):
        members = {m.value for m in ProjectType}
        assert members == {
            "COMPETITION", "COURSEWORK", "RESEARCH", "INTERNSHIP", "INDUSTRY",
        }

    def test_is_string_enum(self):
        # str subclass — required for sqlite/yaml interop.
        assert ProjectType.COMPETITION == "COMPETITION"
        assert isinstance(ProjectType.COMPETITION, str)
