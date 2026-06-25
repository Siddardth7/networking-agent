"""
src/agents/finder.py
5-phase Finder Agent: Discover → Enrich → Classify → Hook → Save
Traceability: DESIGN.md §4 (Finder phases), §6 (Hook generation), §8.12 (HUNTER_EXHAUSTED path)
"""

from __future__ import annotations

import re

from src.core.config import HAIKU_MODEL, get_anthropic_client, load_config
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate, EmailResult, FocusArea, Persona
from src.providers.apify import ApifyProvider
from src.providers.apollo import ApolloProvider
from src.providers.base import SearchProvider
from src.providers.hunter import HunterProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted
from src.providers.serper import SerperProvider

__all__ = [
    "find_contacts",
    "ingest_contacts",
    "is_acceptable_hook",
    "looks_like_verbatim_news",
]

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
) -> tuple[Persona, FocusArea, str | None]:
    """Persona + focus_area + hook_signal via a single Claude haiku call.

    Returns ``(persona, focus_area, hook_signal)`` where ``hook_signal`` is
    a short specific phrase (≤ 80 chars) extracted from the LinkedIn snippet
    — e.g. "led 787 wing-box stress team", "MS at Georgia Tech in composites".
    ``None`` when no specific signal is extractable. The hook generator
    promotes a non-None signal to Tier 0 so hooks are real, not categories.
    """
    tools = [
        {
            "name": "classify_contact",
            "description": (
                "Classify the contact's persona and technical focus area, "
                "and extract one specific personalization signal from their "
                "LinkedIn snippet if present."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "persona": {
                        "type": "string",
                        "enum": ["RECRUITER", "SENIOR_MANAGER", "PEER_ENGINEER", "ALUMNI"],
                        "description": (
                            "RECRUITER: HR/recruiting. "
                            "SENIOR_MANAGER: Director/VP/Manager. Senior/Staff/Principal "
                            "individual contributors ALSO count as SENIOR_MANAGER for "
                            "outreach purposes (their drafts deserve senior-tone treatment). "
                            "PEER_ENGINEER: junior/mid-level Engineer/Analyst IC. "
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
                            "STRUCTURAL_ANALYSIS: stress/loads/FEA/airframe. "
                            "MANUFACTURING: production/quality/MRB/supplier. "
                            "MATERIALS: metallurgy/alloys. "
                            "ADDITIVE: 3D printing. "
                            "PEER: general engineer. "
                            "ALUMNI_ACADEMIC: academic/PhD."
                        ),
                    },
                    "hook_signal": {
                        "type": "string",
                        "description": (
                            "One short, SPECIFIC, verifiable phrase from the "
                            "snippet that could anchor a personalized outreach "
                            "opener — e.g. 'led 787 empennage stress team', "
                            "'MS at Georgia Tech in composites', 'recent paper on "
                            "bonded composite repair'. Constraints: ≤ 80 characters, "
                            "no fabrication, must be grounded in the snippet text. "
                            "Return an EMPTY STRING if the snippet has no specific "
                            "signal — DO NOT invent one."
                        ),
                    },
                },
                "required": ["persona", "focus_area", "hook_signal"],
            },
        }
    ]

    snippet_block = (
        f'LinkedIn snippet:\n"""{candidate.snippet}"""\n\n'
        if candidate.snippet
        else "LinkedIn snippet: (none available)\n\n"
    )

    response = anthropic_client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=200,
        tools=tools,
        tool_choice={"type": "tool", "name": "classify_contact"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Contact: {candidate.full_name}\n"
                    f"Title: {candidate.title or 'Unknown'}\n"
                    f"Company: {company_slug}\n\n"
                    f"{snippet_block}"
                    "Classify persona and focus area, then extract one specific "
                    "hook_signal from the snippet (or empty string if none)."
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        return Persona.PEER_ENGINEER, FocusArea.PEER, None

    data = tool_block.input
    # Malformed tool output (non-dict input) falls back to safe defaults
    # instead of raising AttributeError mid-pipeline (AUDIT-A20).
    if not isinstance(data, dict):
        return Persona.PEER_ENGINEER, FocusArea.PEER, None
    try:
        persona = Persona(data.get("persona", "PEER_ENGINEER"))
    except ValueError:
        persona = Persona.PEER_ENGINEER
    try:
        focus_area = FocusArea(data.get("focus_area", "PEER"))
    except ValueError:
        focus_area = FocusArea.PEER

    raw_signal = (data.get("hook_signal") or "").strip()
    # Truncate at the 80-char ceiling and drop the empty-signal sentinel.
    hook_signal = raw_signal[:80] if raw_signal else None

    return persona, focus_area, hook_signal


# Verbatim-news detection (AUDIT-A4). The June-6 run pasted raw Serper
# news snippets ("May 15 2026. Joby's Commitment to ... · May 5 2026.
# Joby Reports First Quarter 2026 Financial Results.") directly into two
# contacts' hooks. These patterns identify headline-shaped strings so
# they can never be used as a hook again.
_NEWS_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b"
)
_NEWS_MARKER_RE = re.compile(
    r"(?i)(\bannounc\w+\b|\breports?\b|\bfinancial results\b"
    r"|\bpress release\b|\bquarterly\b|\bseries [a-e] funding\b"
    r"|\bcommitment to\b)"
)

