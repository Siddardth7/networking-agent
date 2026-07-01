"""
src/agents/finder.py
5-phase Finder Agent: Discover → Enrich → Classify → Hook → Save
Traceability: DESIGN.md §4 (Finder phases), §6 (Hook generation), §8.12 (HUNTER_EXHAUSTED path)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from src.agents.ranker import rank_contact
from src.core.config import HAIKU_MODEL, get_anthropic_client, load_config
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate, EmailResult, FocusArea, Persona
from src.core.slug import canonical_linkedin_url
from src.providers.apify import ApifyProvider
from src.providers.apollo import ApolloProvider
from src.providers.base import SearchProvider
from src.providers.hunter import HunterProvider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted
from src.providers.serper import SerperProvider

_LOG = logging.getLogger("networking_agent.finder")

__all__ = [
    "apply_classification",
    "build_classify_context",
    "build_discovery_chain",
    "build_email_providers",
    "find_contacts",
    "ingest_contacts",
    "is_acceptable_hook",
    "looks_like_verbatim_news",
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

# The classifier is asked for a hook signal of at most this many characters.
_MAX_HOOK_SIGNAL_LEN = 80


def _trim_hook_signal(text: str) -> str:
    """Trim a hook signal to ``_MAX_HOOK_SIGNAL_LEN`` chars on a word boundary.

    The classifier is told to stay within the ceiling but sometimes overshoots;
    a hard slice cut mid-word ("…large assembly stru" — Finder trial #10 residual).
    Back up to the last space so the anchor reads as a clean phrase. A single
    over-long token (no space) keeps the hard cut. No side effects.
    """
    if len(text) <= _MAX_HOOK_SIGNAL_LEN:
        return text
    cut = text[:_MAX_HOOK_SIGNAL_LEN].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut


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
                            "PEER: a generalist engineer with NO clear single "
                            "specialty — use this (do NOT guess a specialty) when "
                            "the title/snippet doesn't clearly point to one above. "
                            "ALUMNI_ACADEMIC: academic/PhD/research/student."
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
    return apply_classification(
        data.get("persona"), data.get("focus_area"), data.get("hook_signal")
    )


# Persona / focus enum semantics for the host-token `build_classify_context`
# (#50) — a compact echo of the API classify tool schema's descriptions above.
# (Kept separate, not refactored into the tool schema, because that schema's
# exact prompt wording is accuracy-validated at persona 100% / focus 100% (#5)
# and not worth perturbing; if either drifts, reconcile them here.)
_PERSONA_OPTIONS: dict[str, str] = {
    "RECRUITER": "HR / recruiting",
    "SENIOR_MANAGER": "Director/VP/Manager — AND Senior/Staff/Principal ICs (senior tone)",
    "PEER_ENGINEER": "junior / mid-level Engineer / Analyst IC",
    "ALUMNI": "Student / PhD / Research / University",
}
_FOCUS_OPTIONS: dict[str, str] = {
    "COMPOSITE_DESIGN": "composites / carbon fiber",
    "STRUCTURAL_ANALYSIS": "stress / loads / FEA / airframe",
    "MANUFACTURING": "production / quality / MRB / supplier",
    "MATERIALS": "metallurgy / alloys",
    "ADDITIVE": "3D printing",
    "PEER": "generalist engineer, NO clear specialty — use this, do NOT guess one",
    "ALUMNI_ACADEMIC": "academic / PhD / research / student",
}


def apply_classification(
    raw_persona: str | None,
    raw_focus: str | None,
    raw_hook_signal: str | None,
) -> tuple[Persona, FocusArea, str | None]:
    """Deterministic post-processing of a raw classification. Pure, no LLM.

    Shared by the API tool-call path (`_classify_contact`) and the host-token
    path (#50): enum-coerce with safe defaults, enforce the non-engineer focus
    convention (ALUMNI→ALUMNI_ACADEMIC, RECRUITER→PEER; issue #5 / FINDER_AUDIT
    D3 — done in code because the model ignored the prompt rule on strong topic
    signals), and trim the hook signal to the ceiling (dropping an empty one).
    """
    try:
        persona = Persona(raw_persona or "PEER_ENGINEER")
    except ValueError:
        persona = Persona.PEER_ENGINEER
    try:
        focus_area = FocusArea(raw_focus or "PEER")
    except ValueError:
        focus_area = FocusArea.PEER

    if persona is Persona.ALUMNI:
        focus_area = FocusArea.ALUMNI_ACADEMIC
    elif persona is Persona.RECRUITER:
        focus_area = FocusArea.PEER

    raw_signal = (raw_hook_signal or "").strip()
    hook_signal = _trim_hook_signal(raw_signal) if raw_signal else None
    return persona, focus_area, hook_signal


def build_classify_context(candidate: ContactCandidate, company_slug: str) -> dict:
    """Structured grounding for host-model classification of one candidate. No LLM.

    The host model (or the `networking-classifier` subagent) returns
    ``{persona, focus_area, hook_signal}``, which `apply_classification` then
    canonicalizes — so the host path lands on exactly the same labels as the API
    path.
    """
    return {
        "full_name": candidate.full_name,
        "title": candidate.title or "Unknown",
        "company": company_slug,
        "snippet": candidate.snippet or "",
        "persona_options": _PERSONA_OPTIONS,
        "focus_options": _FOCUS_OPTIONS,
        "instruction": (
            "Classify persona + focus_area from the title and snippet, then "
            "extract ONE specific hook_signal (≤ 80 chars) from the snippet — a "
            "concrete detail like 'led 787 wing-box stress team' — or empty if "
            "none. Return {persona, focus_area, hook_signal}."
        ),
    }


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
    and not shaped like a pasted news headline (a "·" separator or two
    co-occurring news markers — D6). No side effects.
    """
    if not hook or hook == "GENERIC":
        return False
    if "\n" in hook or len(hook) > _MAX_HOOK_LEN:
        return False
    # D6: the strict verbatim-news check (one marker trips it) was built for
    # raw Serper snippets but false-positives on real personal signals
    # ("reports to VP Structures", "led 787 stress team since May 5"). An LLM
    # hook_signal is already ≤80 chars and grounded, so only reject the
    # unambiguous pasted-headline shapes: a "·" separator, or two co-occurring
    # news markers (a single one is normal phrasing, not a headline).
    if "·" in hook:
        return False
    marker_hits = len(_NEWS_MARKER_RE.findall(hook)) + bool(_NEWS_DATE_RE.search(hook))
    if marker_hits >= 2:
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

    # Tier 2: Shared employer — word-boundary match on the title OR the company
    # slug. D11: a current employee titled "Structures Engineer" (no employer in
    # the title) still trips it via slug=boeing. Slug dashes → spaces so a
    # multi-word employer ("general-electric") matches.
    slug_words = (candidate.company_slug or "").lower().replace("-", " ")
    for emp in _SHARED_EMPLOYERS:
        pat = r"\b" + re.escape(emp) + r"\b"
        if re.search(pat, title_lower) or re.search(pat, slug_words):
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
        # D12: current year, not a hardcoded one that silently goes stale.
        snippet = serper_provider.search_general(f"{company_name} news {datetime.now().year}")
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
    # D9: inference is a guess ("general-electric" → "generalelectric.com", but
    # the real domain is "ge.com"). When it's wrong EVERY email in the batch
    # fails — previously with no trace. Warn so the cause is visible; the fix is
    # to store companies.domain. ponytail: add a --domain override the day a
    # find CLI exists to pass it (today the orchestrator is the only caller).
    inferred = f"{company_slug.replace('-', '')}.com"
    _LOG.warning(
        "no stored domain for '%s'; inferring '%s' — email lookups will fail if "
        "this is wrong (set companies.domain to override)",
        company_slug,
        inferred,
    )
    return inferred


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


def _dedup_key(candidate: ContactCandidate) -> str:
    """Stable identity for cross-provider dedup: canonical LinkedIn URL, else name.

    Shares ``canonical_linkedin_url`` with the importer (#24) so the same person
    arriving from two providers collapses regardless of scheme/www/query.
    """
    return canonical_linkedin_url(candidate.linkedin_url) or (
        candidate.full_name or ""
    ).strip().lower()


def _discover(
    providers: list[SearchProvider],
    company: str,
    role_keywords: list[str],
    limit: int,
    location: str | None = None,
) -> list[ContactCandidate]:
    """Best-effort accumulation to *limit* across providers, in order, deduped.

    Apify (primary) → Serper (fallback): each provider is asked only for the
    shortfall (``limit - collected``), results are deduped by LinkedIn URL (else
    name), and accumulation stops once *limit* is reached (so a provider that
    already fills the quota leaves the next lane untouched). A provider that
    *fails* (quota, auth, network) is logged and skipped so the next lane gets a
    turn; a provider that returns too few falls through to top up.

    No silent caps (ROADMAP A3, FINDER_AUDIT D1): every provider failure and any
    final shortfall is logged at WARNING — a bad key no longer looks like "no
    contacts exist." Only when EVERY provider failed do we re-raise: the last
    QuotaExhausted if any (preserves the hard-stop contract), else the last error.
    """
    collected: list[ContactCandidate] = []
    seen: set[str] = set()
    quota_exc: QuotaExhausted | None = None
    other_exc: Exception | None = None
    ran_any = False
    for provider in providers:
        if len(collected) >= limit:
            break
        name = type(provider).__name__
        try:
            candidates = provider.search_linkedin_profiles(
                company=company,
                role_keywords=role_keywords,
                limit=limit - len(collected),
                location=location,
            )
        except QuotaExhausted as exc:
            quota_exc = exc
            _LOG.warning("discovery: %s quota exhausted — trying next lane", name)
            continue
        except Exception as exc:  # provider down/misconfigured → try the next lane
            other_exc = exc
            _LOG.warning("discovery: %s failed (%s) — trying next lane", name, exc)
            continue
        ran_any = True
        added = 0
        for cand in candidates:
            key = _dedup_key(cand)
            if not key or key in seen:
                continue
            seen.add(key)
            collected.append(cand)
            added += 1
            if len(collected) >= limit:
                break
        _LOG.info("discovery: %s added %d (%d/%d)", name, added, len(collected), limit)

    if collected:
        if len(collected) < limit:
            _LOG.warning(
                "discovery: best-effort %d/%d — providers exhausted", len(collected), limit
            )
        return collected

    # Nothing collected at all.
    if not ran_any:
        if quota_exc is not None:
            raise quota_exc
        if other_exc is not None:
            raise other_exc
    elif other_exc is not None:
        # A provider errored but a later lane ran clean-and-empty (D1): surface it
        # via the log instead of silently returning [].
        _LOG.warning("discovery: 0 contacts and a provider errored: %s", other_exc)
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
    apollo_exhausted = apollo_provider is not None and state["apollo_exhausted"]
    apollo_unavailable = apollo_provider is None or apollo_exhausted
    if hunter_provider is not None and state["hunter_exhausted"] and apollo_unavailable:
        return EmailResult(email=None, verified=False, confidence=0, source="HUNTER_EXHAUSTED")
    if hunter_result is not None:
        return hunter_result  # Hunter ran, found nothing (source="hunter")
    # D10: Apollo hit its cap without running for this candidate — don't label it
    # "apollo" as if it searched and came up empty.
    if apollo_exhausted:
        return EmailResult(email=None, verified=False, confidence=0, source="APOLLO_EXHAUSTED")
    if apollo_provider is not None:
        return EmailResult(email=None, verified=False, confidence=0, source="apollo")
    # Defensive: unreachable — the both-providers-None case already returned
    # EMAIL_DISABLED above (line 528), so reaching here would mean a present
    # provider produced neither a result nor an exhaustion flag.
    return EmailResult(  # pragma: no cover
        email=None, verified=False, confidence=0, source="EMAIL_DISABLED"
    )


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
            persona, focus_area = forced_persona, candidate.focus_area
            # D7: persona/focus are settled, but a rich snippet still holds a
            # Tier-0 hook. When no explicit hook was supplied, run the
            # classifier purely to mine its hook_signal (its persona/focus
            # guesses are discarded — the forced labels win).
            if candidate.snippet and not candidate.hook:
                _, _, hook_signal = _classify_contact(
                    candidate, company_slug, anthropic_client
                )
            else:
                hook_signal = None
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

        # Referral-likelihood ranking (#11): deterministic, explainable. Score the
        # enriched candidate, log the per-signal breakdown (acceptance: "per-signal
        # contributions logged"), and persist score + reasons for the gate to order on.
        rank = rank_contact(enriched_candidate)
        _LOG.info(
            "rank %s = %d (%s)", candidate.full_name, rank.total, rank.summary()
        )

        with with_writer() as conn:
            # D5: OR IGNORE + the partial unique index (migration 005) skip a
            # re-insert of an already-saved (company_id, linkedin_url) on re-run.
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO contacts
                    (company_id, full_name, title, persona, focus_area,
                     linkedin_url, email, email_verified, source_provider,
                     hook, shared_signals, rank_score, rank_reasons, location)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    rank.total,
                    rank.summary(),
                    candidate.location,  # #18: persisted for timing intelligence
                ),
            )
            inserted = cursor.rowcount > 0

        # Only report contacts that were actually written (a dup on re-run is
        # ignored, so it isn't a "result" of this run).
        if inserted:
            results.append(enriched_candidate)

    return results


