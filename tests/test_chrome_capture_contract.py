"""
tests/test_chrome_capture_contract.py
Contract regression: the Cowork + Chrome producer's published sample output
(docs/chrome-capture.example.json) must stay a guaranteed-supported import as the
importer evolves. Traceability: docs/CHROME_PRODUCER_CONTRACT.md
"""

from __future__ import annotations

from pathlib import Path

from src.agents.importer import parse_contacts_file, validate_contacts_file

# The producer's reference output IS the contract — point the test at the real
# published file rather than a copy, so the two can never drift.
_SAMPLE = Path(__file__).resolve().parent.parent / "docs" / "chrome-capture.example.json"


def test_sample_exists():
    assert _SAMPLE.is_file(), f"missing producer sample: {_SAMPLE}"


def test_sample_validates_clean():
    report = validate_contacts_file(_SAMPLE)
    assert report["ok"] is True
    assert report["count"] == 6
    assert report["errors"] == []


def test_sample_parses_with_expected_persona_mix():
    candidates = parse_contacts_file(_SAMPLE)
    assert len(candidates) == 6
    # file-level company/location flow to every contact
    assert all(c.company_slug == "joby-aviation" for c in candidates)
    personas = [c.persona.value if c.persona else None for c in candidates]
    # alumni-first, one recruiter, two unlabeled peers (classifier's job)
    assert personas.count("ALUMNI") == 3
    assert personas.count("RECRUITER") == 1
    assert personas.count(None) == 2