# Maximum length for a hook string. Anything longer reads as pasted
# content, not a conversational anchor.
_MAX_HOOK_LEN = 120


def looks_like_verbatim_news(text: str) -> bool:
    """Return True when *text* is shaped like a pasted news headline.

    Inputs: any candidate hook string. Output: bool. No side effects.
    Three deterministic signals: a dateline ("May 15"), a headline
    separator ("·"), or press-release phrasing ("Reports ... Financial
    Results", "announced", "Series D funding"). Personal signals like
    "led 787 empennage stress team" do not trip any of them.
    """
    if "·" in text:
        return True
    if _NEWS_DATE_RE.search(text):
        return True
    if _NEWS_MARKER_RE.search(text):
        return True
    return False


def is_acceptable_hook(hook: str | None) -> bool:
    """Whitelist gate for hook shapes (AUDIT-A5).

    Inputs: candidate hook string (may be None). Output: True only when
    the hook is usable as a personalization anchor: non-empty, not the
    GENERIC sentinel, single-line, within ``_MAX_HOOK_LEN`` characters,
    and not a verbatim news headline. No side effects.
    """
    if not hook or hook == "GENERIC":
        return False
    if "\n" in hook or len(hook) > _MAX_HOOK_LEN:
        return False
    if looks_like_verbatim_news(hook):
        return False
    return True


