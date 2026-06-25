"""
src/providers/apollo.py
Apollo.io people-match email provider — FALLBACK after Hunter.
Traceability: DESIGN.md §4 (Provider Layer); input-stack decision 2026-06-25.

Used only when Hunter returns no address (or is exhausted). Quota-gated under
the "apollo" key so the free email-credit tier is never overrun.
"""

from __future__ import annotations

import httpx

from src.core.schemas import EmailResult
from src.providers.base import EmailProvider, register_provider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import with_retry

__all__ = ["ApolloProvider"]

_APOLLO_ENDPOINT = "https://api.apollo.io/v1/people/match"


@register_provider(name="apollo", kind="email")
class ApolloProvider(EmailProvider):
    """Email lookup via Apollo's people/match API.

    Parameters
    ----------
    api_key:
        Apollo API key (sent as the ``X-Api-Key`` header).
    quota_manager:
        Optional :class:`QuotaManager`; ``increment("apollo", 1)`` runs before
        each call and raises ``QuotaExhausted`` at the monthly cap.
    http_client:
        Optional ``httpx.Client`` (tests inject a mock transport).
    """

    def __init__(
        self,
        api_key: str,
        quota_manager: QuotaManager | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._quota_manager = quota_manager
        self._http_client = http_client or httpx.Client(timeout=30.0)

    def close(self) -> None:
        """Release the underlying httpx.Client. Safe to call repeatedly."""
        self._http_client.close()

    def find_email(self, full_name: str, company_domain: str) -> EmailResult:
        """Match a person via Apollo and return their email.

        Quota-gated, then one POST to people/match. Returns an empty
        ``EmailResult`` (``email=None``) when Apollo has no match or masks the
        address. Apollo doesn't return a 0–100 score, so a successful match is
        reported with ``confidence=50`` and ``verified=False`` (treat as a
        best-effort guess, not a verified address). Raises ``QuotaExhausted``
        at the cap and ``AuthError`` on a bad key.
        """
        if self._quota_manager is not None:
            self._quota_manager.increment("apollo", 1)

        first, *rest = full_name.split()
        last = rest[-1] if rest else ""

        headers = {
            "X-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        body = {
            "first_name": first,
            "last_name": last,
            "domain": company_domain,
        }

        def _do_request() -> httpx.Response:
            return self._http_client.post(_APOLLO_ENDPOINT, headers=headers, json=body)

        response = with_retry(_do_request)
        person = response.json().get("person") or {}

        email = person.get("email")
        # Apollo masks unrevealed addresses as "email_not_unlocked@domain.com".
        if not email or "email_not_unlocked" in email:
            return EmailResult(email=None, verified=False, confidence=0, source="apollo")

        return EmailResult(email=email, verified=False, confidence=50, source="apollo")
