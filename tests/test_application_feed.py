"""
tests/test_application_feed.py
Tests for src/agents/application_feed.py — Application-mode feed parser (#58).
Hermetic: writes feed files to tmp_path, no DB, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.application_feed import (
    ApplicationFeedError,
    parse_application_feed,
    validate_application_feed,
)
from src.core.schemas import Application


def _write(tmp_path: Path, obj) -> Path:
    """Write *obj* (JSON-serializable) to a feed file and return the path."""
    p = tmp_path / "feed.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _feed(*postings) -> dict:
    return {"schema": "application-feed/v1", "profile_ref": "default",
            "applications": list(postings)}


_POSTING = {
    "job_id": "ja-2026-06-30-001",
    "company": "Joby Aviation",
    "company_slug": "joby-aviation",
    "location": "Dayton, OH",
    "role_title": "Quality Engineer",
    "job_url": "https://example.com/req/1",
    "function": "QUALITY",
    "target_keywords": ["quality", "MRB", "AS9100"],
    "score": 88,
    "deadline": "2026-07-07",
    "source": "cockpit",
    "contacts": [],
}


# ---------------------------------------------------------------------------
# parse_application_feed — happy path
# ---------------------------------------------------------------------------


def test_parse_full_posting(tmp_path: Path) -> None:
    apps, report = parse_application_feed(_write(tmp_path, _feed(_POSTING)))
    assert len(apps) == 1
    app = apps[0]
    assert isinstance(app, Application)
    assert app.job_id == "ja-2026-06-30-001"
    assert app.role_title == "Quality Engineer"
    assert app.target_keywords == ["quality", "MRB", "AS9100"]
    assert report == {
        "schema": "application-feed/v1",
        "profile_ref": "default",
        "postings_read": 1,
        "usable": 1,
        "dropped": {"not_object": 0, "invalid": 0, "duplicate": 0},
    }


def test_parse_derives_company_slug(tmp_path: Path) -> None:
    posting = {"job_id": "x", "company": "Sierra Space", "role_title": "Structures Eng"}
    apps, _ = parse_application_feed(_write(tmp_path, _feed(posting)))
    assert apps[0].company_slug == "sierra-space"


def test_parse_empty_applications(tmp_path: Path) -> None:
    apps, report = parse_application_feed(_write(tmp_path, _feed()))
    assert apps == []
    assert report["postings_read"] == 0
    assert report["usable"] == 0


# ---------------------------------------------------------------------------
# parse_application_feed — no-silent-caps drop accounting
# ---------------------------------------------------------------------------


def test_parse_counts_non_object_posting(tmp_path: Path) -> None:
    apps, report = parse_application_feed(_write(tmp_path, _feed(_POSTING, "not-a-dict", 42)))
    assert len(apps) == 1
    assert report["postings_read"] == 3
    assert report["dropped"]["not_object"] == 2


def test_parse_counts_invalid_posting(tmp_path: Path) -> None:
    bad = {"company": "Acme", "role_title": "QE"}  # missing job_id
    apps, report = parse_application_feed(_write(tmp_path, _feed(_POSTING, bad)))
    assert len(apps) == 1
    assert report["dropped"]["invalid"] == 1


def test_parse_counts_duplicate_job_id(tmp_path: Path) -> None:
    dupe = dict(_POSTING, role_title="Different Role")
    apps, report = parse_application_feed(_write(tmp_path, _feed(_POSTING, dupe)))
    assert len(apps) == 1  # first wins (job_id is the linkage PK)
    assert apps[0].role_title == "Quality Engineer"
    assert report["dropped"]["duplicate"] == 1


# ---------------------------------------------------------------------------
# parse_application_feed — file-level errors (ApplicationFeedError)
# ---------------------------------------------------------------------------


def test_parse_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "feed.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ApplicationFeedError, match="Malformed JSON"):
        parse_application_feed(p)


def test_parse_non_object_top_level_raises(tmp_path: Path) -> None:
    with pytest.raises(ApplicationFeedError, match="must be a JSON object"):
        parse_application_feed(_write(tmp_path, ["a", "b"]))


def test_parse_missing_applications_list_raises(tmp_path: Path) -> None:
    with pytest.raises(ApplicationFeedError, match="missing an 'applications' list"):
        parse_application_feed(_write(tmp_path, {"schema": "application-feed/v1"}))


# ---------------------------------------------------------------------------
# validate_application_feed
# ---------------------------------------------------------------------------


def test_validate_ok(tmp_path: Path) -> None:
    result = validate_application_feed(_write(tmp_path, _feed(_POSTING)))
    assert result == {"ok": True, "count": 1, "errors": [], "warnings": []}


def test_validate_reports_non_object_and_invalid(tmp_path: Path) -> None:
    bad = {"company": "Acme"}  # missing job_id + role_title
    result = validate_application_feed(_write(tmp_path, _feed("nope", bad)))
    assert result["ok"] is False
    assert result["count"] == 0
    assert any("not a JSON object" in e for e in result["errors"])
    assert any("invalid" in e and "job_id" in e for e in result["errors"])


def test_validate_reports_duplicate_job_id(tmp_path: Path) -> None:
    result = validate_application_feed(_write(tmp_path, _feed(_POSTING, dict(_POSTING))))
    assert result["ok"] is False
    assert any("duplicate job_id" in e for e in result["errors"])


def test_validate_warns_missing_job_url(tmp_path: Path) -> None:
    posting = {"job_id": "x", "company": "Acme", "role_title": "QE"}  # no job_url
    result = validate_application_feed(_write(tmp_path, _feed(posting)))
    assert result["ok"] is True
    assert result["count"] == 1
    assert any("no job_url" in w for w in result["warnings"])


def test_validate_warns_unrecognized_schema(tmp_path: Path) -> None:
    feed = {"schema": "application-feed/v2", "applications": [_POSTING]}
    result = validate_application_feed(_write(tmp_path, feed))
    assert any("unrecognized schema" in w for w in result["warnings"])
    assert result["ok"] is True  # unknown schema warns, doesn't fail


def test_validate_missing_schema_no_warning(tmp_path: Path) -> None:
    feed = {"applications": [_POSTING]}  # schema absent → None → no schema warning
    result = validate_application_feed(_write(tmp_path, feed))
    assert not any("schema" in w for w in result["warnings"])
    assert result["ok"] is True


def test_validate_malformed_json_reports_parse_failed(tmp_path: Path) -> None:
    p = tmp_path / "feed.json"
    p.write_text("{bad", encoding="utf-8")
    result = validate_application_feed(p)
    assert result["ok"] is False
    assert result["errors"] and "parse failed" in result["errors"][0]


def test_validate_missing_file_reports_parse_failed(tmp_path: Path) -> None:
    result = validate_application_feed(tmp_path / "does_not_exist.json")
    assert result["ok"] is False
    assert "parse failed" in result["errors"][0]