def _generate_hook(
    candidate: ContactCandidate,
    hook_signal: str | None = None,
    company_news: str | None = None,
) -> str:
    """Deterministic hook per DESIGN §6, augmented with Layer-1 signals.

    Tier ordering (most-specific first):

    Tier 0 — *hook_signal*: a verbatim signal extracted by the classifier
             from the LinkedIn snippet, accepted only when it passes the
             ``is_acceptable_hook`` whitelist (news-shaped extractions
             are rejected, AUDIT-A4).
    Tier 1 — UIUC alumni signal in title or URL.
    Tier 2 — shared employer (word-boundary match on title).
    Tier 3 — title-keyword specialty bucket.
    Tier 3.5 — title-derived hook ("your work as <title>") so the drafter
             always has a real anchor before GENERIC (AUDIT-A5).
    Tier 4 — ``GENERIC`` sentinel. The drafter / marketer treat this as
             a "no real hook" state.

    *company_news* is deliberately NEVER returned as the hook — it is
    recorded in ``shared_signals`` as phrasing material only (AUDIT-A4).
    """
    del company_news  # never a hook; kept in the signature for callers

    title_lower = (candidate.title or "").lower()
    url_lower = (candidate.linkedin_url or "").lower()

    # Tier 0: explicit hook_signal from the classifier (LinkedIn snippet),
    # gated through the shape whitelist.
    if hook_signal and is_acceptable_hook(hook_signal):
        return hook_signal

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
    if any(
        k in title_lower for k in ["structural", "structures", "stress", "loads", "fea", "airframe"]
    ):
        return "your structures work"
    if any(
        k in title_lower
        for k in ["quality", "mrb", "supplier", "manufacturing engineer", "production"]
    ):
        return "your manufacturing and quality background"
    if any(k in title_lower for k in ["materials", "metallurgy", "alloy", "coating"]):
        return "your materials science background"
    if any(k in title_lower for k in ["additive", "3d print"]):
        return "your additive manufacturing work"

    # Tier 3.5: title-derived hook — always prefer the contact's real
    # title over GENERIC so the drafter has something true to anchor on
    # instead of reaching for filler or a placeholder.
    title = (candidate.title or "").strip()
    if title:
        hook = f"your work as {title}"
        if len(hook) > _MAX_HOOK_LEN:
            hook = hook[: _MAX_HOOK_LEN - 3].rstrip() + "..."
        return hook

    # Tier 4: generic fallback
    return "GENERIC"


def _fetch_company_news_signal(
    company_slug: str,
    serper_provider: SerperProvider,
) -> str | None:
    """Run one Serper news-flavored search per pipeline run.

    Returns a short snippet of the top organic result, or None on quota
    exhaustion / no results / any provider error. Errors are deliberately
    swallowed — Tier 4 (company news) is a *fallback* hook, not a blocking
    step in the pipeline.
    """
    company_name = company_slug.replace("-", " ")
    try:
        snippet = serper_provider.search_general(f"{company_name} news 2026")
    except Exception:
        return None
    # Defensive against mocks / unexpected return types: only proceed for
    # genuine, non-empty strings. Anything else degrades silently to no hook.
    if not isinstance(snippet, str) or not snippet.strip():
        return None
    snippet = snippet.strip()
    if len(snippet) > 120:
        snippet = snippet[:117].rstrip() + "..."
    return snippet


def _company_domain(company_id: int, company_slug: str) -> str:
    """Return the email domain to query Hunter with.

    Inputs: company row id and slug. Output: the stored
    ``companies.domain`` when present, else the slug-derived inference
    (``acme-corp`` → ``acmecorp.com``). Reads the DB; no writes. The
    stored column wins because inference fails silently for companies
    whose web domain differs from their name (AUDIT-A21).
    """
    conn = get_connection()
    try:
        row = conn.execute("SELECT domain FROM companies WHERE id = ?", (company_id,)).fetchone()
    finally:
        conn.close()
    domain = row["domain"] if row is not None else None
    if domain:
        return str(domain)
    return f"{company_slug.replace('-', '')}.com"


def _get_or_create_company(company_slug: str) -> int:
    """Return existing company id or insert a new NEW-state row."""
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


def _discover(
    providers: list[SearchProvider],
    company: str,
    role_keywords: list[str],
    limit: int,
) -> list[ContactCandidate]:
    """Try each discovery provider in order; return the first non-empty result.

    Fallback semantics (input-stack decision 2026-06-25): Apify is primary,
    Serper the fallback. An empty result falls through to the next lane (Google
    may surface profiles a structured people-search missed), so ``[]`` is only
    returned once every lane has *run* and found nothing. A provider that
    *fails* (quota exhausted, auth, network) is skipped so the next lane gets a
    turn. Only when EVERY provider failed do we re-raise — the last
    QuotaExhausted if any (preserves the old hard-stop contract), else the last
    error.
    """
    quota_exc: QuotaExhausted | None = None
    other_exc: Exception | None = None
    ran_clean = False
    for provider in providers:
        try:
            candidates = provider.search_linkedin_profiles(
                company=company, role_keywords=role_keywords, limit=limit
            )
        except QuotaExhausted as exc:
            quota_exc = exc
            continue
        except Exception as exc:  # provider down/misconfigured → try the next lane
            other_exc = exc
            continue
        ran_clean = True
        if candidates:
            return candidates
    if ran_clean:
        return []
    if quota_exc is not None:
        raise quota_exc
    if other_exc is not None:
        raise other_exc
    return []


