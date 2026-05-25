"""
src/agents/finder.py
5-phase Finder Agent: Discover → Enrich → Classify → Hook → Save
Traceability: DESIGN.md §4 (Finder phases), §6 (Hook generation), §8.12 (HUNTER_EXHAUSTED path)
"""

from __future__ import annotations

from typing import Optional

from src.core.config import HAIKU_MODEL, get_anthropic_client, load_config
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate, EmailResult, FocusArea, Persona
from src.providers.hunter import HunterProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted
from src.providers.serper import SerperProvider

__all__ = ["find_contacts"]

_ROLE_KEYWORDS = [
    "quality engineer",
    "supplier quality",
    "MRB engineer",
    "manufacturing engineer",
    "stress engineer",
    "structures engineer",
    "composites engineer",
    "materials engineer",
    "additive manufacturing",
]

_SHARED_EMPLOYERS = [
    "tata",
    "ge",
    "general electric",
    "boeing",
    "lockheed",
    "airbus",
    "honeywell",
]

_UIUC_SIGNALS = ["uiuc", "university of illinois", "urbana-champaign"]


def _classify_contact(
    candidate: ContactCandidate,
    company_slug: str,
    anthropic_client,
) -> tuple[Persona, FocusArea]:
    """Combined persona + focus_area classification via single Claude haiku call."""
    tools = [
        {
            "name": "classify_contact",
            "description": "Classify the contact's persona and technical focus area",
            "input_schema": {
                "type": "object",
                "properties": {
                    "persona": {
                        "type": "string",
                        "enum": ["RECRUITER", "SENIOR_MANAGER", "PEER_ENGINEER", "ALUMNI"],
                        "description": (
                            "RECRUITER: HR/recruiting. "
                            "SENIOR_MANAGER: Director/VP/Manager. "
                            "PEER_ENGINEER: Engineer/Analyst IC. "
                            "ALUMNI: Student/PhD/Research/University."
                        ),
                    },
                    "focus_area": {
                        "type": "string",
                        "enum": [
                            "COMPOSITE_DESIGN",
                            "STRUCTURAL_ANALYSIS",
                            "MANUFACTURING",
                            "MATERIALS",
                            "ADDITIVE",
                            "PEER",
                            "ALUMNI_ACADEMIC",
                        ],
                        "description": (
                            "COMPOSITE_DESIGN: composites/carbon fiber. "
                            "STRUCTURAL_ANALYSIS: stress/loads/FEA. "
                            "MANUFACTURING: production/quality/MRB/supplier. "
                            "MATERIALS: metallurgy/alloys. "
                            "ADDITIVE: 3D printing. "
                            "PEER: general engineer. "
                            "ALUMNI_ACADEMIC: academic/PhD."
                        ),
                    },
                },
                "required": ["persona", "focus_area"],
            },
        }
    ]

    response = anthropic_client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=100,
        tools=tools,
        tool_choice={"type": "tool", "name": "classify_contact"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Contact: {candidate.full_name}\n"
                    f"Title: {candidate.title or 'Unknown'}\n"
                    f"Company: {company_slug}\n"
                    "Classify persona and focus area."
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        return Persona.PEER_ENGINEER, FocusArea.PEER

    data = tool_block.input
    try:
        persona = Persona(data.get("persona", "PEER_ENGINEER"))
    except ValueError:
        persona = Persona.PEER_ENGINEER
    try:
        focus_area = FocusArea(data.get("focus_area", "PEER"))
    except ValueError:
        focus_area = FocusArea.PEER

    return persona, focus_area


def _generate_hook(candidate: ContactCandidate) -> str:
    """5-tier deterministic hook per DESIGN §6."""
    import re

    title_lower = (candidate.title or "").lower()
    url_lower = (candidate.linkedin_url or "").lower()

    # Tier 1: UIUC alumni signal
    for sig in _UIUC_SIGNALS:
        if sig in title_lower or sig in url_lower:
            return "we share a UIUC background"

    # Tier 2: Shared employer — word-boundary match to avoid substring false positives
    for emp in _SHARED_EMPLOYERS:
        if re.search(r"\b" + re.escape(emp) + r"\b", title_lower):
            label = emp.upper() if emp == "ge" else emp.title()
            return f"you also spent time at {label}"

    # Tier 3: Title specialty
    if any(k in title_lower for k in ["composite", "carbon fiber", "fiber reinforced"]):
        return "your composites work"
    if any(k in title_lower for k in ["structural", "structures", "stress", "loads", "fea", "airframe"]):
        return "your structures work"
    if any(k in title_lower for k in ["quality", "mrb", "supplier", "manufacturing engineer", "production"]):
        return "your manufacturing and quality background"
    if any(k in title_lower for k in ["materials", "metallurgy", "alloy", "coating"]):
        return "your materials science background"
    if any(k in title_lower for k in ["additive", "3d print"]):
        return "your additive manufacturing work"

    # Tier 4: company news signals (deferred to v0.2)

    # Tier 5: generic fallback
    return "GENERIC"


def _get_or_create_company(company_slug: str) -> int:
    """Return existing company id or insert a new NEW-state row."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM companies WHERE slug = ?", (company_slug,)
        ).fetchone()
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


def find_contacts(
    company_slug: str,
    limit: int = 5,
    serper_provider: Optional[SerperProvider] = None,
    hunter_provider: Optional[HunterProvider] = None,
    anthropic_client=None,
) -> list[ContactCandidate]:
    """Run the 5-phase Finder pipeline for *company_slug*.

    Returns the list of enriched ContactCandidate objects written to DB.
    Raises QuotaExhausted (from Serper) when the search quota is exhausted.
    On Hunter quota exhaustion, marks remaining contacts HUNTER_EXHAUSTED and continues.
    """
    init_db()
    cfg = load_config()

    if serper_provider is None:
        if not cfg.serper_api_key:
            raise ValueError("SERPER_API_KEY not configured")
        serper_provider = SerperProvider(
            api_key=cfg.serper_api_key, quota_manager=QuotaManager()
        )

    if hunter_provider is None:
        if not cfg.hunter_api_key:
            raise ValueError("HUNTER_API_KEY not configured")
        hunter_provider = HunterProvider(
            api_key=cfg.hunter_api_key, quota_manager=QuotaManager()
        )

    if anthropic_client is None:
        anthropic_client = get_anthropic_client(cfg.anthropic_api_key)

    company_id = _get_or_create_company(company_slug)

    # Idempotency: clear partial contacts from any previous failed run (DESIGN §8.11)
    with with_writer() as conn:
        conn.execute(
            "DELETE FROM contacts WHERE company_id = ? AND state = 'NEW'",
            (company_id,),
        )

    # Phase 1: Discover — QuotaExhausted propagates; company stays NEW
    candidates = serper_provider.search_linkedin_profiles(
        company=company_slug.replace("-", " "),
        role_keywords=_ROLE_KEYWORDS,
        limit=limit,
    )

    if not candidates:
        with with_writer() as conn:
            conn.execute(
                "UPDATE companies SET state = 'FOUND' WHERE id = ?", (company_id,)
            )
        return []

    # Phase 2: Enrich — on Hunter QuotaExhausted mark remaining contacts HUNTER_EXHAUSTED
    company_domain = f"{company_slug.replace('-', '')}.com"
    hunter_exhausted = False
    enriched: list[tuple[ContactCandidate, EmailResult]] = []

    for candidate in candidates:
        if hunter_exhausted:
            email_result = EmailResult(
                email=None, verified=False, confidence=0, source="HUNTER_EXHAUSTED"
            )
        else:
            try:
                email_result = hunter_provider.find_email(
                    full_name=candidate.full_name, company_domain=company_domain
                )
            except QuotaExhausted:
                hunter_exhausted = True
                email_result = EmailResult(
                    email=None, verified=False, confidence=0, source="HUNTER_EXHAUSTED"
                )
        enriched.append((candidate, email_result))

    # Phases 3 + 4 + 5: Classify → Hook → Save per contact
    results: list[ContactCandidate] = []

    for candidate, email_result in enriched:
        persona, focus_area = _classify_contact(candidate, company_slug, anthropic_client)
        hook = _generate_hook(candidate)

        enriched_candidate = ContactCandidate(
            full_name=candidate.full_name,
            title=candidate.title,
            linkedin_url=candidate.linkedin_url,
            company_slug=company_slug,
            persona=persona,
            focus_area=focus_area,
            email=email_result.email,
        )
        results.append(enriched_candidate)

        with with_writer() as conn:
            conn.execute(
                """
                INSERT INTO contacts
                    (company_id, full_name, title, persona, focus_area,
                     linkedin_url, email, email_verified, source_provider, hook)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    candidate.full_name,
                    candidate.title,
                    persona.value,
                    focus_area.value,
                    candidate.linkedin_url,
                    email_result.email,
                    int(email_result.verified),
                    email_result.source,
                    hook,
                ),
            )

    # Transition company NEW → FOUND
    with with_writer() as conn:
        conn.execute(
            "UPDATE companies SET state = 'FOUND' WHERE id = ?", (company_id,)
        )

    return results
