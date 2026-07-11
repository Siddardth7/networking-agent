"""
tests/test_profile.py
Profile-driven de-hardcode (#61, Phase B P4): the Profile schema/loader, the
free-form → taxonomy resolver, and the zero-regression contract — the built-in
default profile must reproduce every constant it replaced, byte for byte.
"""

from __future__ import annotations

import json

import pytest

import src.core.config as config_module
from src.core.errors import ProfileError
from src.core.profile import (
    FocusAreaDef,
    Profile,
    focus_area_names,
    load_profile,
    profile_path,
    resolve_target_focus,
)
from src.core.schemas import FocusArea


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point the config dir (and thus profile.yaml resolution) at tmp_path."""
    monkeypatch.delenv("NETWORKING_AGENT_CONFIG", raising=False)
    monkeypatch.setattr(config_module, "_config_path", tmp_path / "config.yaml")
    return tmp_path


# ---------------------------------------------------------------------------
# Zero-regression: the default profile IS the pre-#61 hardcoded configuration
# ---------------------------------------------------------------------------


class TestDefaultProfileZeroRegression:
    def test_role_keywords_are_the_old_default(self):
        assert Profile().role_keywords == (
            "quality engineer",
            "supplier quality",
            "MRB engineer",
            "manufacturing engineer",
            "stress engineer",
            "structures engineer",
            "composites engineer",
            "materials engineer",
            "additive manufacturing",
        )
        assert config_module.DEFAULT_ROLE_KEYWORDS == list(Profile().role_keywords)

    def test_school_signals_and_name(self):
        p = Profile()
        assert p.school_name == "UIUC"
        assert p.school_signals == ("uiuc", "university of illinois", "urbana-champaign")

    def test_shared_employers(self):
        assert Profile().shared_employers == (
            "tata", "ge", "general electric", "boeing", "lockheed", "airbus", "honeywell",
        )

    def test_identity_markers(self):
        assert Profile().identity_markers == (
            "uiuc",
            "university of illinois",
            "urbana-champaign",
            "aerospace engineering",
            "ms aerospace",
        )

    def test_focus_area_names_match_the_enum(self):
        assert focus_area_names(Profile()) == tuple(e.value for e in FocusArea)

    def test_api_description_reproduces_validated_prompt_wording(self):
        # The classify tool schema's focus description is accuracy-validated
        # (#5); the default profile must render it byte-identically.
        built = " ".join(f"{fa.name}: {fa.api_desc}." for fa in Profile().focus_areas)
        assert built == (
            "COMPOSITE_DESIGN: composites/carbon fiber. "
            "STRUCTURAL_ANALYSIS: stress/loads/FEA/airframe. "
            "MANUFACTURING: production/quality/MRB/supplier. "
            "MATERIALS: metallurgy/alloys. "
            "ADDITIVE: 3D printing. "
            "PEER: a generalist engineer with NO clear single "
            "specialty — use this (do NOT guess a specialty) when "
            "the title/snippet doesn't clearly point to one above. "
            "ALUMNI_ACADEMIC: academic/PhD/research/student."
        )

    def test_host_focus_options_reproduce_old_dict(self):
        from src.agents.finder import _focus_options

        assert _focus_options(Profile()) == {
            "COMPOSITE_DESIGN": "composites / carbon fiber",
            "STRUCTURAL_ANALYSIS": "stress / loads / FEA / airframe",
            "MANUFACTURING": "production / quality / MRB / supplier",
            "MATERIALS": "metallurgy / alloys",
            "ADDITIVE": "3D printing",
            "PEER": "generalist engineer, NO clear specialty — use this, do NOT guess one",
            "ALUMNI_ACADEMIC": "academic / PhD / research / student",
        }

    def test_fallback_identity_and_identity_short(self):
        p = Profile()
        assert p.fallback_identity == "Siddardth Pathipaka, MS Aerospace UIUC (Dec 2025)"
        assert p.identity_short == "MS AE at UIUC, composites"

    def test_default_alumni_ask_angle_names_uiuc(self):
        from src.agents.drafter import _alumni_ask_angles

        assert (
            "how their own UIUC-to-industry transition went and what they'd do differently"
            in _alumni_ask_angles(Profile().school_name)
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoadProfile:
    def test_no_file_returns_builtin_default(self, tmp_config):
        assert load_profile() == Profile()

    def test_mtime_cache_hits_and_invalidates(self, tmp_config):
        # Post-review cleanup: repeated loads of an unchanged file are served
        # from the cache; an edit (new mtime) is picked up on the next call.
        import os

        path = tmp_config / "profile.yaml"
        path.write_text("school_name: MIT\n", encoding="utf-8")
        first = load_profile()
        assert first.school_name == "MIT"
        assert load_profile() is first  # cached object, no re-parse
        path.write_text("school_name: Stanford\n", encoding="utf-8")
        os.utime(path, ns=(1, 1))  # force a distinct mtime regardless of fs resolution
        assert load_profile().school_name == "Stanford"

    def test_default_ref_aliases(self, tmp_config):
        assert profile_path() == tmp_config / "profile.yaml"
        assert profile_path("default") == tmp_config / "profile.yaml"
        assert profile_path("nurse") == tmp_config / "profiles" / "nurse.yaml"

    def test_thin_file_overrides_only_named_fields(self, tmp_config):
        (tmp_config / "profile.yaml").write_text(
            "school_name: Georgia Tech\nschool_signals: [georgia tech, gatech]\n",
            encoding="utf-8",
        )
        p = load_profile()
        assert p.school_name == "Georgia Tech"
        assert p.school_signals == ("georgia tech", "gatech")
        # Everything else keeps the default (thin-profile contract).
        assert p.role_keywords == Profile().role_keywords
        assert p.focus_areas == Profile().focus_areas
        assert p.identity_markers == Profile().identity_markers

    def test_custom_focus_areas_get_structural_areas_appended(self, tmp_config):
        (tmp_config / "profile.yaml").write_text(
            "focus_areas:\n"
            "  - name: backend\n"
            "    description: distributed systems\n"
            "    keywords: [backend, go, kafka]\n"
            "    hook: your distributed-systems work\n"
            "    hook_keywords: [backend, distributed]\n",
            encoding="utf-8",
        )
        p = load_profile()
        names = focus_area_names(p)
        assert names[0] == "BACKEND"  # upper-cased
        assert "PEER" in names and "ALUMNI_ACADEMIC" in names
        backend = p.focus_areas[0]
        assert backend.keywords == ("backend", "go", "kafka")
        assert backend.hook == "your distributed-systems work"
        assert backend.api_desc == "distributed systems"  # falls back to description

    def test_named_ref_loads_from_profiles_dir(self, tmp_config):
        (tmp_config / "profiles").mkdir()
        (tmp_config / "profiles" / "swe.yaml").write_text(
            "name: swe-profile\nschool_name: MIT\n", encoding="utf-8"
        )
        p = load_profile("swe")
        assert p.name == "swe-profile"
        assert p.school_name == "MIT"

    def test_named_ref_missing_raises(self, tmp_config):
        with pytest.raises(FileNotFoundError, match="ghost"):
            load_profile("ghost")

    def test_non_mapping_file_falls_back_to_defaults(self, tmp_config):
        (tmp_config / "profile.yaml").write_text("- just\n- a list\n", encoding="utf-8")
        assert load_profile() == Profile()

    def test_malformed_focus_entries_are_skipped(self, tmp_config):
        (tmp_config / "profile.yaml").write_text(
            "focus_areas:\n  - notadict\n  - {description: no name}\n", encoding="utf-8"
        )
        # Every entry malformed → the default taxonomy survives.
        assert load_profile().focus_areas == Profile().focus_areas

    def test_env_var_selects_named_profile(self, tmp_config, monkeypatch):
        # NETWORKING_AGENT_PROFILE makes a named profile the active one for
        # ALL no-ref callers (drafter, guardrails, config) — review finding 1.
        (tmp_config / "profiles").mkdir()
        (tmp_config / "profiles" / "swe.yaml").write_text(
            "name: swe-profile\nschool_name: MIT\n", encoding="utf-8"
        )
        monkeypatch.setenv("NETWORKING_AGENT_PROFILE", "swe")
        assert load_profile().name == "swe-profile"
        # An explicit ref still wins over the env var.
        (tmp_config / "profiles" / "other.yaml").write_text(
            "name: other-profile\n", encoding="utf-8"
        )
        assert load_profile("other").name == "other-profile"

    def test_env_var_naming_missing_profile_raises(self, tmp_config, monkeypatch):
        monkeypatch.setenv("NETWORKING_AGENT_PROFILE", "ghost")
        with pytest.raises(FileNotFoundError, match="ghost"):
            load_profile()

    def test_invalid_yaml_raises_clean_profile_error(self, tmp_config):
        # Review finding 2: a typo'd profile.yaml must surface as ProfileError,
        # not a raw yaml.YAMLError traceback out of every entry point.
        (tmp_config / "profile.yaml").write_text(
            'school_name: "unclosed\nrole_keywords: [\n', encoding="utf-8"
        )
        with pytest.raises(ProfileError, match="not valid YAML"):
            load_profile()

    def test_explicit_empty_lists_are_honored(self, tmp_config):
        # Review finding 3: `shared_employers: []` means "no employer hooks",
        # NOT "give me the aerospace defaults".
        (tmp_config / "profile.yaml").write_text(
            "shared_employers: []\nidentity_markers: []\nschool_signals: []\n",
            encoding="utf-8",
        )
        p = load_profile()
        assert p.shared_employers == ()
        assert p.identity_markers == ()
        assert p.school_signals == ()
        # And the empty lists actually disable the behaviors.
        from src.agents.finder import _generate_hook
        from src.agents.guardrails import detect_redundant_intro
        from src.core.schemas import ContactCandidate

        boeing_alum = ContactCandidate(
            full_name="A", company_slug="boeing", title="Nurse",
            linkedin_url="https://x/in/uiuc-grad",
        )
        # Tier 1 (uiuc URL) and Tier 2 (boeing slug) are disabled → falls
        # through to the Tier-3.5 title-derived hook.
        assert _generate_hook(boeing_alum) == "your work as Nurse"
        assert not detect_redundant_intro("UIUC and UIUC and aerospace engineering twice")

    def test_explicit_empty_focus_areas_means_structural_only(self, tmp_config):
        (tmp_config / "profile.yaml").write_text("focus_areas: []\n", encoding="utf-8")
        assert focus_area_names(load_profile()) == ("PEER", "ALUMNI_ACADEMIC")

    def test_explicit_structural_area_is_not_duplicated(self, tmp_config):
        (tmp_config / "profile.yaml").write_text(
            "focus_areas:\n"
            "  - name: BACKEND\n"
            "    description: backend\n"
            "  - name: PEER\n"
            "    description: generalist nurse, no clear specialty\n",
            encoding="utf-8",
        )
        p = load_profile()
        names = focus_area_names(p)
        assert names.count("PEER") == 1
        assert names.count("ALUMNI_ACADEMIC") == 1
        # The profile's own PEER wording wins over the structural default.
        assert p.focus_areas[1].description == "generalist nurse, no clear specialty"


# ---------------------------------------------------------------------------
# Resolver (the P2-deferred ranker wiring input)
# ---------------------------------------------------------------------------


_SWE = Profile(
    name="swe",
    focus_areas=(
        FocusAreaDef(
            name="BACKEND",
            description="distributed systems / APIs",
            keywords=("backend", "distributed systems", "go", "kafka"),
        ),
        FocusAreaDef(
            name="INFRA",
            description="platform / SRE",
            keywords=("infrastructure", "kubernetes", "sre"),
        ),
    ),
)


class TestResolveTargetFocus:
    def test_exact_function_name_wins(self):
        # The issue #61 exit-criteria user: function BACKEND resolves directly.
        assert resolve_target_focus("BACKEND", ["distributed systems", "Go", "Kafka"], _SWE) == (
            "BACKEND"
        )

    def test_function_name_is_case_and_separator_insensitive(self):
        assert resolve_target_focus("back-end", [], _SWE) is None  # not an area name
        assert resolve_target_focus("backend", [], _SWE) == "BACKEND"
        assert resolve_target_focus(" Backend ", [], _SWE) == "BACKEND"

    def test_keyword_overlap_resolves(self):
        assert resolve_target_focus(None, ["Kafka", "Go"], _SWE) == "BACKEND"
        assert resolve_target_focus("SWE", ["kubernetes"], _SWE) == "INFRA"

    def test_default_profile_aerospace_keywords(self):
        p = Profile()
        assert resolve_target_focus("QUALITY", ["MRB", "supplier quality"], p) == "MANUFACTURING"
        assert resolve_target_focus(None, ["stress", "FEA"], p) == "STRUCTURAL_ANALYSIS"

    def test_tie_returns_none(self):
        assert resolve_target_focus(None, ["kafka", "kubernetes"], _SWE) is None

    def test_no_terms_or_no_overlap_returns_none(self):
        assert resolve_target_focus(None, [], _SWE) is None
        assert resolve_target_focus(None, None, _SWE) is None
        assert resolve_target_focus("NURSE", ["icu"], _SWE) is None

    def test_structural_areas_never_resolve(self):
        assert resolve_target_focus("PEER", [], Profile()) is None
        assert resolve_target_focus("ALUMNI_ACADEMIC", [], Profile()) is None


# ---------------------------------------------------------------------------
# Wiring: config fallback + plan target_focus + profile-driven spots
# ---------------------------------------------------------------------------


class TestProfileWiring:
    def test_config_falls_back_to_profile_role_keywords(self, tmp_config, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("SERPER_API_KEY", "k")
        (tmp_config / "profile.yaml").write_text(
            "role_keywords: [backend engineer, platform engineer]\n", encoding="utf-8"
        )
        cfg = config_module.load_config()
        assert cfg.finder_role_keywords == ["backend engineer", "platform engineer"]

    def test_config_yaml_keywords_still_beat_profile(self, tmp_config, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("SERPER_API_KEY", "k")
        (tmp_config / "profile.yaml").write_text(
            "role_keywords: [backend engineer]\n", encoding="utf-8"
        )
        (tmp_config / "config.yaml").write_text(
            "pipeline:\n  finder_role_keywords: [from config]\n", encoding="utf-8"
        )
        (tmp_config / "config.yaml").chmod(0o600)
        cfg = config_module.load_config()
        assert cfg.finder_role_keywords == ["from config"]

    def test_guardrails_identity_markers_come_from_profile(self, tmp_config):
        from src.agents.guardrails import detect_redundant_intro

        (tmp_config / "profile.yaml").write_text(
            "identity_markers: [georgia tech, ms cs]\n", encoding="utf-8"
        )
        assert detect_redundant_intro("Georgia Tech then more Georgia Tech")
        assert not detect_redundant_intro("UIUC and UIUC again")  # old markers inactive

    def test_hook_generation_uses_profile_school_and_employers(self, tmp_config):
        from src.agents.finder import _generate_hook
        from src.core.schemas import ContactCandidate

        (tmp_config / "profile.yaml").write_text(
            "school_name: Georgia Tech\n"
            "school_signals: [gatech]\n"
            "shared_employers: [stripe]\n",
            encoding="utf-8",
        )
        alum = ContactCandidate(
            full_name="A", company_slug="acme", linkedin_url="https://x/in/gatech-grad"
        )
        assert _generate_hook(alum) == "we share a Georgia Tech background"
        ex_coworker = ContactCandidate(
            full_name="B", company_slug="acme", title="Engineer, ex-Stripe"
        )
        assert _generate_hook(ex_coworker) == "you also spent time at Stripe"

    def test_apply_classification_accepts_custom_profile_label(self, tmp_config):
        from src.agents.finder import apply_classification

        (tmp_config / "profile.yaml").write_text(
            "focus_areas:\n  - name: BACKEND\n    description: backend\n", encoding="utf-8"
        )
        persona, focus, _ = apply_classification("PEER_ENGINEER", "BACKEND", None)
        assert focus == "BACKEND"
        # An old-taxonomy label is now unknown → safe PEER fallback.
        _, focus, _ = apply_classification("PEER_ENGINEER", "MATERIALS", None)
        assert focus == "PEER"

    def test_apply_classification_returns_plain_labels(self, tmp_config):
        # Focus labels are plain strings pipeline-wide (post-review cleanup);
        # they still compare equal to the FocusArea members (StrEnum).
        from src.agents.finder import apply_classification

        _, focus, _ = apply_classification("PEER_ENGINEER", "COMPOSITE_DESIGN", None)
        assert type(focus) is str
        assert focus == FocusArea.COMPOSITE_DESIGN

    def test_drafter_fallback_identity_from_profile(self, tmp_config, monkeypatch):
        import src.agents.drafter as drafter_module

        (tmp_config / "profile.yaml").write_text(
            "fallback_identity: Alex Rivera, MS CS Georgia Tech (May 2026)\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            drafter_module, "_PERSONA_TEMPLATE_DIR", tmp_config / "missing-templates"
        )
        from src.core.schemas import Persona

        text = drafter_module._load_persona_template(Persona.PEER_ENGINEER)
        assert text == "Write outreach messages as Alex Rivera, MS CS Georgia Tech (May 2026)."

    def test_drafter_templates_dir_override(self, tmp_config):
        import src.agents.drafter as drafter_module
        from src.core.schemas import Persona

        tdir = tmp_config / "templates"
        tdir.mkdir()
        (tdir / "peer_engineer.md").write_text("CUSTOM PEER TEMPLATE", encoding="utf-8")
        (tmp_config / "profile.yaml").write_text(
            f"templates_dir: {tdir}\n", encoding="utf-8"
        )
        assert drafter_module._load_persona_template(Persona.PEER_ENGINEER) == (
            "CUSTOM PEER TEMPLATE"
        )
        # A persona file the custom dir lacks falls back to the built-in, with
        # [[SCHOOL]] filled from the profile (default school → "UIUC").
        alumni = drafter_module._load_persona_template(Persona.ALUMNI)
        assert "UIUC" in alumni

    def test_persona_templates_carry_no_hardcoded_identity(self, tmp_config):
        """#99: the built-in templates leak no sender name or fixed discipline."""
        import src.agents.drafter as drafter_module
        from src.core.schemas import Persona

        banned = ("Pathipaka", "Siddardth", "aerospace", "STEM OPT", "Illini", "MS in AE")
        for persona in Persona:
            text = drafter_module._load_persona_template(persona)
            for token in banned:
                assert token not in text, f"{persona} template leaks {token!r}"

    def test_alumni_template_school_from_profile(self, tmp_config):
        """#99: a custom profile's school flows into the alumni template."""
        import src.agents.drafter as drafter_module
        from src.core.schemas import Persona

        (tmp_config / "profile.yaml").write_text("school_name: Georgia Tech\n", encoding="utf-8")
        alumni = drafter_module._load_persona_template(Persona.ALUMNI)
        assert "Georgia Tech" in alumni
        assert "UIUC" not in alumni
        assert "[[SCHOOL]]" not in alumni  # placeholder fully interpolated

    def test_alumni_ask_angle_school_from_profile(self, tmp_config):
        from src.agents.drafter import _alumni_ask_angles

        angles = _alumni_ask_angles("Georgia Tech")
        assert (
            "how their own Georgia Tech-to-industry transition went "
            "and what they'd do differently" in angles
        )

    def test_coerce_focus_label(self, tmp_config):
        from src.core.profile import coerce_focus_label

        (tmp_config / "profile.yaml").write_text(
            "focus_areas:\n  - name: BACKEND\n    description: backend\n", encoding="utf-8"
        )
        assert coerce_focus_label("BACKEND") == "BACKEND"
        assert coerce_focus_label("backend") == "BACKEND"  # case-normalized
        assert coerce_focus_label("MATERIALS") == "PEER"  # not in this taxonomy
        assert coerce_focus_label(None) == "PEER"
        # Importer contract: default=None so the classifier fills unknowns.
        assert coerce_focus_label("MATERIALS", default=None) is None
        assert coerce_focus_label(None, default=None) is None

    def test_build_classify_context_accepts_explicit_profile(self):
        from src.agents.finder import build_classify_context
        from src.core.schemas import ContactCandidate

        ctx = build_classify_context(
            ContactCandidate(full_name="A", company_slug="acme"), "acme", profile=_SWE
        )
        assert set(ctx["focus_options"]) == {"BACKEND", "INFRA"}