def _resolve_email(
    candidate: ContactCandidate,
    hunter_provider: HunterProvider | None,
    apollo_provider: ApolloProvider | None,
    company_domain: str,
    state: dict[str, bool],
) -> EmailResult:
    """Resolve one candidate's email: source-supplied → Hunter → Apollo.

    *state* carries per-batch exhaustion flags (``hunter_exhausted`` /
    ``apollo_exhausted``) so an exhausted provider is skipped for the rest of
    the batch instead of re-raising each time. The ``HUNTER_EXHAUSTED`` sentinel
    is preserved for the Hunter-only path (existing contract); it's only emitted
    once Apollo has also failed/absent.
    """
    if candidate.email:
        # Source already supplied an address (e.g. Apollo export) — trust it.
        return EmailResult(email=candidate.email, verified=False, confidence=0, source="IMPORT")
    if hunter_provider is None and apollo_provider is None:
        return EmailResult(email=None, verified=False, confidence=0, source="EMAIL_DISABLED")

    # Primary: Hunter.
    hunter_result: EmailResult | None = None
    if hunter_provider is not None and not state["hunter_exhausted"]:
        try:
            hunter_result = hunter_provider.find_email(
                full_name=candidate.full_name, company_domain=company_domain
            )
        except QuotaExhausted:
            state["hunter_exhausted"] = True
    if hunter_result is not None and hunter_result.email:
        return hunter_result

    # Fallback: Apollo (only when Hunter yielded nothing).
    if apollo_provider is not None and not state["apollo_exhausted"]:
        try:
            apollo_result = apollo_provider.find_email(
                full_name=candidate.full_name, company_domain=company_domain
            )
        except QuotaExhausted:
            state["apollo_exhausted"] = True
        else:
            if apollo_result.email:
                return apollo_result

    # Nothing found — pick the most informative empty sentinel.
    apollo_unavailable = apollo_provider is None or state["apollo_exhausted"]
    if hunter_provider is not None and state["hunter_exhausted"] and apollo_unavailable:
        return EmailResult(email=None, verified=False, confidence=0, source="HUNTER_EXHAUSTED")
    if hunter_result is not None:
        return hunter_result  # Hunter ran, found nothing (source="hunter")
    if apollo_provider is not None:
        return EmailResult(email=None, verified=False, confidence=0, source="apollo")
    return EmailResult(email=None, verified=False, confidence=0, source="EMAIL_DISABLED")


