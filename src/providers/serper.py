"""
src/providers/serper.py
Google Serper API search provider for LinkedIn profile discovery.
Traceability: DESIGN.md §4 (Provider Layer)
"""

from __future__ import annotations

from typing import Optional

import httpx

from src.core.schemas import ContactCandidate
from src.providers.base import SearchProvider, register_provider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import QuotaExhausted, with_retry

__all__ = ["SerperProvider"]

_SERPER_ENDPOINT = "https://google.serper.dev/search"


@register_provider(name="serper", kind="search")
class SerperProvider(SearchProvider):
    """LinkedIn profile search provider backed by the Serper Google Search API.

    Uses ``site:linkedin.com/in`` queries to find candidate profiles matching
    a company name and role keywords.

    Parameters
    ----------
    api_key:
        Serper API key (``X-API-KEY`` header).
    quota_manager:
        Optional :class:`~src.providers.quota_manager.QuotaManager` instance
        for monthly usage tracking.  When ``None``, quota tracking is skipped.
    http_client:
        Optional ``httpx.Client`` instance.  Primarily used in tests to inject
        a mock transport.  When ``None``, a default client with a 30-second
        timeout is created internally.

    Example
    -------
    >>> provider = SerperProvider(api_key="key-here")
    >>> results = provider.search_linkedin_profiles(
    ...     company="Lockheed Martin",
    ...     role_keywords=["quality engineer", "supplier quality", "MRB"],
    ...     limit=10,
    ... )
    """

    def __init__(
        self,
        api_key: str,
        quota_manager: Optional[QuotaManager] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._quota_manager = quota_manager
        self._http_client = http_client or httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # SearchProvider implementation
    # ------------------------------------------------------------------

    def search_linkedin_profiles(
        self,
        company: str,
        role_keywords: list[str],
        limit: int,
    ) -> list[ContactCandidate]:
        """Search for LinkedIn profiles matching *company* and *role_keywords*.

        Builds a ``site:linkedin.com/in`` query, calls the Serper API with
        retry/backoff, parses the organic results into
        :class:`~src.core.schemas.ContactCandidate` objects, and returns at
        most *limit* candidates.

        Parameters
        ----------
        company:
            Target company name (e.g. ``"Lockheed Martin"``).
        role_keywords:
            List of role/title keywords joined with ``OR`` in the query
            (e.g. ``["quality engineer", "supplier quality", "MRB"]``).
        limit:
            Maximum number of candidates to return.

        Returns
        -------
        list[ContactCandidate]
            Parsed candidates, possibly empty if no matching organic results.

        Raises
        ------
        QuotaExhausted
            If the monthly quota for ``"serper"`` is exceeded.
        AuthError
            If the API key is invalid (HTTP 401/403).
        httpx.HTTPStatusError
            After retry exhaustion for 429 or 5xx responses.
        """
        # Build the query string
        keywords_str = " OR ".join(role_keywords)
        query = f'site:linkedin.com/in "{company}" ({keywords_str})'

        # Compute the company slug once (used for all ContactCandidate objects)
        company_slug = company.lower().replace(" ", "-")

        # Increment quota BEFORE the network call (fail fast if already exhausted)
        if self._quota_manager is not None:
            self._quota_manager.increment("serper", 1)

        # Perform the HTTP request with retry/backoff
        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        body = {"q": query, "num": limit}

        def _do_request() -> httpx.Response:
            return self._http_client.post(
                _SERPER_ENDPOINT,
                headers=headers,
                json=body,
            )

        response = with_retry(_do_request)

        # Parse organic results
        data = response.json()
        organic = data.get("organic", [])

        candidates: list[ContactCandidate] = []
        for item in organic:
            if len(candidates) >= limit:
                break
            candidate = self._parse_organic_result(item, company_slug)
            if candidate is not None:
                candidates.append(candidate)

        return candidates

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_organic_result(
        self, item: dict, company_slug: str
    ) -> Optional[ContactCandidate]:
        """Parse a single organic search result dict into a ContactCandidate.

        Parameters
        ----------
        item:
            A dict from the ``organic`` array in the Serper API response.
            Expected keys: ``"title"`` (str) and ``"link"`` (str).
        company_slug:
            Pre-computed slug for the target company
            (company name lowercased with spaces replaced by hyphens).

        Returns
        -------
        ContactCandidate or None
            ``None`` if the result is missing required fields or the parsed
            name is empty.
        """
        raw_title: Optional[str] = item.get("title")
        link: Optional[str] = item.get("link")

        if not raw_title or not link:
            return None

        # Extract full name: everything before the first " - "
        parts = raw_title.split(" - ")
        full_name = parts[0].strip()

        if not full_name:
            return None

        # Extract job title: second segment after " - ", strip company/site suffix
        job_title: Optional[str] = None
        if len(parts) >= 2:
            raw_job = parts[1]
            # Strip everything from " at " or " | " (whichever comes first)
            at_pos = raw_job.find(" at ")
            pipe_pos = raw_job.find(" | ")

            # Determine the earliest cut-off position
            cut = len(raw_job)  # default: no suffix found
            if at_pos != -1:
                cut = min(cut, at_pos)
            if pipe_pos != -1:
                cut = min(cut, pipe_pos)

            job_title = raw_job[:cut].strip() or None

        return ContactCandidate(
            full_name=full_name,
            title=job_title,
            linkedin_url=link,
            company_slug=company_slug,
        )
