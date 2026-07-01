"""
tests/test_schemas.py
Focused tests for src/core/schemas.py
"""

import pytest
from pydantic import ValidationError

from src.core.schemas import (
    Application,
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
        "Application",
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


# ---------------------------------------------------------------------------
# Application (Phase B, #58)
# ---------------------------------------------------------------------------


def test_application_minimal_derives_slug():
    """Required fields only → company_slug derived from company via slugify."""
    app = Application(job_id="ja-1", company="Joby Aviation, Inc.", role_title="Quality Engineer")
    assert app.company_slug == "joby-aviation-inc"
    assert app.function is None
    assert app.target_keywords == []
    assert app.contacts == []


def test_application_explicit_slug_preserved():
    """An explicit company_slug is honored (not overwritten by the deriver)."""
    app = Application(
        job_id="ja-2", company="Joby Aviation", company_slug="joby", role_title="QE"
    )
    assert app.company_slug == "joby"


def test_application_blank_slug_is_derived():
    """A blank/whitespace company_slug is trimmed to '' then derived."""
    app = Application(job_id="ja-3", company="Acme Corp", company_slug="   ", role_title="QE")
    assert app.company_slug == "acme-corp"


def test_application_reuses_contact_candidate_verbatim():
    """Pre-captured leads coerce to canonical ContactCandidate records."""
    app = Application(
        job_id="ja-4",
        company="Acme",
        role_title="QE",
        contacts=[{"full_name": "Jane Doe", "company_slug": "acme", "persona": "RECRUITER"}],
    )
    assert isinstance(app.contacts[0], ContactCandidate)
    assert app.contacts[0].persona == Persona.RECRUITER


def test_application_missing_required_field_rejected():
    with pytest.raises(ValidationError):
        Application(company="Acme", role_title="QE")  # no job_id
