"""
src/providers/base.py
Abstract provider interfaces and the @register_provider decorator stub.
Traceability: DESIGN.md §4 (Provider Layer)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.core.schemas import ContactCandidate, EmailResult

__all__ = [
    "SearchProvider",
    "EmailProvider",
    "register_provider",
    "get_registry",
]

# ---------------------------------------------------------------------------
# Provider registry
# v0.1.0: decorator is a no-op; runtime registry lands in v0.1.1
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Abstract base classes
# ---------------------------------------------------------------------------


class SearchProvider(ABC):
    """Contract for LinkedIn-style profile search providers.

    Concrete implementations (e.g. PhantomBuster, Apify, mock) must
    implement :meth:`search_linkedin_profiles` and may add their own
    ``__init__`` for credentials / configuration.
    """

    @abstractmethod
    def search_linkedin_profiles(
        self,
        company: str,
        role_keywords: list[str],
        limit: int,
        location: str | None = None,
    ) -> list[ContactCandidate]:
        """Search for LinkedIn profiles matching the given criteria.

        Args:
            company: Target company name or slug.
            role_keywords: Keywords matched against job titles (e.g. ``["engineer", "materials"]``).
            limit: Maximum number of candidates to return.
            location: Optional geographic filter (e.g. ``"Dayton, OH"``) — providers
                fold it into their query (location is a first-class campaign filter).

        Returns:
            A list of :class:`~src.core.schemas.ContactCandidate` objects,
            possibly empty if no matches are found.
        """


class EmailProvider(ABC):
    """Contract for email lookup / verification providers.

    Concrete implementations (e.g. Hunter.io, Apollo, mock) must
    implement :meth:`find_email`.
    """

    @abstractmethod
    def find_email(
        self,
        full_name: str,
        company_domain: str,
    ) -> EmailResult:
        """Attempt to find and verify an email address.

        Args:
            full_name: The contact's full name (e.g. ``"Jane Doe"``).
            company_domain: The company's root domain (e.g. ``"acme.com"``).

        Returns:
            An :class:`~src.core.schemas.EmailResult` with ``email``,
            ``verified``, ``confidence``, and ``source`` fields.
            If no email is found, ``email`` is ``None`` and ``verified``
            is ``False``.
        """


# ---------------------------------------------------------------------------
# @register_provider decorator stub
# v0.1.0: decorator is a no-op; runtime registry lands in v0.1.1
# ---------------------------------------------------------------------------


def register_provider(name: str, kind: str):
    """Class decorator that registers a provider under *name* and *kind*.

    In v0.1.0 this is a **no-op** beyond recording metadata in
    :data:`_PROVIDER_REGISTRY`; the runtime dispatch layer (plugin loading,
    dependency injection) will be wired up in v0.1.1.

    Args:
        name: Unique identifier for this provider (e.g. ``"hunter"``).
        kind: Provider category — ``"search"`` or ``"email"``.

    Example::

        @register_provider(name="mock_search", kind="search")
        class MockSearchProvider(SearchProvider):
            def search_linkedin_profiles(self, company, role_keywords, limit):
                return []
    """

    def decorator(cls: type) -> type:
        # v0.1.0: decorator is a no-op; runtime registry lands in v0.1.1
        _PROVIDER_REGISTRY[name] = {
            "kind": kind,
            "cls": cls,
        }
        return cls  # return the class unchanged

    return decorator


# ---------------------------------------------------------------------------
# Registry accessor
# ---------------------------------------------------------------------------


def get_registry() -> dict[str, dict[str, Any]]:
    """Return a shallow copy of the provider registry.

    Returns:
        A ``dict`` mapping provider *name* → ``{"kind": ..., "cls": ...}``.
        Mutating the returned dict does not affect the internal registry.
    """
    return dict(_PROVIDER_REGISTRY)
