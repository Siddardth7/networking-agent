"""
src/providers/hunter.py
Hunter.io email-finder provider.
Traceability: DESIGN.md §4 (Provider Layer), §8.12 (Hard-stop quota enforcement)
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator, Optional

import httpx

from src.core.schemas import EmailResult
from src.providers.base import EmailProvider, register_provider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import AuthError, with_retry

__all__ = ["HunterProvider", "scrub_api_key_in_exc", "scrubbed_hunter_call"]

_HUNTER_ENDPOINT = "https://api.hunter.io/v2/email-finder"

_REDACTED = "***"


@contextmanager
def scrubbed_hunter_call(api_key: str) -> Iterator[None]:
    """Context manager that scrubs ``api_key`` from any httpx exception raised.

    Use at every Hunter call site so the wire-level ``?api_key=`` value
    cannot leak into stderr tracebacks. Intentionally re-raises with
    ``from None`` to break the ``__cause__``/``__context__`` chain — the
    original unscrubbed exception must not surface.
    """
    try:
        yield
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        # `from None` is deliberate: the original exception's repr would
        # leak the api_key via request.url. Traceback chain is intentionally
        # broken for security.
        raise scrub_api_key_in_exc(exc, api_key) from None


def scrub_api_key_in_exc(exc: BaseException, api_key: str) -> BaseException:
    """Return a new exception of the same class with ``api_key`` redacted.

    Hunter authenticates via ``?api_key=`` query parameter, so any
    ``httpx`` exception carrying a ``request.url`` will leak the key if its
    ``__repr__``/``__str__`` reaches stderr. This helper builds a sanitized
    twin of ``exc``:

    - For ``httpx.HTTPStatusError``: replaces ``api_key`` query param on
      ``request.url`` (and the response's request URL) with ``***``.
    - For ``httpx.RequestError`` (incl. ``TimeoutException``): same for
      ``request.url`` when present.
    - Always scrubs the message/args via literal key replacement and a
      regex catch-all on ``api_key=<value>``.

    The original exception is *not* chained (use ``raise scrubbed from None``)
    to avoid the leak resurfacing via ``__cause__``/``__context__``.

    ``api_key`` is required — passing ``""`` to disable literal replacement
    is intentional and supported by the regex fallback.
    """

    def _scrub_str(s):
        if not isinstance(s, str):
            return s
        out = s
        if api_key:
            out = out.replace(api_key, _REDACTED)
        out = re.sub(r"(api_key=)[^&\s'\"]+", r"\1" + _REDACTED, out)
        return out

    def _scrub_url(url):
        try:
            if "api_key" in url.params:
                return url.copy_set_param("api_key", _REDACTED)
        except Exception:
            pass
        return url

    new_args = tuple(_scrub_str(a) if isinstance(a, str) else a for a in exc.args)

    if isinstance(exc, httpx.HTTPStatusError):
        req = exc.request
        resp = exc.response
        try:
            req = httpx.Request(
                method=req.method,
                url=_scrub_url(req.url),
                headers=req.headers,
            )
        except Exception:
            pass
        try:
            resp_scrubbed = httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                content=resp.content,
                request=req,
            )
        except Exception:
            resp_scrubbed = resp
        return httpx.HTTPStatusError(
            message=_scrub_str(str(exc)),
            request=req,
            response=resp_scrubbed,
        )

    if isinstance(exc, httpx.RequestError):
        req = getattr(exc, "_request", None)
        if req is None:
            try:
                req = exc.request
            except RuntimeError:
                req = None
        scrubbed_req = None
        if req is not None:
            try:
                scrubbed_req = httpx.Request(
                    method=req.method,
                    url=_scrub_url(req.url),
                    headers=req.headers,
                )
            except Exception:
                scrubbed_req = None
        try:
            new_exc = type(exc)(_scrub_str(str(exc)), request=scrubbed_req)
        except TypeError:
            try:
                new_exc = type(exc)(_scrub_str(str(exc)))
            except Exception:
                new_exc = exc
        return new_exc

    try:
        return type(exc)(*new_args)
    except Exception:
        return exc


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

        with scrubbed_hunter_call(self._api_key):
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
