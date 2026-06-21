"""
src/agents/importer.py
Source-agnostic contact import: normalize any leads file (Apollo / Apify /
Serper / Cowork+Chrome / manual CSV or JSON) into canonical ContactCandidate
records and run them through the shared ingest → classify → hook → save path.

Traceability: docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from src.agents.finder import ingest_contacts
from src.core.config import get_anthropic_client, load_config
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate, FocusArea, Persona

__all__ = [
    "ContactImportError",
    "import_contacts",
    "parse_contacts_file",
    "validate_contacts_file",
]


class ContactImportError(ValueError):  # named to avoid shadowing builtin ImportError
    """Raised when an input file cannot be parsed into canonical contacts."""


# ---------------------------------------------------------------------------
# Header / key aliasing — one map covers Apollo, Apify, manual exports, and the
# canonical schema, so most sources need no per-source code.
# ---------------------------------------------------------------------------

# normalized-header -> canonical field
_ALIAS: dict[str, str] = {
    # name (full, or first/last combined)
    "full name": "full_name",
    "fullname": "full_name",
    "name": "full_name",
    "contact name": "full_name",
    "first name": "first_name",
    "firstname": "first_name",
    "first": "first_name",
    "last name": "last_name",
    "lastname": "last_name",
    "last": "last_name",
    # title
    "title": "title",
    "job title": "title",
    "headline": "title",
    "occupation": "title",
    "position": "title",
    "current title": "title",
    # linkedin url
    "linkedin": "linkedin_url",
    "linkedin url": "linkedin_url",
    "linkedin_url": "linkedin_url",
    "linkedinurl": "linkedin_url",
    "person linkedin url": "linkedin_url",
    "profile url": "linkedin_url",
    "profileurl": "linkedin_url",
    "public profile url": "linkedin_url",
    # email
    "email": "email",
    "email address": "email",
    "work email": "email",
    "person email": "email",
    # company
    "company": "company",
    "company name": "company",
    "organization": "company",
    "organization name": "company",
    "current company": "company",
    "employer": "company",
    # company slug (explicit)
    "company slug": "company_slug",
    "company_slug": "company_slug",
    # location
    "location": "location",
    "person location": "location",
    "city": "location",
    "location name": "location",
    # about / snippet
    "about": "about",
    "summary": "about",
    "snippet": "about",
    "bio": "about",
    "description": "about",
    # explicit overrides
    "persona": "persona",
    "focus area": "focus_area",
    "focus_area": "focus_area",
    "hook": "hook",
}

def _norm_header(h: str) -> str:
    """Lowercase, collapse non-alphanumerics to single spaces, strip."""
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def _slugify(name: str) -> str:
    """Company name → url-safe slug ('Joby Aviation' → 'joby-aviation')."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "unknown"


def _apply_aliases(raw: dict) -> dict:
    """Map a raw row/object's keys to canonical fields, combining first/last name.

    Unknown keys are ignored. Returns a dict of canonical_field -> value (str),
    with blanks dropped.
    """
    canon: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        field = _ALIAS.get(_norm_header(key))
        if field is not None and field not in canon:
            canon[field] = text

    # Combine first + last when there's no explicit full name.
    if "full_name" not in canon:
        first = canon.pop("first_name", "")
        last = canon.pop("last_name", "")
        combined = f"{first} {last}".strip()
        if combined:
            canon["full_name"] = combined
    else:
        canon.pop("first_name", None)
        canon.pop("last_name", None)

    return canon


def _coerce_enum(value, enum_cls):
    """Lenient enum parse: return the member or None (so the classifier runs)."""
    if value is None:
        return None
    try:
        return enum_cls(str(value).strip().upper())
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def _read_rows(path: Path, source: str) -> tuple[list[dict], dict]:
    """Return (list of raw record dicts, file-level meta dict).

    Supports CSV and JSON. JSON may be a bare list of contacts, or an object
    ``{company, company_slug, location, source, contacts: [...]}``. Source is
    only used as a hint today (the alias map handles Apollo/Apify uniformly);
    'auto' detects by extension.
    """
    suffix = path.suffix.lower()
    if source in ("apollo", "manual") or (source == "auto" and suffix == ".csv"):
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh)), {}
    if source in ("serper", "apify", "chrome") or (source == "auto" and suffix == ".json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)], {}
        if isinstance(data, dict):
            contacts = data.get("contacts")
            if isinstance(contacts, list):
                meta = {k: data[k] for k in ("company", "company_slug", "location", "source")
                        if k in data}
                return [r for r in contacts if isinstance(r, dict)], meta
            # A single bare contact object.
            return [data], {}
    raise ContactImportError(
        f"Unsupported or undetectable file format: {path.name} (source={source})"
    )


def parse_contacts_file(
    path: str | Path,
    source: str = "auto",
    *,
    default_company: str | None = None,
    default_location: str | None = None,
) -> list[ContactCandidate]:
    """Parse a leads file into canonical ContactCandidate records (no DB writes).

    Each record resolves its company from (record company/company_slug → file
    meta → *default_company*). Records without a resolvable company or a
    full_name are skipped — see :func:`validate_contacts_file` to surface those.
    Deduplicates by normalized LinkedIn URL, else by name+company.
    """
    path = Path(path)
    raw_rows, meta = _read_rows(path, source)
    meta_company = meta.get("company") or meta.get("company_slug") or default_company
    meta_location = meta.get("location") or default_location

    candidates: list[ContactCandidate] = []
    seen: set[str] = set()
    for raw in raw_rows:
        canon = _apply_aliases(raw)
        full_name = canon.get("full_name")
        if not full_name:
            continue

        company = canon.get("company_slug") or canon.get("company") or meta_company
        if not company:
            continue
        company_slug = _slugify(company)

        url = (canon.get("linkedin_url") or "").rstrip("/").lower()
        dedup_key = url or f"{full_name.lower()}|{company_slug}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        candidates.append(
            ContactCandidate(
                full_name=full_name,
                title=canon.get("title"),
                linkedin_url=canon.get("linkedin_url"),
                company_slug=company_slug,
                persona=_coerce_enum(canon.get("persona"), Persona),
                focus_area=_coerce_enum(canon.get("focus_area"), FocusArea),
                email=canon.get("email"),
                snippet=canon.get("about"),
                hook=canon.get("hook"),
                location=canon.get("location") or meta_location,
            )
        )
    return candidates


