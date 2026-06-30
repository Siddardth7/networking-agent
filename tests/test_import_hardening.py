"""
tests/test_import_hardening.py
Import-layer hardening (#24): cross-source LinkedIn-URL dedup, malformed-input
paths, alias-map edge values, and the import-path branches the #21 baseline left
uncovered (existing-company reuse, client build, draft-on-import).
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from src.agents.importer import (
    ContactImportError,
    import_contacts,
    parse_contacts_file,
    parse_contacts_file_with_report,
    validate_contacts_file,
)
from src.core.db import get_connection
from src.core.slug import canonical_linkedin_url


def _classify_response(persona="PEER_ENGINEER", focus_area="PEER", hook_signal=""):
    tool = Mock()
    tool.type = "tool_use"
    tool.input = {"persona": persona, "focus_area": focus_area, "hook_signal": hook_signal}
    resp = Mock()
    resp.content = [tool]
    return resp


def _mk_client():
    client = Mock()
    client.messages.create.side_effect = lambda **kw: _classify_response()
    return client


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    monkeypatch.setattr("src.core.db._DB_PATH", path)
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", path)
    return path


# --------------------------------------------------------------------------- #
# canonical_linkedin_url (the shared dedup key)
# --------------------------------------------------------------------------- #


class TestCanonicalUrl:
    def test_strips_scheme_www_query_and_slash(self):
        assert canonical_linkedin_url("https://www.linkedin.com/in/jane/") == "linkedin.com/in/jane"

    def test_strips_query_and_fragment(self):
        assert canonical_linkedin_url("http://linkedin.com/in/jane?utm=x#sec") == (
            "linkedin.com/in/jane"
        )

    def test_case_insensitive(self):
        assert canonical_linkedin_url("HTTPS://LinkedIn.com/IN/Jane") == "linkedin.com/in/jane"

    def test_two_source_forms_collapse(self):
        a = canonical_linkedin_url("https://www.linkedin.com/in/jane/")
        b = canonical_linkedin_url("http://linkedin.com/in/jane?ref=apollo")
        assert a == b

    def test_falsy_is_empty(self):
        assert canonical_linkedin_url(None) == ""
        assert canonical_linkedin_url("") == ""


# --------------------------------------------------------------------------- #
# Cross-source dedup through the parser
# --------------------------------------------------------------------------- #


def _write_json(tmp_path, name, payload):
    f = tmp_path / name
    f.write_text(json.dumps(payload))
    return f


class TestCrossSourceDedup:
    def test_same_person_two_url_forms_deduped(self, tmp_path):
        f = _write_json(tmp_path, "leads.json", [
            {"name": "Jane Doe", "company": "Acme", "linkedin": "https://www.linkedin.com/in/jane/"},
            {"name": "Jane Doe", "company": "Acme", "linkedin": "http://linkedin.com/in/jane?x=1"},
        ])
        candidates, report = parse_contacts_file_with_report(f)
        assert len(candidates) == 1
        assert report["dropped"]["duplicate"] == 1

    def test_no_url_falls_back_to_name_company(self, tmp_path):
        f = _write_json(tmp_path, "leads.json", [
            {"name": "Jane Doe", "company": "Acme"},
            {"name": "Jane Doe", "company": "Acme"},
        ])
        candidates, report = parse_contacts_file_with_report(f)
        assert len(candidates) == 1
        assert report["dropped"]["duplicate"] == 1


# --------------------------------------------------------------------------- #
# Malformed-input paths
# --------------------------------------------------------------------------- #


class TestMalformedInput:
    def test_malformed_json_raises_clean_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json,,,")
        with pytest.raises(ContactImportError, match="Malformed JSON"):
            parse_contacts_file(f)

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "leads.txt"
        f.write_text("whatever")
        with pytest.raises(ContactImportError, match="Unsupported"):
            parse_contacts_file(f)

    def test_validate_reports_malformed_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("[{,,,]")
        result = validate_contacts_file(f)
        assert result["ok"] is False
        assert "parse failed" in result["errors"][0]

    def test_validate_reports_missing_file(self, tmp_path):
        result = validate_contacts_file(tmp_path / "nope.json")
        assert result["ok"] is False

    def test_bare_single_contact_object(self, tmp_path):
        # A JSON object that is one contact (no "contacts" list) is read as a row.
        f = _write_json(tmp_path, "one.json", {"name": "Solo Dev", "company": "Acme"})
        [c] = parse_contacts_file(f)
        assert c.full_name == "Solo Dev"

    def test_json_scalar_is_unsupported(self, tmp_path):
        # Valid JSON that is neither a list nor an object (a bare scalar).
        f = _write_json(tmp_path, "scalar.json", 42)
        with pytest.raises(ContactImportError, match="Unsupported"):
            parse_contacts_file(f)


# --------------------------------------------------------------------------- #
# Alias-map edge values
# --------------------------------------------------------------------------- #


class TestAliasEdges:
    def test_none_value_skipped(self, tmp_path):
        f = _write_json(tmp_path, "n.json", [{"name": "Jane", "company": "Acme", "title": None}])
        [c] = parse_contacts_file(f)
        assert c.title is None  # null value dropped, not stringified "None"

    def test_blank_value_skipped(self, tmp_path):
        f = _write_json(tmp_path, "b.json", [{"name": "Jane", "company": "Acme", "title": "   "}])
        [c] = parse_contacts_file(f)
        assert c.title is None  # whitespace-only dropped


# --------------------------------------------------------------------------- #
# Import-path branches (#21 baseline gaps)
# --------------------------------------------------------------------------- #


class TestImportBranches:
    def test_existing_company_is_reused(self, db_path, tmp_path):
        client = _mk_client()
        f1 = _write_json(tmp_path, "a.json", [
            {"name": "A One", "company": "Acme", "linkedin": "https://linkedin.com/in/a1"},
        ])
        f2 = _write_json(tmp_path, "b.json", [
            {"name": "B Two", "company": "Acme", "linkedin": "https://linkedin.com/in/b2"},
        ])
        import_contacts(f1, anthropic_client=client)
        import_contacts(f2, anthropic_client=client)  # reuses the Acme company row
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM companies WHERE slug='acme'"
            ).fetchone()
        finally:
            conn.close()
        assert row["n"] == 1  # not duplicated

    def test_builds_client_when_none(self, db_path, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.agents.importer.get_anthropic_client", lambda *a, **k: _mk_client()
        )
        f = _write_json(tmp_path, "c.json", [
            {"name": "Cy Three", "company": "Acme", "linkedin": "https://linkedin.com/in/c3"},
        ])
        out = import_contacts(f)  # no anthropic_client → importer builds one
        assert out["by_company"]["acme"]["imported"] == 1

    def test_draft_on_import(self, db_path, tmp_path, monkeypatch):
        # Stub the drafter so this exercises the draft-on-import branch without
        # the full LLM drafting machinery.
        monkeypatch.setattr(
            "src.agents.drafter.draft_for_contacts",
            lambda ids, anthropic_client=None: {ids[0]: [Mock(), Mock()]},
        )
        client = _mk_client()
        f = _write_json(tmp_path, "d.json", [
            {"name": "Dee Four", "company": "Acme", "linkedin": "https://linkedin.com/in/d4"},
        ])
        out = import_contacts(f, anthropic_client=client, auto_select=True, draft=True)
        assert out["by_company"]["acme"]["drafted"] == 2

    def test_no_usable_contacts_raises(self, db_path, tmp_path):
        f = _write_json(tmp_path, "empty.json", [{"title": "nobody"}])  # no name → dropped
        with pytest.raises(ContactImportError, match="No usable contacts"):
            import_contacts(f, anthropic_client=_mk_client())
