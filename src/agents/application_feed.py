"""
src/agents/application_feed.py
Application-mode input parser (Phase B, #58) — the second input front-door.

Parses an application-feed file (a list of job postings, feed schema §4) into
canonical :class:`~src.core.schemas.Application` records, and offers a dry-run
``validate_application_feed`` for producers. Mirrors the shape of
``src/agents/importer.py`` (a wrapped-error type following the ContactImportError
pattern, a JSON reader that surfaces malformed input cleanly, and a
parse/validate split). Ships DARK in P1 — this module NEVER writes to the DB;
P2/#59 wires ``/network-jobs`` on top of it.

Traceability: docs/APPLICATION_FEED_INPUT_DESIGN_2026-06-30.md §4, §10 (P1).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from src.core.schemas import Application

__all__ = [
    "ApplicationFeedError",
    "parse_application_feed",
    "validate_application_feed",
]

_SCHEMA = "application-feed/v1"


class ApplicationFeedError(ValueError):
    """Raised when an application-feed file cannot be parsed into postings.

    Parallels ``importer.ContactImportError`` — a distinct entity (postings, not
    contacts) gets its own name while following the same wrap-malformed-input
    pattern so callers get a clean error, not a raw ``JSONDecodeError``.
    """


def _read_feed(path: Path) -> tuple[list, dict]:
    """Return ``(raw_postings_list, feed_meta)`` from a feed file.

    A feed is a JSON object ``{schema, profile_ref, applications: [...]}``. The
    returned list is raw (entries are not yet coerced to :class:`Application`, and
    non-object entries are left in place so the caller can surface them — no
    silent drops). Malformed JSON or a wrong top-level shape is wrapped in
    :class:`ApplicationFeedError` (importer._read_rows pattern).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApplicationFeedError(f"Malformed JSON in {path.name}: {exc}") from exc

    if not isinstance(data, dict):
        raise ApplicationFeedError(
            f"Application feed must be a JSON object with an 'applications' list: "
            f"{path.name}"
        )
    applications = data.get("applications")
    if not isinstance(applications, list):
        raise ApplicationFeedError(
            f"Application feed missing an 'applications' list: {path.name}"
        )
    meta = {"schema": data.get("schema"), "profile_ref": data.get("profile_ref")}
    return applications, meta


def _posting_fields(exc: ValidationError) -> str:
    """Compact 'field1, field2' from a pydantic ValidationError for a posting."""
    return ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())


def parse_application_feed(path: str | Path) -> tuple[list[Application], dict]:
    """Parse an application-feed file into ``(applications, report)``.

    No DB writes. Each posting is coerced to an :class:`Application` (required
    ``job_id`` / ``company`` / ``role_title``, ``company_slug`` derived from
    ``company`` when absent). Malformed postings are **counted, not silently
    dropped** — the ``report`` is the "no silent caps" record::

        {"schema", "profile_ref", "postings_read", "usable",
         "dropped": {"not_object": int, "invalid": int, "duplicate": int}}

    A non-object entry → ``not_object``; a posting failing validation →
    ``invalid``; a repeated ``job_id`` → ``duplicate`` (first wins, since
    ``job_id`` is the linkage PK). Raises :class:`ApplicationFeedError` only when
    the file itself is unreadable/misshaped (see :func:`_read_feed`).
    """
    path = Path(path)
    raw, meta = _read_feed(path)

    dropped = {"not_object": 0, "invalid": 0, "duplicate": 0}
    apps: list[Application] = []
    seen: set[str] = set()
    for raw_posting in raw:
        if not isinstance(raw_posting, dict):
            dropped["not_object"] += 1
            continue
        try:
            app = Application(**raw_posting)
        except ValidationError:
            dropped["invalid"] += 1
            continue
        if app.job_id in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(app.job_id)
        apps.append(app)

    report = {
        "schema": meta.get("schema"),
        "profile_ref": meta.get("profile_ref"),
        "postings_read": len(raw),
        "usable": len(apps),
        "dropped": dropped,
    }
    return apps, report


def validate_application_feed(path: str | Path) -> dict:
    """Dry-run check of an application-feed file — the producer contract validator.

    Returns ``{"ok": bool, "count": int, "errors": [...], "warnings": [...]}``
    without writing anything. Surfaces every malformed posting (non-object entry,
    missing/invalid required field, or a duplicate ``job_id``) so a bad feed is
    caught before any discovery runs — the Application-mode analogue of
    ``importer.validate_contacts_file``.
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        raw, meta = _read_feed(path)
    except (ApplicationFeedError, OSError) as exc:
        return {"ok": False, "count": 0, "errors": [f"parse failed: {exc}"], "warnings": []}

    schema = meta.get("schema")
    if schema not in (None, _SCHEMA):
        warnings.append(f"unrecognized schema {schema!r} — expected {_SCHEMA!r}")

    seen: set[str] = set()
    usable = 0
    for i, raw_posting in enumerate(raw):
        if not isinstance(raw_posting, dict):
            errors.append(f"posting {i}: not a JSON object")
            continue
        try:
            app = Application(**raw_posting)
        except ValidationError as exc:
            errors.append(f"posting {i}: invalid ({_posting_fields(exc)})")
            continue
        if app.job_id in seen:
            errors.append(f"posting {i} ({app.job_id}): duplicate job_id")
            continue
        seen.add(app.job_id)
        if not app.job_url:
            warnings.append(
                f"posting {i} ({app.job_id}): no job_url — drafts can't cite the posting"
            )
        usable += 1

    return {"ok": usable > 0 and not errors, "count": usable,
            "errors": errors, "warnings": warnings}
