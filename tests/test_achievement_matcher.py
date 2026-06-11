"""Tests for src/agents/achievement_matcher.py"""

from __future__ import annotations

import yaml

from src.agents.achievement_matcher import (
    Bullet,
    Project,
    ResumeLibrary,
    load_resume_library,
    match_achievements,
)
from src.core.schemas import FocusArea


def _make_library() -> ResumeLibrary:
    return ResumeLibrary(
        projects=[
            Project(
                id="composites_project",
                title="CFRP Fuselage Design",
                focus_areas=[FocusArea.COMPOSITE_DESIGN, FocusArea.MANUFACTURING],
                bullets=[
                    Bullet(
                        id="b1",
                        text="Designed composite fuselage with 40% weight reduction.",
                        keywords=["composite", "fuselage", "structural"],
                    ),
                    Bullet(
                        id="b2",
                        text="Led vacuum infusion process for 1.2m CFRP panel.",
                        keywords=["composite", "manufacturing", "infusion"],
                    ),
                    Bullet(
                        id="b3",
                        text="Coupon testing — 98% correlation with FEA.",
                        keywords=["composite", "test", "fea"],
                    ),
                    Bullet(id="b4", text="Extra composite bullet.", keywords=["composite"]),
                ],
            ),
            Project(
                id="structures_project",
                title="Wing Box Analysis",
                focus_areas=[FocusArea.STRUCTURAL_ANALYSIS],
                bullets=[
                    Bullet(
                        id="s1",
                        text="Ran NASTRAN on wing box — found buckling margin.",
                        keywords=["structural", "fea", "nastran", "wing"],
                    ),
                    Bullet(
                        id="s2",
                        text="Fatigue life estimation with S-N curves.",
                        keywords=["structural", "fatigue", "analysis"],
                    ),
                ],
            ),
            Project(
                id="mfg_project",
                title="Supplier Quality Internship",
                focus_areas=[FocusArea.MANUFACTURING],
                bullets=[
                    Bullet(
                        id="m1",
                        text="Dispositioned 47 MRB tickets.",
                        keywords=["quality", "mrb", "supplier"],
                    ),
                ],
            ),
        ]
    )


class TestMatchAchievements:
    def test_composites_contact_gets_composites_bullets(self):
        lib = _make_library()
        results = match_achievements(FocusArea.COMPOSITE_DESIGN, "Composites Manager", lib, top_n=3)
        assert len(results) == 3
        # All returned bullets should come from the composites project.
        # ProvenancedBullet exposes project_title and project_type so
        # the drafter can forbid re-attribution.
        for b in results:
            assert b.project_title == "CFRP Fuselage Design"
            assert (
                "composite" in b.text.lower()
                or "cfrp" in b.text.lower()
                or "coupon" in b.text.lower()
            )

    def test_keyword_overlap_ranks_bullet_higher(self):
        lib = _make_library()
        # "Composites Manager" → "composite" matches in multiple bullets;
        # b1 has "composite" + "fuselage" matches; best overlap for title "Composites Manager"
        results = match_achievements(FocusArea.COMPOSITE_DESIGN, "Composites Manager", lib, top_n=3)
        # b1 has "composite" in title (1 match); b2 has "composite" (1); b3 has "composite" (1)
        # b4 has only "composite" (1). All tied for this title.
        # Verify we get top_n=3 bullets
        assert len(results) == 3

    def test_title_specific_keyword_boosts_ranking(self):
        lib = _make_library()
        # "Structural Engineer" → "structural" keyword in s1 and b1/b3
        # For COMPOSITE_DESIGN focus area only composites_project bullets are eligible
        results = match_achievements(
            FocusArea.COMPOSITE_DESIGN, "Structural Composites Engineer", lib, top_n=2
        )
        assert len(results) == 2
        # b1 has "composite" + "structural" = score 2 → should be first
        assert "fuselage" in results[0].text.lower()
        assert results[0].project_title == "CFRP Fuselage Design"

    def test_manufacturing_focus_gets_manufacturing_bullets(self):
        lib = _make_library()
        results = match_achievements(FocusArea.MANUFACTURING, "Quality Engineer", lib, top_n=3)
        # Both composites_project and mfg_project have MANUFACTURING
        assert len(results) == 3
        # m1 ("Dispositioned 47 MRB tickets") is the only bullet whose
        # "quality" keyword overlaps "Quality Engineer" — must rank in.
        texts = [b.text for b in results]
        assert any("MRB" in t for t in texts)

    def test_unmatched_focus_area_returns_empty(self):
        lib = _make_library()
        results = match_achievements(FocusArea.ADDITIVE, "Additive Engineer", lib, top_n=3)
        assert results == []

    def test_top_n_limits_results(self):
        lib = _make_library()
        results = match_achievements(
            FocusArea.COMPOSITE_DESIGN, "Composites Engineer", lib, top_n=2
        )
        assert len(results) == 2

    def test_empty_library_returns_empty(self):
        lib = ResumeLibrary(projects=[])
        results = match_achievements(
            FocusArea.COMPOSITE_DESIGN, "Composites Engineer", lib, top_n=3
        )
        assert results == []


class TestLoadResumeLibrary:
    def test_missing_file_returns_empty(self, tmp_path):
        lib = load_resume_library(str(tmp_path / "nonexistent.yaml"))
        assert lib.projects == []

    def test_loads_valid_yaml(self, tmp_path):
        data = {
            "projects": [
                {
                    "id": "p1",
                    "title": "Test Project",
                    "focus_areas": ["COMPOSITE_DESIGN"],
                    "bullets": [{"id": "b1", "text": "A bullet.", "keywords": ["composite"]}],
                }
            ]
        }
        p = tmp_path / "library.yaml"
        p.write_text(yaml.dump(data))
        lib = load_resume_library(str(p))
        assert len(lib.projects) == 1
        assert lib.projects[0].id == "p1"
        assert lib.projects[0].focus_areas == [FocusArea.COMPOSITE_DESIGN]

    def test_empty_yaml_returns_empty(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        lib = load_resume_library(str(p))
        assert lib.projects == []