def validate_contacts_file(
    path: str | Path,
    source: str = "auto",
    *,
    default_company: str | None = None,
) -> dict:
    """Dry-run check of an input file — the contract validator for producers.

    Returns ``{"ok": bool, "count": int, "errors": [...], "warnings": [...]}``
    without writing anything. Used by the Cowork+Chrome producer and by
    ``/network-import --validate`` to confirm a file is well-formed and that
    every contact resolves a company before any LLM work runs.
    """
    path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        raw_rows, meta = _read_rows(path, source)
    except (ContactImportError, json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "count": 0, "errors": [f"parse failed: {exc}"], "warnings": []}

    meta_company = meta.get("company") or meta.get("company_slug") or default_company
    usable = 0
    for i, raw in enumerate(raw_rows):
        canon = _apply_aliases(raw)
        if not canon.get("full_name"):
            errors.append(f"row {i}: missing full_name (or first/last name)")
            continue
        if not (canon.get("company_slug") or canon.get("company") or meta_company):
            errors.append(f"row {i} ({canon['full_name']}): no company — pass --company")
            continue
        if not canon.get("linkedin_url") and not canon.get("email"):
            warnings.append(
                f"row {i} ({canon['full_name']}): no linkedin_url or email — "
                "LinkedIn/email channels can't send"
            )
        if not canon.get("title"):
            warnings.append(f"row {i} ({canon['full_name']}): no title — weaker hook + classify")
        usable += 1

    return {"ok": usable > 0 and not errors, "count": usable,
            "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Import (parse → ingest → optional select/draft)
# ---------------------------------------------------------------------------


def _get_or_create_company(company_slug: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    finally:
        conn.close()
    if row is not None:
        return int(row["id"])
    with with_writer() as conn:
        cursor = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, 'NEW')",
            (company_slug, company_slug.replace("-", " ").title()),
        )
        return int(cursor.lastrowid)


def _max_contact_id(company_id: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM contacts WHERE company_id = ?",
            (company_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["m"])


def _new_ids_since(company_id: int, since_id: int) -> list[int]:
    conn = get_connection()
    try:
        return [
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM contacts WHERE company_id = ? AND id > ? ORDER BY id",
                (company_id, since_id),
            ).fetchall()
        ]
    finally:
        conn.close()


def import_contacts(
    path: str | Path,
    *,
    company: str | None = None,
    location: str | None = None,
    source: str = "auto",
    auto_select: bool = False,
    draft: bool = False,
    anthropic_client=None,
    hunter_provider=None,
) -> dict:
    """Import a leads file end-to-end. Returns a per-company summary.

    Parses *path* → normalizes to canonical ContactCandidates → runs the shared
    ``ingest_contacts`` enrich/classify/hook/save path → optionally marks the
    imported contacts SELECTED and drafts them. Multi-company files are grouped
    so ask-rotation still operates per company. Returns
    ``{slug: {"imported": int, "contact_ids": [...], "drafted": int}}``.
    """
    init_db()
    candidates = parse_contacts_file(
        path, source, default_company=company, default_location=location
    )
    if not candidates:
        raise ContactImportError(
            "No usable contacts found (need a full_name and a resolvable company; "
            "pass --company if the file has no company column)."
        )

    if anthropic_client is None:
        anthropic_client = get_anthropic_client(load_config().anthropic_api_key)

    # Group by company so ingest (and downstream ask-rotation) operate per company.
    groups: dict[str, list[ContactCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.company_slug, []).append(c)

    summary: dict[str, dict] = {}
    for slug, group in groups.items():
        company_id = _get_or_create_company(slug)
        before = _max_contact_id(company_id)
        ingest_contacts(
            group,
            company_id,
            slug,
            anthropic_client=anthropic_client,
            hunter_provider=hunter_provider,
            company_news=None,  # imports have no Serper news pass
        )
        new_ids = _new_ids_since(company_id, before)

        with with_writer() as conn:
            conn.execute("UPDATE companies SET state = 'FOUND' WHERE id = ?", (company_id,))
            if auto_select and new_ids:
                conn.execute(
                    f"UPDATE contacts SET state = 'SELECTED', selected = 1 "
                    f"WHERE id IN ({','.join('?' for _ in new_ids)})",
                    tuple(new_ids),
                )

        drafted = 0
        if draft and auto_select and new_ids:
            from src.agents.drafter import draft_for_contacts

            results = draft_for_contacts(new_ids, anthropic_client=anthropic_client)
            drafted = sum(len(v) for v in results.values())

        summary[slug] = {
            "imported": len(new_ids),
            "contact_ids": new_ids,
            "drafted": drafted,
        }
    return summary
