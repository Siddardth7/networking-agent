"""
src/providers/serper.py
Google Serper API search provider for LinkedIn profile discovery.
Traceability: DESIGN.md §4 (Provider Layer)
"""

from __future__ import annotations

import httpx

from src.core.schemas import ContactCandidate
from src.core.search_cache import cache_get, cache_put
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
        quota_manager: QuotaManager | None = None,
        http_client: httpx.Client | None = None,
        cache_ttl_days: int = 0,
    ) -> None:
        self._api_key = api_key
        self._quota_manager = quota_manager
        self._http_client = http_client or httpx.Client(timeout=30.0)
        # Response cache TTL in days (v0.2.1 free-quota work). 0 = disabled
        # — the default, so unit callers get pure pass-through behavior.
        # The finder wires this from config.search_cache_ttl_days; cache
        # hits skip both the HTTP call and the quota increment.
        self._cache_ttl_days = cache_ttl_days

    def _fetch_json(self, body: dict) -> dict:
        """POST *body* to Serper, with read-through response caching.

        Inputs: the JSON request body. Output: the parsed response dict.
        Side effects: on cache miss — one quota increment and one HTTP
        call, then the response is stored; on hit — none. Raises
        QuotaExhausted / AuthError / httpx errors exactly like the
        uncached path (cache errors never mask provider errors because
        hits return before any network activity).
        """
        cached = cache_get("serper", body, self._cache_ttl_days)
        if cached is not None:
            return cached

        if self._quota_manager is not None:
            self._quota_manager.increment("serper", 1)

        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

        def _do_request() -> httpx.Response:
            return self._http_client.post(
                _SERPER_ENDPOINT,
                headers=headers,
                json=body,
            )

        response = with_retry(_do_request)
        data = response.json()
        if self._cache_ttl_days > 0 and isinstance(data, dict):
            cache_put("serper", body, data)
        return data

    def close(self) -> None:
        """Release the underlying httpx.Client (AUDIT-A25).

        Safe to call multiple times. Long-lived hosts (e.g. test
        sessions) should call this instead of relying on process exit.
        """
        self._http_client.close()

    # ------------------------------------------------------------------
    # SearchProvider implementation
    # ------------------------------------------------------------------

    def search_linkedin_profiles(
        self,
        company: str,
        role_keywords: list[str],
        limit: int,
        location: str | None = None,
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
        # Serper free tier caps num at 10. For larger limits, batch into
        # multiple queries with page offsets and deduplicate by LinkedIn URL.
        serper_max_num = 10

        # Compute the company slug once (used for all ContactCandidate objects)
        company_slug = company.lower().replace(" ", "-")

        keywords_str = " OR ".join(role_keywords)
        query = f'site:linkedin.com/in "{company}" ({keywords_str})'
        if location:
            # Location is a first-class campaign filter — bias the Google query.
            query = f'{query} "{location}"'

        candidates: list[ContactCandidate] = []
        seen_urls: set[str] = set()
        page = 1

        while len(candidates) < limit:
            batch_size = min(serper_max_num, limit - len(candidates))

            body: dict = {"q": query, "num": batch_size}
            if page > 1:
                body["page"] = page

            # Read-through cache: a hit skips both quota and HTTP (v0.2.1).
            data = self._fetch_json(body)
            organic = data.get("organic", [])

            if not organic:
                break  # no more results

            added_this_page = 0
            for item in organic:
                if len(candidates) >= limit:
                    break
                candidate = self._parse_organic_result(item, company_slug)
                if candidate is not None:
                    url_key = (candidate.linkedin_url or "").rstrip("/").lower()
                    if url_key and url_key not in seen_urls:
                        seen_urls.add(url_key)
                        candidates.append(candidate)
                        added_this_page += 1

            if added_this_page == 0:
                break  # no new unique results; stop paging

            page += 1

        return candidates

    def search_general(self, query: str) -> str | None:
        """Run a single, general-purpose Serper query and return the top
        snippet — or ``None`` on quota exhaustion, no results, or error.

        Used for Tier 4 company-news hooks (DESIGN §6); the finder calls
        this once per pipeline run and shares the result across all
        contacts. Errors are caller-swallowed; this method itself raises
        only QuotaExhausted (which the caller may also swallow).

        Parameters
        ----------
        query:
            Free-form search string (e.g. ``"Joby Aviation news 2026"``).

        Returns
        -------
        Optional[str]
            The ``snippet`` field of the first organic result, or ``None``.
        """
        body = {"q": query, "num": 3}
        try:
            data = self._fetch_json(body)
        except QuotaExhausted:
            raise  # documented contract: quota exhaustion propagates
        except Exception:
            return None
        for item in data.get("organic", []):
            snippet = (item.get("snippet") or "").strip()
            if snippet:
                return snippet
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_organic_result(self, item: dict, company_slug: str) -> ContactCandidate | None:
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
        raw_title: str | None = item.get("title")
        link: str | None = item.get("link")
        snippet: str | None = item.get("snippet")

        if not raw_title or not link:
            return None

        # Extract full name: everything before the first " - "
        parts = raw_title.split(" - ")
        full_name = parts[0].strip()

        if not full_name:
            return None

        # Extract job title: second segment after " - ", strip company/site suffix
        job_title: str | None = None
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

        # Snippet may be falsy / whitespace-only; normalize to None.
        snippet_clean = (snippet or "").strip() or None

        return ContactCandidate(
            full_name=full_name,
            title=job_title,
            linkedin_url=link,
            company_slug=company_slug,
            snippet=snippet_clean,
        )
