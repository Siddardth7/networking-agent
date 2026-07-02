"""
src/cli/network_setup_host.py
Onboarding bridge (ROADMAP B3 P1, #76) — the deterministic verbs the
host-token `/network-setup` wizard drives to inspect and write a user's
config files. Ships dark until P2 (#77) wires the command.

Verbs (no LLM, no network):

  - ``status``
    → JSON per user file (config.yaml, profile.yaml, voice.md,
      resume_library.yaml): exists / valid / a short summary. Validation uses
      the REAL runtime loaders (the #61 profile parser, the ResumeLibrary
      model), so "valid here" means "loads at runtime". Errors are reported
      as messages, never tracebacks.
  - ``scaffold``
    → write the config.yaml skeleton via the existing
      ``config.write_default_config`` (0o600; never overwrites).
  - ``write profile|voice|resume`` (content on stdin)
    → validate → timestamped ``.bak`` of any existing file → write the RAW
      text (user comments survive). Invalid content is refused with the
      validator's message; the existing file is left untouched.

config.yaml itself is deliberately NOT writable here beyond ``scaffold`` —
API keys stay a manual edit the doctor (`network_check`) verifies.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.agents.achievement_matcher import ResumeLibrary
from src.core.config import (
    _resolve_config_path,
    resume_library_path,
    voice_doc_path,
)
from src.core.errors import ProfileError
from src.core.profile import Profile, focus_area_names, load_profile, profile_path

__all__ = ["run_setup_host"]

# Mirror of drafter._VOICE_DOC_MAX_CHARS: past this the drafter truncates the
# voice doc, so the wizard warns at write time instead of surprising later.
_VOICE_MAX_CHARS = 16 * 1024

_SENTINEL = "REPLACE_ME"

# Valid top-level profile.yaml keys (the Profile dataclass fields). The #61
# loader silently ignores unknown keys — fine at runtime, but a wizard write
# with a typo'd key ("template_dir") should say so.
_PROFILE_KEYS = frozenset(Profile.__dataclass_fields__)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _config_status() -> dict:
    path = _resolve_config_path()
    entry: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return entry
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        entry.update(valid=False, error=f"not valid YAML: {exc}")
        return entry
    keys = data.get("keys") if isinstance(data, dict) else None
    unfilled = sorted(
        name for name, value in (keys or {}).items() if value == _SENTINEL
    )
    entry.update(valid=True, unfilled_keys=unfilled)
    return entry


def _profile_status() -> dict:
    path = profile_path()
    entry: dict = {"path": str(path), "exists": path.exists()}
    try:
        # Honors NETWORKING_AGENT_PROFILE; a named ref pointing nowhere is a
        # status finding (FileNotFoundError), not a crash.
        profile = load_profile()
    except (ProfileError, FileNotFoundError) as exc:
        entry.update(valid=False, error=str(exc))
        return entry
    entry.update(
        valid=True,
        name=profile.name,
        focus_areas=list(focus_area_names(profile)),
        # Absent file = the built-in aerospace default — the wizard's cue.
        using_builtin_default=not path.exists(),
    )
    return entry


def _voice_status() -> dict:
    path = voice_doc_path()
    entry: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return entry
    text = path.read_text(encoding="utf-8")
    entry.update(valid=bool(text.strip()), chars=len(text))
    if len(text) > _VOICE_MAX_CHARS:
        entry["warning"] = f"over {_VOICE_MAX_CHARS} chars — the drafter truncates it"
    return entry


def _resume_status() -> dict:
    path = resume_library_path()
    entry: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return entry
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        library = ResumeLibrary.model_validate(data or {"projects": []})
    except (yaml.YAMLError, ValidationError) as exc:
        entry.update(valid=False, error=str(exc))
        return entry
    entry.update(
        valid=True,
        projects=len(library.projects),
        bullets=sum(len(p.bullets) for p in library.projects),
    )
    return entry


def run_status(args: argparse.Namespace) -> int:
    """Report each user file's existence/validity/summary as JSON."""
    del args
    print(json.dumps({
        "config": _config_status(),
        "profile": _profile_status(),
        "voice": _voice_status(),
        "resume_library": _resume_status(),
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


def run_scaffold(args: argparse.Namespace) -> int:
    """Write the config.yaml skeleton (existing helper; never overwrites)."""
    del args
    from src.core.config import write_default_config

    path = _resolve_config_path()
    existed = path.exists()
    write_default_config(path)
    print(json.dumps({
        "path": str(path),
        "created": not existed,
        "note": "fill the REPLACE_ME keys, then run: python -m src.cli.network_check",
    }))
    return 0


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def _validate_profile(content: str) -> tuple[str | None, list[str]]:
    """Return (error, warnings) for profile.yaml *content*."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return f"not valid YAML: {exc}", []
    # The runtime loader tolerates a non-mapping by falling back to the
    # default profile — a wizard writing one is a mistake, so refuse it.
    if not isinstance(data, dict):
        return "profile.yaml must be a YAML mapping of profile fields", []
    warnings = [
        f"unknown key '{key}' (ignored by the loader — typo?)"
        for key in sorted(set(data) - _PROFILE_KEYS)
    ]
    return None, warnings


def _validate_voice(content: str) -> tuple[str | None, list[str]]:
    if not content.strip():
        return "voice.md is empty", []
    if len(content) > _VOICE_MAX_CHARS:
        return None, [f"over {_VOICE_MAX_CHARS} chars — the drafter truncates it"]
    return None, []


def _validate_resume(content: str) -> tuple[str | None, list[str]]:
    try:
        data = yaml.safe_load(content)
        library = ResumeLibrary.model_validate(data or {"projects": []})
    except yaml.YAMLError as exc:
        return f"not valid YAML: {exc}", []
    except ValidationError as exc:
        return str(exc), []
    if not library.projects:
        return None, ["no projects — the drafter will have no approved facts"]
    return None, []


_WRITE_TARGETS = {
    "profile": (profile_path, _validate_profile),
    "voice": (voice_doc_path, _validate_voice),
    "resume": (resume_library_path, _validate_resume),
}


def _backup(path: Path) -> str | None:
    """Copy an existing *path* aside; return the backup path (None if absent)."""
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.{stamp}.bak")
    n = 1
    while candidate.exists():  # same-second rewrites (wizard retries)
        candidate = path.with_name(f"{path.name}.{stamp}-{n}.bak")
        n += 1
    shutil.copy2(path, candidate)
    return str(candidate)


def run_write(args: argparse.Namespace) -> int:
    """Validate stdin content for *target*, back up any existing file, write."""
    path_fn, validate = _WRITE_TARGETS[args.target]
    content = sys.stdin.read()
    error, warnings = validate(content)
    if error is not None:
        print(json.dumps({"error": error}))
        return 1
    path = path_fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(path)
    # Raw text, not a re-dump — the user's YAML comments survive.
    path.write_text(content, encoding="utf-8")
    print(json.dumps({"written": str(path), "backup": backup, "warnings": warnings}))
    return 0


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def run_setup_host(args: argparse.Namespace) -> int:
    """Dispatch the ``status`` / ``scaffold`` / ``write`` verbs."""
    if args.verb == "status":
        return run_status(args)
    if args.verb == "scaffold":
        return run_scaffold(args)
    return run_write(args)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Onboarding bridge (#76): status | scaffold | write profile|voice|resume."
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    sub.add_parser("status", help="Existence/validity/summary of each user file as JSON")
    sub.add_parser("scaffold", help="Write the config.yaml skeleton (never overwrites)")

    p_write = sub.add_parser("write", help="Validate stdin, back up, write a user file")
    p_write.add_argument("target", choices=sorted(_WRITE_TARGETS))

    sys.exit(run_setup_host(parser.parse_args()))
