"""
src/providers/hunter.py
Hunter.io email-finder provider.
Traceability: DESIGN.md §4 (Provider Layer), §8.12 (Hard-stop quota enforcement)
"""

from __future__ import annotations

from typing import Optional

import httpx

from src.core.schemas import EmailResult
from src.providers.base import EmailProvider, register_provider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import AuthError, with_retry

__all__ = ["HunterProvider"]

_HUNTER_ENDPOINT = "https://api.hunter.io/v2/email-finder"


@register_provider(name="hunter", kind="email")
class HunterProvider(EmailProvider):
    """Email lookup provider backed by the Hunter.io Email Finder API.

    Parameters
    ----------
    api_key:
        Hunter.io API key (required).
    quota_manager:
        Optional :class:`~src.providers.quota_manager.QuotaManager` instance.
        When provided, ``increment("hunter", 1)`` is called before each
        successful HTTP request.  If quota is exhausted,
        :class:`~src.providers.retry.QuotaExhausted` is raised and the HTTP
        call is never made.
    http_client:
        Optional ``httpx.Client`` for dependency injection (e.g. in tests).
        When ``None`` a default client with a 30-second timeout is created.

    Example
    -------
    >>> provider = HunterProvider(api_key="my-key")
    >>> result = provider.find_email("Jane Doe", "boeing.com")
    >>> result.source
    'hunter'
    """

    def __init__(
        self,
        api_key: str,
        quota_manager: Optional[QuotaManager] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._quota_manager = quota_manager
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # EmailProvider interface
    # ------------------------------------------------------------------

    def find_email(
        self,
        full_name: str,
        company_domain: str,
    ) -> EmailResult:
        """Find and verify an email address via the Hunter.io Email Finder API.

        Parameters
        ----------
        full_name:
            The contact's full name (e.g. ``"Jane Doe"``).  Split on the first
            space to derive ``first_name`` and ``last_name``.
        company_domain:
            The company's root domain (e.g. ``"boeing.com"``).

        Returns
        -------
        EmailResult
            - ``email``: The found address, or ``None`` if Hunter returned no
              result.
            - ``verified``: ``True`` when the verification status is
              ``"valid"`` or ``"accept_all"``.
            - ``confidence``: Hunter's 0–100 score (``0`` when absent).
            - ``source``: always ``"hunter"``.

        Raises
        ------
        QuotaExhausted
            When the monthly Hunter quota would be exceeded.
        AuthError
            On HTTP 401 or 403 (invalid/revoked API key).
        httpx.HTTPStatusError
            After exhausting retries for 5xx or 429 responses.
        httpx.TimeoutException
            After exhausting the single timeout retry.
        """
        # --- Quota gate: check and increment before making the HTTP call ---
        if self._quota_manager is not None:
            # increment() raises QuotaExhausted if the limit would be exceeded;
            # that exception propagates directly to the caller (DESIGN §8.12).
            self._quota_manager.increment("hunter", 1)

        # --- Name splitting ---
        first, *rest = full_name.split()
        last = rest[-1] if rest else ""

        # --- HTTP call wrapped in retry/backoff ---
        params = {
            "domain": company_domain,
            "first_name": first,
            "last_name": last,
            "api_key": self._api_key,
        }

        response = with_retry(
            lambda: self._http_client.get(_HUNTER_ENDPOINT, params=params)
        )

        # --- Parse response ---
        payload = response.json()
        data: dict = payload.get("data") or {}

        if not data:
            return EmailResult(email=None, verified=False, confidence=0, source="hunter")

        email: Optional[str] = data.get("email")
        verification_status: str = data.get("verification", {}).get("status", "")
        verified: bool = verification_status in ("valid", "accept_all")
        confidence: int = int(data.get("score", 0))

        return EmailResult(
            email=email,
            verified=verified,
            confidence=confidence,
            source="hunter",
        )
