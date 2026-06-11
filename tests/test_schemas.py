"""
tests/test_schemas.py
Focused tests for src/core/schemas.py
"""

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    Channel,
    ContactCandidate,
    DraftDispatchRequest,
    DraftDispatchResponse,
    Persona,
)


def test_star_import_succeeds():
    """All public names are importable."""
    from src.core.schemas import __all__  # noqa: F401

    expected = {
        "Persona",
        "FocusArea",
        "Channel",
        "PipelineState",
        "ContactState",
        "ContactCandidate",
        "EmailResult",
        "RetryDecision",
        "DraftDispatchRequest",
        "DraftDispatchResponse",
    }
    assert expected.issubset(set(__all__))


def test_contact_candidate_validation():
    result = ContactCandidate.model_validate(
        {"full_name": "Jane Doe", "company_slug": "acme", "persona": "RECRUITER"}
    )
    assert result.persona == Persona.RECRUITER
    assert result.full_name == "Jane Doe"
    assert result.company_slug == "acme"


def test_draft_dispatch_request_round_trip():
    req = DraftDispatchRequest(contact_id=1, channel=Channel.LINKEDIN_CONNECTION)
    data = req.model_dump()
    req2 = DraftDispatchRequest.model_validate(data)
    assert req2.channel == Channel.LINKEDIN_CONNECTION
    assert req2.max_attempts == 2


def test_draft_dispatch_response_ok():
    resp = DraftDispatchResponse(
        status="OK",
        new_draft_id=42,
        new_version=2,
        body="Hello...",
        quality_flag=False,
    )
    assert resp.status == "OK"
    assert resp.quality_flag is False


def test_invalid_enum_rejected():
    with pytest.raises(ValidationError):
        ContactCandidate.model_validate(
            {"full_name": "X", "company_slug": "y", "persona": "INVALID"}
        )
