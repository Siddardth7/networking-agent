"""
src/providers/hunter.py
Hunter.io email provider — domain-pattern inference (issue #13). One quota-gated
domain-search per company yields the org's email pattern, applied locally to
every contact (the uncapped cold-email channel) instead of one capped per-person
Email Finder lookup each.
Traceability: DESIGN.md §4 (Provider Layer), §8.12 (Hard-stop quota enforcement)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from src.core.schemas import EmailResult
from src.providers.base import EmailProvider, register_provider
from src.providers.quota_manager import QuotaManager
from src.providers.retry import with_retry

__all__ = [
    "HunterProvider",
    "apply_email_pattern",
    "scrub_api_key_in_exc",
    "scrubbed_hunter_call",
]

# Domain-search returns the org's email *pattern* (e.g. "{first}.{last}"). One
# quota-gated call per company yields a pattern that infers every employee's
# address locally — the uncapped cold-email channel (issue #13, A5), vs the old
# per-person Email Finder that spent one of ~25 monthly credits per contact.
_HUNTER_DOMAIN_ENDPOINT = "https://api.hunter.io/v2/domain-search"

# An inferred address is a best-effort guess (not a per-address verification),
# so it carries a moderate fixed confidence and verified=False — same posture as
# Apollo's matches.
_PATTERN_CONFIDENCE = 50

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


def apply_email_pattern(
    pattern: str, first: str, last: str, domain: str
) -> str | None:
    """Build an email from a Hunter org *pattern* and a contact's name.

    *pattern* is Hunter's local-part template — ``"{first}.{last}"``,
    ``"{f}{last}"``, ``"{first}"`` etc. Tokens are filled from *first*/*last*
    (lowercased), the result joined to *domain*. Returns ``None`` when the
    pattern is empty, leaves an unresolved ``{token}`` (a token we don't know),
    or collapses to an empty/malformed local part (e.g. a required name part is
    missing). Pure — no I/O.
    """
    if not pattern or not domain:
        return None
    first = (first or "").strip().lower()
    last = (last or "").strip().lower()
    local = pattern
    for token, value in (
        ("{first_name}", first),
        ("{last_name}", last),
        ("{first}", first),
        ("{last}", last),
        ("{f}", first[:1]),
        ("{l}", last[:1]),
    ):
        local = local.replace(token, value)
    # An unknown/unfilled token means we can't trust the address.
    if "{" in local or "}" in local:
        return None
    # Tidy separators a missing name part can leave behind ("john." / ".smith").
    local = re.sub(r"[^a-z0-9._%+-]", "", local).strip("._-")
    if not local or ".." in local:
        return None
    return f"{local}@{domain}"


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
        quota_manager: QuotaManager | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._quota_manager = quota_manager
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=30.0)
        # Per-domain pattern cache (incl. None results) so a company is looked up
        # at most once per provider lifetime — the rest of its contacts are free.
        self._pattern_cache: dict[str, str | None] = {}

    def close(self) -> None:
        """Release the underlying httpx.Client (AUDIT-A25).

        Safe to call multiple times. Long-lived hosts (e.g. test
        sessions) should call this instead of relying on process exit.
        """
        self._http_client.close()

    # ------------------------------------------------------------------
    # EmailProvider interface
    # ------------------------------------------------------------------

    def email_pattern(self, company_domain: str) -> str | None:
        """Return the org's email pattern for *company_domain*, cached per domain.

        One quota-gated Hunter ``domain-search`` per company; the result (a
        pattern like ``"{first}.{last}"``, or ``None`` when Hunter has none) is
        cached — so the first contact at a company spends one credit and every
        contact after is inferred for free (the uncapped channel, issue #13).

        Raises ``QuotaExhausted`` when the monthly cap would be exceeded (before
        any HTTP call), ``AuthError`` on 401/403, and httpx errors after retry
        exhaustion — the unchanged Hunter contract.
        """
        if company_domain in self._pattern_cache:
            return self._pattern_cache[company_domain]

        if self._quota_manager is not None:
            # Raises QuotaExhausted if the limit would be exceeded (DESIGN §8.12);
            # nothing is cached, so the caller's HUNTER_EXHAUSTED path is intact.
            self._quota_manager.increment("hunter", 1)

        params = {"domain": company_domain, "api_key": self._api_key}
        with scrubbed_hunter_call(self._api_key):
            response = with_retry(
                lambda: self._http_client.get(_HUNTER_DOMAIN_ENDPOINT, params=params)
            )

        data: dict = (response.json() or {}).get("data") or {}
        pattern = data.get("pattern") or None
        self._pattern_cache[company_domain] = pattern
        return pattern

    def find_email(
        self,
        full_name: str,
        company_domain: str,
    ) -> EmailResult:
        """Infer an email from the company's Hunter pattern.

        Gets the org pattern via :meth:`email_pattern` (one quota-gated lookup
        per company, then cached), then applies it to *full_name* locally. The
        returned address is a best-effort guess (``verified=False``,
        ``confidence=50``, ``source="hunter_pattern"``); ``email`` is ``None``
        when the company has no pattern or the name can't fill it — letting the
        provider chain fall through to Apollo.

        Parameters
        ----------
        full_name:
            The contact's full name (split on whitespace → first/last).
        company_domain:
            The company's root domain (e.g. ``"boeing.com"``).

        Raises
        ------
        QuotaExhausted
            When the monthly Hunter quota would be exceeded (on the first,
            uncached lookup for a domain).
        AuthError
            On HTTP 401 or 403 (invalid/revoked API key).
        httpx.HTTPStatusError / httpx.TimeoutException
            After exhausting retries.
        """
        pattern = self.email_pattern(company_domain)
        if not pattern:
            return EmailResult(email=None, verified=False, confidence=0, source="hunter")

        first, *rest = full_name.split()
        last = rest[-1] if rest else ""
        email = apply_email_pattern(pattern, first, last, company_domain)
        if not email:
            return EmailResult(email=None, verified=False, confidence=0, source="hunter")

        return EmailResult(
            email=email,
            verified=False,
            confidence=_PATTERN_CONFIDENCE,
            source="hunter_pattern",
        )
