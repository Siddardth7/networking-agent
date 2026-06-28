"""
src/providers/apify.py
Apify LinkedIn profile-search provider — PRIMARY discovery source.
Backed by the harvestapi/linkedin-profile-search Actor (no LinkedIn cookies).
Traceability: DESIGN.md §4 (Provider Layer); input-stack decision 2026-06-25.

Single API key, no rotation (Sid's call 2026-06-25): the LinkedIn send cap
(~20/day) bounds demand below one free account's monthly credit, so multi-account
rotation is deferred. Billed per 25-profile search page; a per-month call cap in
the QuotaManager ("apify") is the coarse $-budget guard.
"""

from __future__ import annotations

import re

import httpx

from src.core.schemas import ContactCandidate
from src.providers.base import SearchProvider, register_provider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import with_retry

__all__ = ["ApifyProvider"]

# Tilde form of the Actor id for the REST path (username~actor-name).
_APIFY_ACTOR = "harvestapi~linkedin-profile-search"
_APIFY_ENDPOINT = f"https://api.apify.com/v2/acts/{_APIFY_ACTOR}/run-sync-get-dataset-items"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "unknown"


def _first(value):
    """First element of a non-empty list, else the value itself.

    Full mode nests current job under ``currentPosition`` (object); Short mode
    under ``currentPositions`` (array). This collapses both to a dict.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _nested_str(item: dict, *path: str) -> str | None:
    """Walk *path* through nested dicts/arrays, returning a clean string leaf."""
    cur: object = item
    for seg in path:
        cur = _first(cur)
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    cur = _first(cur)
    return cur.strip() if isinstance(cur, str) and cur.strip() else None


def _pos_field(item: dict, key: str) -> str | None:
    """Read *key* from currentPosition (Full) or currentPositions[0] (Short)."""
    return _nested_str(item, "currentPosition", key) or _nested_str(item, "currentPositions", key)


def _parse_item(item: dict, company_slug: str) -> ContactCandidate | None:
    """Map one Apify profile object to a canonical ContactCandidate.

    Returns ``None`` for an unusable record (no name). Nested company/location
    are flattened here so the canonical record is flat — the same gap the
    importer's ``_lift_apify_nested`` closes for the file-import path.
    """
    first = (item.get("firstName") or "").strip()
    last = (item.get("lastName") or "").strip()
    full_name = (item.get("fullName") or f"{first} {last}").strip()
    if not full_name:
        return None

    # title: headline (Full) wins; else the current job title (Short has no headline).
    title = (item.get("headline") or "").strip() or _pos_field(item, "title") or _pos_field(
        item, "position"
    )
    url = (item.get("linkedinUrl") or item.get("profileUrl") or "").strip() or None
    about = (item.get("about") or item.get("summary") or "").strip() or None
    location = _nested_str(item, "location", "linkedinText")

    return ContactCandidate(
        full_name=full_name,
        title=title or None,
        linkedin_url=url,
        company_slug=company_slug,
        snippet=about,
        location=location,
    )


@register_provider(name="apify", kind="search")
class ApifyProvider(SearchProvider):
    """LinkedIn profile discovery via the Apify harvestapi search Actor.

    Parameters
    ----------
    api_key:
        Apify API token (single key; no rotation).
    quota_manager:
        Optional :class:`QuotaManager`. When given, ``increment("apify", 1)``
        runs before each search page and raises ``QuotaExhausted`` at the cap.
    http_client:
        Optional ``httpx.Client`` (tests inject a mock transport). Default
        timeout is 120s because the synchronous run can take ~15–60s.
    profile_mode:
        ``"Full"`` (default; 184 fields incl. about/headline, clean URLs) or
        ``"Short"`` (cheaper, sparser). We use Full — see input-stack decision.
    max_charge_per_run_usd:
        Per-run hard ceiling passed to Apify so a single call can't overrun the
        budget regardless of the quota counter.
    """

    def __init__(
        self,
        api_key: str,
        quota_manager: QuotaManager | None = None,
        http_client: httpx.Client | None = None,
        profile_mode: str = "Full",
        max_charge_per_run_usd: float = 0.5,
    ) -> None:
        self._api_key = api_key
        self._quota_manager = quota_manager
        self._http_client = http_client or httpx.Client(timeout=120.0)
        self._profile_mode = profile_mode
        self._max_charge_per_run_usd = max_charge_per_run_usd

    def close(self) -> None:
        """Release the underlying httpx.Client. Safe to call repeatedly."""
        self._http_client.close()

    def search_linkedin_profiles(
        self,
        company: str,
        role_keywords: list[str],
        limit: int,
        location: str | None = None,
    ) -> list[ContactCandidate]:
        """Search LinkedIn profiles at *company* matching *role_keywords*.

        Quota-gated (one increment per call = one search page) then one
        run-sync HTTP call; parses the returned dataset items into at most
        *limit* canonical candidates. Raises ``QuotaExhausted`` at the budget
        cap; ``AuthError`` on a bad token (both surface to the caller, which
        falls back to the next discovery provider). *location*, when given, is
        appended to the semantic query to bias results geographically.
        """
        if self._quota_manager is not None:
            self._quota_manager.increment("apify", 1)

        # Broaden semantic ranking across the top role keywords, not just the
        # first (FINDER_AUDIT D4) — otherwise a composites/stress engineer can be
        # ranked below "quality engineer" matches and truncated at the limit.
        # `currentJobTitles` below still post-filters on the full keyword set.
        search_query = (
            f"{company} ({' OR '.join(role_keywords[:3])})" if role_keywords else company
        )
        if location:
            search_query = f"{search_query} {location}"
        body: dict = {
            "profileScraperMode": self._profile_mode,
            "searchQuery": search_query,
            "maxItems": max(int(limit), 1),
        }
        if role_keywords:
            body["currentJobTitles"] = list(role_keywords)

        params = {
            "token": self._api_key,
            "maxTotalChargeUsd": self._max_charge_per_run_usd,
        }

        def _do_request() -> httpx.Response:
            return self._http_client.post(_APIFY_ENDPOINT, params=params, json=body)

        response = with_retry(_do_request)
        items = response.json()
        if not isinstance(items, list):
            return []

        company_slug = _slugify(company)
        candidates: list[ContactCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = _parse_item(item, company_slug)
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates
