"""
src/providers/retry.py
Retry/backoff policy and quota-related exceptions for the provider layer.
Traceability: DESIGN.md §4 (Provider Layer), §8.12 (Hard-stop quota enforcement)
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

__all__ = [
    "AuthError",
    "ClientError",
    "QuotaExhausted",
    "with_retry",
]


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Raised on HTTP 401/403 — no retry."""


class ClientError(Exception):
    """Raised on HTTP 4xx (not 401/403/429) — no retry. Carries response body."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class QuotaExhausted(Exception):  # noqa: N818 — public name since v0.1.0
    """Raised when a provider's monthly quota would be exceeded.

    Attributes:
        provider:  The provider name (e.g. ``"serper"``, ``"hunter"``).
        used:      Current usage count *before* the attempted increment.
        limit_val: The configured monthly limit for this provider.

    The caller decides how to handle exhaustion:
    - Serper exhausted mid-discovery → abort run, leave company state NEW.
    - Hunter exhausted mid-enrichment → mark remaining contacts with
      ``email=NULL, source_provider='HUNTER_EXHAUSTED'``.
    No automatic failover occurs in v0.1.0.
    """

    def __init__(self, provider: str, used: int, limit_val: int) -> None:
        self.provider = provider
        self.used = used
        self.limit_val = limit_val
        super().__init__(
            f"Quota exhausted for provider '{provider}': used={used}, limit={limit_val}."
        )


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

# 5xx: 3 retries, exponential backoff 1s / 2s / 4s
_5XX_BACKOFFS: list[float] = [1.0, 2.0, 4.0]

# 429: 3 retries, exponential backoff 2s / 4s / 8s (unless Retry-After header present)
_429_BACKOFFS: list[float] = [2.0, 4.0, 8.0]

# Maximum value accepted from Retry-After header
_RETRY_AFTER_MAX: float = 60.0

# Delay for single timeout retry
_TIMEOUT_RETRY_DELAY: float = 5.0


# ---------------------------------------------------------------------------
# Core retry function
# ---------------------------------------------------------------------------


def with_retry(
    fn: Callable[[], httpx.Response],
    *,
    on_rate_limit_message: Callable[[float], None] | None = None,
) -> httpx.Response:
    """Execute *fn* with retry/backoff logic for HTTP provider calls.

    The retry table is:
    - **5xx** — 3 retries, backoff: 1s / 2s / 4s; raises last
      ``httpx.HTTPStatusError`` on exhaustion.
    - **429** — 3 retries, backoff: 2s / 4s / 8s; uses ``Retry-After``
      header value (clamped to ≤60s) when present; raises last
      ``httpx.HTTPStatusError`` on exhaustion.
    - **401 / 403** — raises :exc:`AuthError` immediately (no retry).
    - **other 4xx** — raises :exc:`ClientError` immediately (no retry).
    - **network timeout** — 1 retry at 5s delay; raises
      ``httpx.TimeoutException`` on exhaustion.

    Args:
        fn: A zero-argument callable that returns an ``httpx.Response``.
            May raise ``httpx.TimeoutException`` on network timeout.
        on_rate_limit_message: Optional callback invoked with the wait
            duration (float seconds) when a 429 response is encountered,
            before sleeping. Useful for surfacing user-facing messages such
            as ``"Rate-limited; waiting Ns..."``.

    Returns:
        The first successful ``httpx.Response``.

    Raises:
        AuthError: Immediately on HTTP 401 or 403.
        ClientError: Immediately on other HTTP 4xx (not 401/403/429).
        httpx.HTTPStatusError: After exhausting retries for 5xx or 429.
        httpx.TimeoutException: After exhausting the single timeout retry.
    """
    timeout_retries_remaining: int = 1
    fivexx_attempt: int = 0  # number of 5xx sleeps already done
    ratelimit_attempt: int = 0  # number of 429 sleeps already done

    last_5xx_exc: httpx.HTTPStatusError | None = None
    last_429_exc: httpx.HTTPStatusError | None = None

    while True:
        # --- Invoke the callable ---
        try:
            response = fn()
        except httpx.TimeoutException:
            if timeout_retries_remaining > 0:
                timeout_retries_remaining -= 1
                time.sleep(_TIMEOUT_RETRY_DELAY)
                continue
            raise

        status = response.status_code

        # --- Success (2xx / 3xx) ---
        if status < 400:
            return response

        # --- Auth failure: no retry ---
        if status in (401, 403):
            raise AuthError(f"Authentication error: HTTP {status}")

        # --- Rate-limited (429) ---
        if status == 429:
            if ratelimit_attempt < len(_429_BACKOFFS):
                wait = _parse_retry_after(
                    response.headers.get("Retry-After"),
                    default=_429_BACKOFFS[ratelimit_attempt],
                )
                if on_rate_limit_message is not None:
                    on_rate_limit_message(wait)
                last_429_exc = _make_status_error(response)
                ratelimit_attempt += 1
                time.sleep(wait)
                continue
            # Retries exhausted for 429
            raise last_429_exc or _make_status_error(response)

        # --- Server error (5xx) ---
        if status >= 500:
            if fivexx_attempt < len(_5XX_BACKOFFS):
                wait = _5XX_BACKOFFS[fivexx_attempt]
                last_5xx_exc = _make_status_error(response)
                fivexx_attempt += 1
                time.sleep(wait)
                continue
            # Retries exhausted for 5xx
            raise last_5xx_exc or _make_status_error(response)

        # --- Other 4xx: client error, no retry ---
        raise ClientError(status, response.text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(header_value: str | None, *, default: float) -> float:
    """Parse the ``Retry-After`` header value into seconds.

    Args:
        header_value: Raw header string (integer seconds), or ``None``.
        default: Fallback value when the header is absent or not a valid int.

    Returns:
        Wait duration in seconds as a float, clamped to ``_RETRY_AFTER_MAX``.
    """
    if header_value is None:
        return default
    try:
        seconds = int(header_value)
    except (ValueError, TypeError):
        return default
    return min(float(seconds), _RETRY_AFTER_MAX)


def _make_status_error(response: httpx.Response) -> httpx.HTTPStatusError:
    """Construct an ``httpx.HTTPStatusError`` from a response object."""
    return httpx.HTTPStatusError(
        message=f"HTTP {response.status_code}",
        request=response.request,
        response=response,
    )
