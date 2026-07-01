"""User profile — the identity/domain layer (Phase B P4, issue #61).

The agent is profile-driven, not person-driven: everything that used to be
hardcoded to one user (aerospace role keywords, UIUC school signals, the
focus-area taxonomy, the shared-employer list, identity markers, the drafter's
identity fallback, persona-template selection) lives on a :class:`Profile`.

The **default profile is the existing aerospace user** — every default field
value below is byte-identical to the constant it replaced, so an install with
no ``profile.yaml`` behaves exactly as before (zero regression, the issue's
DoD). A ``profile.yaml`` next to ``config.yaml`` overrides fields for a
different user/field; an Application feed's ``profile_ref`` selects a named
profile from ``profiles/<ref>.yaml``.

The profile is deliberately **thin** (design decision #5): it references the
existing ``voice.md`` + ``resume_library.yaml`` (resolved by the same
config-dir convention) rather than rebuilding what they hold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

__all__ = [
    "FocusAreaDef",
    "Profile",
    "focus_area_names",
    "load_profile",
    "profile_path",
    "resolve_target_focus",
]

# Focus areas every profile must have: the classifier's safe fallback (PEER)
# and the persona-forced academic bucket (ALUMNI_ACADEMIC). The loader appends
# them when a custom profile omits them, so `apply_classification`'s structural
# rules (ALUMNI→ALUMNI_ACADEMIC, RECRUITER→PEER, unknown→PEER) always resolve.
STRUCTURAL_FOCUS_AREAS = ("PEER", "ALUMNI_ACADEMIC")


@dataclass(frozen=True)
class FocusAreaDef:
    """One focus area in a profile's taxonomy.

    ``description`` is the host-path classify hint; ``api_description`` is the
    API tool-schema wording (kept separate because the API prompt's exact
    wording is accuracy-validated — see finder.py — and the two historically
    differ in spacing). ``keywords`` feed the free-form resolver
    (:func:`resolve_target_focus`) — they never reach a prompt.
    """

    name: str
    description: str
    api_description: str | None = None
    keywords: tuple[str, ...] = ()
    # Tier-3 title-specialty hook (finder): when any hook_keyword appears in a
    # contact's title, `hook` is the deterministic hook phrase. Areas without
    # one simply never produce a Tier-3 hook (Tier 3.5 covers them).
    hook: str | None = None
    hook_keywords: tuple[str, ...] = ()

    @property
    def api_desc(self) -> str:
        return self.api_description if self.api_description is not None else self.description


# The default (aerospace) taxonomy. description == the exact _FOCUS_OPTIONS
# strings this replaced; api_description == the exact API tool-schema strings.
_DEFAULT_FOCUS_AREAS: tuple[FocusAreaDef, ...] = (
    FocusAreaDef(
        name="COMPOSITE_DESIGN",
        description="composites / carbon fiber",
        api_description="composites/carbon fiber",
        keywords=("composite", "composites", "carbon fiber", "layup", "laminate"),
        hook="your composites work",
        hook_keywords=("composite", "carbon fiber", "fiber reinforced"),
    ),
    FocusAreaDef(
        name="STRUCTURAL_ANALYSIS",
        description="stress / loads / FEA / airframe",
        api_description="stress/loads/FEA/airframe",
        keywords=("stress", "structures", "structural", "loads", "fea", "airframe"),
        hook="your structures work",
        hook_keywords=("structural", "structures", "stress", "loads", "fea", "airframe"),
    ),
    FocusAreaDef(
        name="MANUFACTURING",
        description="production / quality / MRB / supplier",
        api_description="production/quality/MRB/supplier",
        keywords=("manufacturing", "production", "quality", "mrb", "supplier"),
        hook="your manufacturing and quality background",
        hook_keywords=("quality", "mrb", "supplier", "manufacturing engineer", "production"),
    ),
    FocusAreaDef(
        name="MATERIALS",
        description="metallurgy / alloys",
        api_description="metallurgy/alloys",
        keywords=("materials", "metallurgy", "alloy", "alloys"),
        hook="your materials science background",
        hook_keywords=("materials", "metallurgy", "alloy", "coating"),
    ),
    FocusAreaDef(
        name="ADDITIVE",
        description="3D printing",
        api_description="3D printing",
        keywords=("additive", "3d printing", "3d-printing"),
        hook="your additive manufacturing work",
        hook_keywords=("additive", "3d print"),
    ),
    FocusAreaDef(
        name="PEER",
        description="generalist engineer, NO clear specialty — use this, do NOT guess one",
        api_description=(
            "a generalist engineer with NO clear single "
            "specialty — use this (do NOT guess a specialty) when "
            "the title/snippet doesn't clearly point to one above"
        ),
    ),
    FocusAreaDef(
        name="ALUMNI_ACADEMIC",
        description="academic / PhD / research / student",
        api_description="academic/PhD/research/student",
    ),
)


@dataclass(frozen=True)
class Profile:
    """One user's identity + domain configuration.

    Field defaults ARE the default profile (the current aerospace user) —
    ``Profile()`` reproduces pre-#61 behavior exactly.
    """

    name: str = "default"
    # Drafter fallback when a persona template file is missing.
    fallback_identity: str = "Siddardth Pathipaka, MS Aerospace UIUC (Dec 2025)"
    # Shortest self-identity, quoted in the length-regeneration note.
    identity_short: str = "MS AE at UIUC, composites"
    # Short school name — hook phrasing + the alumni ask-rotation angle.
    school_name: str = "UIUC"
    # Lowercase substrings that mark a shared-school (Tier 1 hook) match.
    school_signals: tuple[str, ...] = ("uiuc", "university of illinois", "urbana-champaign")
    # Lowercase employer names for the Tier 2 shared-employer hook.
    shared_employers: tuple[str, ...] = (
        "tata",
        "ge",
        "general electric",
        "boeing",
        "lockheed",
        "airbus",
        "honeywell",
    )
    # Identity phrases that should appear at most once per message (AUDIT-A8).
    identity_markers: tuple[str, ...] = (
        "uiuc",
        "university of illinois",
        "urbana-champaign",
        "aerospace engineering",
        "ms aerospace",
    )
    # Default discovery role keywords (Finder) — overridable per call
    # (Application mode) or via config pipeline.finder_role_keywords.
    role_keywords: tuple[str, ...] = (
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
    # The focus-area taxonomy (classifier labels + resolver keywords).
    focus_areas: tuple[FocusAreaDef, ...] = _DEFAULT_FOCUS_AREAS
    # Optional directory of persona templates overriding the built-in
    # (aerospace-voiced) ones. Missing files fall back to the built-ins.
    templates_dir: str | None = None


def focus_area_names(profile: Profile) -> tuple[str, ...]:
    return tuple(fa.name for fa in profile.focus_areas)


def profile_path(ref: str | None = None) -> Path:
    """Path of the profile file for *ref* (``None``/"default" → profile.yaml)."""
    from src.core.config import config_dir

    if not ref or ref == "default":
        return config_dir() / "profile.yaml"
    return config_dir() / "profiles" / f"{ref}.yaml"


def load_profile(ref: str | None = None) -> Profile:
    """Load the profile for *ref*.

    Inputs: optional profile ref (an Application feed's ``profile_ref``).
    Output: a :class:`Profile`. The default ref with no ``profile.yaml`` on
    disk returns the built-in default (zero regression). A **named** ref whose
    file is missing raises ``FileNotFoundError`` — silently drafting as the
    wrong person is worse than failing loudly. Reads the filesystem; no other
    side effects.
    """
    path = profile_path(ref)
    if not path.exists():
        if ref and ref != "default":
            raise FileNotFoundError(
                f"profile_ref '{ref}' names no profile file at {path} — "
                "create it (see config/profile.example.yaml) or fix the ref"
            )
        return Profile()
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        logger.warning("profile file %s is not a mapping; using defaults", path)
        return Profile()
    return _profile_from_dict(data, name=ref or "default")


def _profile_from_dict(data: dict, name: str) -> Profile:
    """Build a Profile from parsed YAML, defaulting missing fields (thin file)."""
    defaults = Profile()

    def _tuple(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        raw = data.get(key)
        if not raw:
            return fallback
        return tuple(str(item) for item in raw)

    focus_areas = _parse_focus_areas(data.get("focus_areas"))
    return Profile(
        name=str(data.get("name") or name),
        fallback_identity=str(data.get("fallback_identity") or defaults.fallback_identity),
        identity_short=str(data.get("identity_short") or defaults.identity_short),
        school_name=str(data.get("school_name") or defaults.school_name),
        school_signals=_tuple("school_signals", defaults.school_signals),
        shared_employers=_tuple("shared_employers", defaults.shared_employers),
        identity_markers=_tuple("identity_markers", defaults.identity_markers),
        role_keywords=_tuple("role_keywords", defaults.role_keywords),
        focus_areas=focus_areas,
        templates_dir=str(data["templates_dir"]) if data.get("templates_dir") else None,
    )


def _parse_focus_areas(raw: object) -> tuple[FocusAreaDef, ...]:
    """Parse the YAML focus_areas list; append missing structural areas."""
    if not raw or not isinstance(raw, list):
        return _DEFAULT_FOCUS_AREAS
    areas: list[FocusAreaDef] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            logger.warning("skipping malformed focus_areas entry: %r", item)
            continue
        areas.append(
            FocusAreaDef(
                name=str(item["name"]).strip().upper(),
                description=str(item.get("description") or item["name"]),
                api_description=(
                    str(item["api_description"]) if item.get("api_description") else None
                ),
                keywords=tuple(str(k) for k in (item.get("keywords") or [])),
                hook=str(item["hook"]) if item.get("hook") else None,
                hook_keywords=tuple(str(k) for k in (item.get("hook_keywords") or [])),
            )
        )
    if not areas:
        return _DEFAULT_FOCUS_AREAS
    present = {a.name for a in areas}
    structural = {fa.name: fa for fa in _DEFAULT_FOCUS_AREAS if fa.name in STRUCTURAL_FOCUS_AREAS}
    for sname in STRUCTURAL_FOCUS_AREAS:
        if sname not in present:
            areas.append(structural[sname])
    return tuple(areas)


def resolve_target_focus(
    function: str | None,
    target_keywords: list[str] | None,
    profile: Profile,
) -> str | None:
    """Resolve a feed's free-form ``function``/``target_keywords`` to a focus area.

    This is the taxonomy resolver from issue #61: the value it returns feeds
    the ranker's ``target_focus`` signal (the P2-deferred wiring). Pure — no
    I/O, no LLM.

    Resolution: (1) an exact (case/space-insensitive) match of ``function``
    against an area name wins outright — ``function: "BACKEND"`` hits a
    profile's BACKEND area directly; (2) otherwise the area whose keyword list
    best overlaps the terms wins; a tie or zero overlap returns ``None``
    (skipping the +10 rank signal is safer than guessing the wrong team).
    Structural areas (PEER, ALUMNI_ACADEMIC) are never role targets.
    """
    domain = [fa for fa in profile.focus_areas if fa.name not in STRUCTURAL_FOCUS_AREAS]
    if function:
        canon = function.strip().upper().replace(" ", "_").replace("-", "_")
        for fa in domain:
            if fa.name == canon:
                return fa.name

    terms = [t.strip().lower() for t in [function or "", *(target_keywords or [])] if t.strip()]
    if not terms:
        return None
    best_name: str | None = None
    best_score = 0
    tied = False
    for fa in domain:
        keywords = [k.lower() for k in fa.keywords]
        # ponytail: substring overlap, no stemming — mirror achievement_matcher;
        # upgrade both together if matching quality ever becomes the bottleneck.
        score = sum(1 for t in terms if any(k in t or t in k for k in keywords))
        if score > best_score:
            best_name, best_score, tied = fa.name, score, False
        elif score == best_score and score > 0:
            tied = True
    if best_score > 0 and not tied:
        return best_name
    return None