def ingest_contacts(
    candidates: list[ContactCandidate],
    company_id: int,
    company_slug: str,
    *,
    anthropic_client,
    hunter_provider: HunterProvider | None = None,
    apollo_provider: ApolloProvider | None = None,
    company_news: str | None = None,
) -> list[ContactCandidate]:
    """Source-agnostic ingest: enrich → classify → hook → save.

    This is the second half of the Finder, factored out so that ANY input
    source — Serper discovery, an Apollo export, an Apify dump, a Cowork +
    Chrome automation, or a manually compiled file — can flow through the same
    enrich/classify/hook/save path (flexible-input design, 2026-06-21). Each
    *candidate* is a canonical ``ContactCandidate``; fields the source already
    supplied are honored, fields it left blank are generated:

    - ``email``: kept when the candidate already has one (e.g. Apollo); else
      Hunter-enriched when a provider is given; else left empty.
    - ``persona`` / ``focus_area``: kept when BOTH are supplied (skips the Haiku
      classifier call); else classified from title + snippet.
    - ``hook``: kept when supplied; else generated via the deterministic tiers.

    For Serper-discovered candidates (no persona/email/hook pre-set) this is
    byte-for-byte the previous behavior. Writes one ``contacts`` row per
    candidate; does NOT transition company state (the caller owns that).
    """
    company_domain = _company_domain(company_id, company_slug)
    # Per-batch exhaustion flags shared across candidates: once a provider hits
    # its monthly cap we skip it for the rest of the batch (Hunter → Apollo).
    email_state = {"hunter_exhausted": False, "apollo_exhausted": False}
    enriched: list[tuple[ContactCandidate, EmailResult]] = []

    for candidate in candidates:
        email_result = _resolve_email(
            candidate, hunter_provider, apollo_provider, company_domain, email_state
        )
        enriched.append((candidate, email_result))

    results: list[ContactCandidate] = []
    for candidate, email_result in enriched:
        # Persona/focus precedence: an explicit label wins; `alumni_confirmed`
        # (Alumni-tool ground truth) forces ALUMNI; otherwise the classifier
        # decides. The classifier still runs to fill an unknown focus_area, but
        # a forced persona is never overridden by its guess.
        forced_persona = candidate.persona or (
            Persona.ALUMNI if candidate.alumni_confirmed else None
        )
        if forced_persona is not None and candidate.focus_area is not None:
            persona, focus_area, hook_signal = forced_persona, candidate.focus_area, None
        else:
            cls_persona, cls_focus, hook_signal = _classify_contact(
                candidate, company_slug, anthropic_client
            )
            persona = forced_persona or cls_persona
            focus_area = candidate.focus_area or cls_focus

        # Honor a source-supplied hook; otherwise generate one.
        hook = candidate.hook if candidate.hook else _generate_hook(
            candidate,
            hook_signal=hook_signal,
            company_news=company_news,
        )

        enriched_candidate = ContactCandidate(
            full_name=candidate.full_name,
            title=candidate.title,
            linkedin_url=candidate.linkedin_url,
            company_slug=company_slug,
            persona=persona,
            focus_area=focus_area,
            email=email_result.email,
            snippet=candidate.snippet,
            hook=hook,
            location=candidate.location,
            school=candidate.school,
            alumni_confirmed=candidate.alumni_confirmed,
            connection_degree=candidate.connection_degree,
        )
        results.append(enriched_candidate)

        signal_parts: list[str] = []
        if candidate.snippet:
            signal_parts.append(f"profile: {candidate.snippet[:140]}")
        if company_news:
            signal_parts.append(f"company_news: {company_news[:140]}")
        # Producer signals (Cowork+Chrome) surfaced for the reviewer.
        if candidate.alumni_confirmed:
            signal_parts.append("alumni_confirmed: true")
        if candidate.school:
            signal_parts.append(f"school: {candidate.school[:60]}")
        if candidate.connection_degree:
            signal_parts.append(f"degree: {candidate.connection_degree[:10]}")
        shared_signals = " | ".join(signal_parts) or None

        with with_writer() as conn:
            conn.execute(
                """
                INSERT INTO contacts
                    (company_id, full_name, title, persona, focus_area,
                     linkedin_url, email, email_verified, source_provider,
                     hook, shared_signals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    shared_signals,
                ),
            )

    return results


def find_contacts(
    company_slug: str,
    limit: int = 5,
    serper_provider: SerperProvider | None = None,
    hunter_provider: HunterProvider | None = None,
    apify_provider: ApifyProvider | None = None,
    apollo_provider: ApolloProvider | None = None,
    anthropic_client=None,
) -> list[ContactCandidate]:
    """Run the 5-phase Finder pipeline for *company_slug*.

    Returns the list of enriched ContactCandidate objects written to DB.
    Raises QuotaExhausted (from Serper) when the search quota is exhausted.
    On Hunter quota exhaustion, marks remaining contacts HUNTER_EXHAUSTED and continues.

    Email enrichment is opt-in (v0.2.1): when
    ``pipeline.enable_email_enrichment`` is false (the default) and no
    *hunter_provider* is injected, the Hunter phase is skipped entirely —
    no key required, zero Hunter quota spent — and contacts are stored
    with ``source_provider='EMAIL_DISABLED'``. An explicitly injected
    *hunter_provider* always wins over the toggle.
    """
    init_db()
    cfg = load_config()

    # Discovery providers: Apify (primary) → Serper (fallback). Build whichever
    # keys are present; at least one is required. An injected serper_provider
    # (tests) is honored as-is. Serper also powers the Tier-4 company-news hook.
    if serper_provider is None and cfg.serper_api_key:
        serper_provider = SerperProvider(
            api_key=cfg.serper_api_key,
            quota_manager=QuotaManager(),
            cache_ttl_days=cfg.search_cache_ttl_days,
        )
    if apify_provider is None and cfg.apify_api_key:
        apify_provider = ApifyProvider(api_key=cfg.apify_api_key, quota_manager=QuotaManager())

    search_chain = [p for p in (apify_provider, serper_provider) if p is not None]
    if not search_chain:
        raise ValueError("No discovery provider configured (set APIFY_API_KEY or SERPER_API_KEY)")

    if hunter_provider is None and cfg.enable_email_enrichment:
        if not cfg.hunter_api_key:
            raise ValueError("HUNTER_API_KEY not configured")
        hunter_provider = HunterProvider(api_key=cfg.hunter_api_key, quota_manager=QuotaManager())
    # Apollo email fallback: only when enrichment is on and a key exists. Hunter
    # stays primary; Apollo fills the gaps it misses (input-stack decision).
    if apollo_provider is None and cfg.enable_email_enrichment and cfg.apollo_api_key:
        apollo_provider = ApolloProvider(api_key=cfg.apollo_api_key, quota_manager=QuotaManager())

    if anthropic_client is None:
        anthropic_client = get_anthropic_client(cfg.anthropic_api_key)

    company_id = _get_or_create_company(company_slug)

    # Idempotency: clear partial contacts from any previous failed run (DESIGN §8.11)
    with with_writer() as conn:
        conn.execute(
            "DELETE FROM contacts WHERE company_id = ? AND state = 'NEW'",
            (company_id,),
        )

    # Phase 1: Discover — Apify primary, Serper fallback. QuotaExhausted only
    # propagates when EVERY lane is exhausted; company stays NEW in that case.
    candidates = _discover(
        search_chain,
        company=company_slug.replace("-", " "),
        role_keywords=_ROLE_KEYWORDS,
        limit=limit,
    )

    if not candidates:
        # Both automated lanes (Apify → Serper) came up empty. The FINAL fallback
        # is manual: source profiles by hand and feed them through
        # `/network-import` (importer.py) — same enrich/classify/hook/draft path.
        # A dedicated producer feature is postponed (input-stack decision).
        with with_writer() as conn:
            conn.execute("UPDATE companies SET state = 'FOUND' WHERE id = ?", (company_id,))
        return []

    # Phase 1.5: One company-news search per run, shared across all contacts
    # as a Tier-4 fallback hook. Needs Serper; skipped when only Apify is wired.
    # Errors are swallowed inside the helper — this signal is a nice-to-have.
    company_news = (
        _fetch_company_news_signal(company_slug, serper_provider)
        if serper_provider is not None
        else None
    )

    # Phases 2–5: Enrich → Classify → Hook → Save — source-agnostic, shared with
    # every input path (Apify / Apollo / Serper / Cowork+Chrome / manual).
    results = ingest_contacts(
        candidates,
        company_id,
        company_slug,
        anthropic_client=anthropic_client,
        hunter_provider=hunter_provider,
        apollo_provider=apollo_provider,
        company_news=company_news,
    )

    # Transition company NEW → FOUND
    with with_writer() as conn:
        conn.execute("UPDATE companies SET state = 'FOUND' WHERE id = ?", (company_id,))

    return results