def build_discovery_chain(
    cfg,
    serper_provider: SerperProvider | None = None,
    apify_provider: ApifyProvider | None = None,
) -> tuple[list[SearchProvider], SerperProvider | None]:
    """Apify (primary) → Serper (fallback) discovery chain from config keys.

    Returns ``(chain, serper_provider)``; the serper reference is handed back
    separately because find_contacts also uses it for the Tier-4 company-news
    search. Injected providers (tests) win over key-built ones. Raises
    ``ValueError`` when no discovery key is configured. Shared by find_contacts
    and the host-token ``discover`` verb (#50) so both build the chain the same way.
    """
    if serper_provider is None and cfg.serper_api_key:
        serper_provider = SerperProvider(
            api_key=cfg.serper_api_key,
            quota_manager=QuotaManager(),
            cache_ttl_days=cfg.search_cache_ttl_days,
        )
    if apify_provider is None and cfg.apify_api_key:
        apify_provider = ApifyProvider(api_key=cfg.apify_api_key, quota_manager=QuotaManager())

    chain = [p for p in (apify_provider, serper_provider) if p is not None]
    if not chain:
        raise ValueError("No discovery provider configured (set APIFY_API_KEY or SERPER_API_KEY)")
    return chain, serper_provider


def build_email_providers(
    cfg,
    hunter_provider: HunterProvider | None = None,
    apollo_provider: ApolloProvider | None = None,
) -> tuple[HunterProvider | None, ApolloProvider | None]:
    """Hunter (primary) + Apollo (fallback) email providers, gated on config.

    Mirrors find_contacts: enrichment must be enabled. Raises ``ValueError``
    when enabled but ``HUNTER_API_KEY`` is missing (existing contract). Both
    None means email is disabled (contacts saved as ``EMAIL_DISABLED``). Shared
    by find_contacts and the host-token ``ingest`` verb (#50).
    """
    if hunter_provider is None and cfg.enable_email_enrichment:
        if not cfg.hunter_api_key:
            raise ValueError("HUNTER_API_KEY not configured")
        hunter_provider = HunterProvider(api_key=cfg.hunter_api_key, quota_manager=QuotaManager())
    if apollo_provider is None and cfg.enable_email_enrichment and cfg.apollo_api_key:
        apollo_provider = ApolloProvider(api_key=cfg.apollo_api_key, quota_manager=QuotaManager())
    return hunter_provider, apollo_provider