class TestJobsHostPlanTargetFocus:
    @pytest.fixture
    def tmp_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
        monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
        return tmp_path

    def _plan(self, tmp_path, capsys, feed: dict) -> tuple[int, dict]:
        import argparse

        from src.cli.network_jobs_host import run_plan

        p = tmp_path / "feed.json"
        p.write_text(json.dumps(feed), encoding="utf-8")
        rc = run_plan(argparse.Namespace(feed=str(p)))
        return rc, json.loads(capsys.readouterr().out)

    def test_plan_emits_resolved_target_focus(self, tmp_config, tmp_db, capsys):
        rc, out = self._plan(tmp_db, capsys, {
            "schema": "application-feed/v1",
            "profile_ref": "default",
            "applications": [{
                "job_id": "j1", "company": "Boeing", "role_title": "Stress Engineer",
                "function": "STRUCTURES", "target_keywords": ["stress", "FEA"],
            }],
        })
        assert rc == 0
        assert out["profile"] == "default"
        assert out["postings"][0]["target_focus"] == "STRUCTURAL_ANALYSIS"

    def test_plan_null_target_focus_when_unresolvable(self, tmp_config, tmp_db, capsys):
        rc, out = self._plan(tmp_db, capsys, {
            "schema": "application-feed/v1",
            "applications": [{
                "job_id": "j2", "company": "Acme", "role_title": "Barista",
            }],
        })
        assert rc == 0
        assert out["postings"][0]["target_focus"] is None

    def test_plan_unknown_profile_ref_errors(self, tmp_config, tmp_db, capsys):
        rc, out = self._plan(tmp_db, capsys, {
            "schema": "application-feed/v1",
            "profile_ref": "ghost",
            "applications": [{
                "job_id": "j3", "company": "Acme", "role_title": "Engineer",
            }],
        })
        assert rc == 1
        assert "ghost" in out["error"]

    def test_plan_malformed_named_profile_errors_cleanly(self, tmp_config, tmp_db, capsys):
        # Review finding 2 at the plan verb: invalid YAML in a named profile →
        # JSON error + rc 1, not a raw traceback.
        (tmp_config / "profiles").mkdir()
        (tmp_config / "profiles" / "broken.yaml").write_text(
            'name: "unclosed\nrole_keywords: [\n', encoding="utf-8"
        )
        rc, out = self._plan(tmp_db, capsys, {
            "schema": "application-feed/v1",
            "profile_ref": "broken",
            "applications": [{
                "job_id": "j4", "company": "Acme", "role_title": "Engineer",
            }],
        })
        assert rc == 1
        assert "not valid YAML" in out["error"]

    def test_ranker_scores_target_focus_match(self):
        from src.agents.ranker import rank_contact
        from src.core.schemas import ContactCandidate

        contact = ContactCandidate(
            full_name="C", company_slug="acme", focus_area="STRUCTURAL_ANALYSIS"
        )
        base = rank_contact(contact).total
        boosted = rank_contact(contact, target_focus="STRUCTURAL_ANALYSIS").total
        assert boosted == base + 10
