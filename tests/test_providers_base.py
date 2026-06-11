"""
tests/test_providers_base.py
Unit tests for src/providers/base.py — abstract interfaces and registry.
"""

from __future__ import annotations

import pytest

from src.core.schemas import ContactCandidate, EmailResult
from src.providers.base import (
    EmailProvider,
    SearchProvider,
    get_registry,
    register_provider,
)

# ---------------------------------------------------------------------------
# Helpers — minimal concrete implementations
# ---------------------------------------------------------------------------


class _ConcreteSearch(SearchProvider):
    """Minimal SearchProvider implementation for testing."""

    def search_linkedin_profiles(
        self,
        company: str,
        role_keywords: list[str],
        limit: int,
    ) -> list[ContactCandidate]:
        return []


class _ConcreteEmail(EmailProvider):
    """Minimal EmailProvider implementation for testing."""

    def find_email(self, full_name: str, company_domain: str) -> EmailResult:
        return EmailResult(
            email=None,
            verified=False,
            confidence=0,
            source="mock",
        )


# ---------------------------------------------------------------------------
# 1. Abstract classes cannot be instantiated directly
# ---------------------------------------------------------------------------


class TestAbstractInstantiation:
    def test_search_provider_is_abstract(self):
        """SearchProvider must raise TypeError when instantiated directly."""
        with pytest.raises(TypeError, match="abstract"):
            SearchProvider()  # type: ignore[abstract]

    def test_email_provider_is_abstract(self):
        """EmailProvider must raise TypeError when instantiated directly."""
        with pytest.raises(TypeError, match="abstract"):
            EmailProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 2. Concrete subclasses can be instantiated and implement the contract
# ---------------------------------------------------------------------------


class TestConcreteSubclasses:
    def test_concrete_search_instantiates(self):
        provider = _ConcreteSearch()
        assert isinstance(provider, SearchProvider)

    def test_concrete_search_returns_list(self):
        provider = _ConcreteSearch()
        result = provider.search_linkedin_profiles(
            company="Acme",
            role_keywords=["engineer"],
            limit=10,
        )
        assert isinstance(result, list)

    def test_concrete_email_instantiates(self):
        provider = _ConcreteEmail()
        assert isinstance(provider, EmailProvider)

    def test_concrete_email_returns_email_result(self):
        provider = _ConcreteEmail()
        result = provider.find_email(
            full_name="Jane Doe",
            company_domain="acme.com",
        )
        assert isinstance(result, EmailResult)
        assert result.verified is False
        assert result.source == "mock"

    def test_partial_subclass_stays_abstract(self):
        """A subclass that skips one abstract method must still be abstract."""

        class _Partial(SearchProvider):
            pass  # does NOT implement search_linkedin_profiles

        with pytest.raises(TypeError):
            _Partial()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 3. @register_provider records in the registry without crashing
# ---------------------------------------------------------------------------


class TestRegisterProviderDecorator:
    def test_decorator_does_not_crash(self):
        """@register_provider must not raise during decoration."""

        @register_provider(name="test_search_v1", kind="search")
        class _Reg(SearchProvider):
            def search_linkedin_profiles(self, company, role_keywords, limit):
                return []

        # If we got here, no exception was raised.

    def test_decorator_records_name_in_registry(self):
        registry = get_registry()
        assert "test_search_v1" in registry

    def test_decorator_records_kind(self):
        registry = get_registry()
        assert registry["test_search_v1"]["kind"] == "search"

    def test_decorator_records_cls(self):
        """The stored class must be the decorated class itself."""

        @register_provider(name="test_email_v1", kind="email")
        class _RegEmail(EmailProvider):
            def find_email(self, full_name, company_domain):
                return EmailResult(email=None, verified=False, confidence=0, source="test")

        registry = get_registry()
        assert registry["test_email_v1"]["cls"] is _RegEmail

    def test_decorator_returns_original_class_unchanged(self):
        """The decorator must return the class unmodified (no wrapping)."""

        @register_provider(name="test_identity", kind="search")
        class _Id(SearchProvider):
            def search_linkedin_profiles(self, company, role_keywords, limit):
                return []

        # Class must still be a SearchProvider subclass
        assert issubclass(_Id, SearchProvider)
        # And must be directly instantiable
        instance = _Id()
        assert isinstance(instance, SearchProvider)

    def test_get_registry_returns_copy(self):
        """Mutating the returned dict must not affect the internal registry."""
        before = get_registry()
        count_before = len(before)

        returned = get_registry()
        returned["__sentinel__"] = {}  # mutate the copy

        after = get_registry()
        assert "__sentinel__" not in after
        assert len(after) == count_before