def find_contacts(
    company_slug: str,
    limit: int = 5,
    serper_provider: SerperProvider | None = None,
    hunter_provider: HunterProvider | None = None,
    apify_provider: ApifyProvider | None = None,
    apollo_provider: ApolloProvider | None = None,
    anthropic_client=None,
    location: str | None = None,
    role_keywords: list[str] | None = None,
) -> list[ContactCandidate]:
    """Run the 5-phase Finder pipeline for *company_slug*.

    Returns the list of enriched ContactCandidate objects written to DB.
    Raises QuotaExhausted (from Serper) when the search quota is exhausted.
    On Hunter quota exhaustion, marks remaining contacts HUNTER_EXHAUSTED and continues.

    *role_keywords* overrides the config-global ``finder_role_keywords`` for this
    call — Application mode (#59) passes a posting's free-form ``target_keywords``
    to bias discovery toward the role's team. When None, the config default is
    used (Campaign mode's behavior, unchanged).

    Email enrichment is opt-in (v0.2.1): when
    ``pipeline.enable_email_enrichment`` is false (the default) and no
    *hunter_provider* is injected, the Hunter phase is skipped entirely —
    no key required, zero Hunter quota spent — and contacts are stored
    with ``source_provider='EMAIL_DISABLED'``. An explicitly injected
    *hunter_provider* always wins over the toggle.
    """
    init_db()
    cfg = load_config()

    # Discovery providers: Apify (primary) → Serper (fallback); at least one
    # key required. Serper also powers the Tier-4 company-news hook, so it's
    # handed back. Email providers (Hunter → Apollo) are gated on enrichment.
    search_chain, serper_provider = build_discovery_chain(cfg, serper_provider, apify_provider)
    hunter_provider, apollo_provider = build_email_providers(cfg, hunter_provider, apollo_provider)

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
        role_keywords=role_keywords or cfg.finder_role_keywords,
        limit=limit,
        location=location,
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
