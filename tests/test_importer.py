"""
tests/test_importer.py
Flexible-input import: normalize Apollo / Apify / canonical / manual files into
canonical ContactCandidates, validate them, and run the shared ingest path.
Traceability: docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from src.agents.importer import (
    ContactImportError,
    import_contacts,
    parse_contacts_file,
    validate_contacts_file,
)
from src.core.db import get_connection, init_db


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


# ---------------------------------------------------------------------------
# Parsing / adapters
# ---------------------------------------------------------------------------
class TestParse:
    def test_apollo_csv_first_last_and_aliases(self, tmp_path):
        f = tmp_path / "apollo.csv"
        f.write_text(
            "First Name,Last Name,Title,Company,Person Linkedin Url,Email,City\n"
            "Maya,Lindqvist,Structures Engineer,Joby Aviation,"
            "https://linkedin.com/in/maya,maya@joby.aero,Dayton\n"
        )
        [c] = parse_contacts_file(f)
        assert c.full_name == "Maya Lindqvist"
        assert c.title == "Structures Engineer"
        assert c.company_slug == "joby-aviation"
        assert c.linkedin_url == "https://linkedin.com/in/maya"
        assert c.email == "maya@joby.aero"
        assert c.location == "Dayton"

    def test_apify_json_keys(self, tmp_path):
        f = tmp_path / "apify.json"
        f.write_text(json.dumps([
            {"fullName": "Dan Okafor", "headline": "GNC Engineer",
             "profileUrl": "https://linkedin.com/in/dan", "company": "Joby",
             "summary": "UIUC AE alum"},
        ]))
        [c] = parse_contacts_file(f)
        assert c.full_name == "Dan Okafor"
        assert c.title == "GNC Engineer"
        assert c.linkedin_url == "https://linkedin.com/in/dan"
        assert c.snippet == "UIUC AE alum"
        assert c.company_slug == "joby"

    def test_canonical_json_object_with_meta(self, tmp_path):
        f = tmp_path / "chrome.json"
        f.write_text(json.dumps({
            "company": "Sierra Space", "location": "Louisville, CO",
            "contacts": [{"full_name": "Grace Chen", "title": "Propulsion Engineer",
                          "persona": "ALUMNI", "focus_area": "PEER"}],
        }))
        [c] = parse_contacts_file(f)
        assert c.company_slug == "sierra-space"
        assert c.location == "Louisville, CO"
        assert c.persona.value == "ALUMNI"  # explicit label honored

    def test_manual_csv_with_default_company(self, tmp_path):
        f = tmp_path / "manual.csv"
        f.write_text("name,title,linkedin\nJane Doe,Engineer,https://linkedin.com/in/jane\n")
        [c] = parse_contacts_file(f, default_company="Blue Origin")
        assert c.full_name == "Jane Doe"
        assert c.company_slug == "blue-origin"

    def test_dedup_by_linkedin_url(self, tmp_path):
        f = tmp_path / "dupes.json"
        f.write_text(json.dumps([
            {"full_name": "A B", "linkedin_url": "https://linkedin.com/in/x", "company": "Co"},
            {"full_name": "A B 2", "linkedin_url": "https://linkedin.com/in/x/", "company": "Co"},
        ]))
        out = parse_contacts_file(f)
        assert len(out) == 1

    def test_rows_without_name_or_company_skipped(self, tmp_path):
        f = tmp_path / "partial.json"
        f.write_text(json.dumps([
            {"title": "no name"},
            {"full_name": "No Company"},
            {"full_name": "Good One", "company": "Co"},
        ]))
        out = parse_contacts_file(f)
        assert [c.full_name for c in out] == ["Good One"]

    def test_invalid_persona_coerced_to_none(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps([
            {"full_name": "X Y", "company": "Co", "persona": "WIZARD"},
        ]))
        [c] = parse_contacts_file(f)
        assert c.persona is None  # → classifier will run


# ---------------------------------------------------------------------------
# Validation (the producer contract check)
# ---------------------------------------------------------------------------
class TestValidate:
    def test_ok_file(self, tmp_path):
        f = tmp_path / "ok.csv"
        f.write_text("name,title,linkedin,company\nA B,Eng,https://li/a,Co\n")
        r = validate_contacts_file(f)
        assert r["ok"] is True and r["count"] == 1 and not r["errors"]

    def test_missing_name_is_error(self, tmp_path):
        f = tmp_path / "noname.csv"
        f.write_text("title,company\nEng,Co\n")
        r = validate_contacts_file(f)
        assert r["ok"] is False and any("full_name" in e for e in r["errors"])

    def test_missing_company_is_error(self, tmp_path):
        f = tmp_path / "noco.csv"
        f.write_text("name,title\nA B,Eng\n")
        r = validate_contacts_file(f)
        assert r["ok"] is False and any("company" in e for e in r["errors"])

    def test_no_channel_is_warning_not_error(self, tmp_path):
        f = tmp_path / "nochan.csv"
        f.write_text("name,company\nA B,Co\n")
        r = validate_contacts_file(f)
        assert r["ok"] is True and any("can't send" in w for w in r["warnings"])

    def test_unparseable_file(self, tmp_path):
        f = tmp_path / "broken.json"
        f.write_text("{not valid json")
        r = validate_contacts_file(f)
        assert r["ok"] is False and r["errors"]


# ---------------------------------------------------------------------------
# End-to-end import (DB + classify)
# ---------------------------------------------------------------------------
class TestImport:
    def test_import_writes_contacts_and_classifies(self, db_path, tmp_path):
        init_db()
        f = tmp_path / "leads.csv"
        f.write_text(
            "name,title,linkedin,company\n"
            "A B,Structures Engineer,https://li/a,Joby Aviation\n"
            "C D,Quality Engineer,https://li/c,Joby Aviation\n"
        )
        client = _mk_client()
        summary = import_contacts(f, anthropic_client=client)
        assert summary["joby-aviation"]["imported"] == 2

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT full_name, persona, hook, source_provider, state FROM contacts "
                "ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        assert rows[0]["persona"] == "PEER_ENGINEER"  # from classifier
        assert rows[0]["hook"]  # a hook was generated
        assert rows[0]["state"] == "NEW"  # not selected by default
        assert client.messages.create.call_count == 2  # one classify per contact

    def test_supplied_email_skips_hunter_and_labels_import(self, db_path, tmp_path):
        init_db()
        f = tmp_path / "leads.json"
        f.write_text(json.dumps({
            "company": "Joby", "contacts": [
                {"full_name": "A B", "title": "Eng", "email": "a@joby.aero",
                 "persona": "ALUMNI", "focus_area": "PEER"},
            ]}))
        # persona+focus supplied → classifier skipped entirely
        client = _mk_client()
        import_contacts(f, anthropic_client=client)
        assert client.messages.create.call_count == 0

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT email, source_provider, persona FROM contacts"
            ).fetchone()
        finally:
            conn.close()
        assert row["email"] == "a@joby.aero"
        assert row["source_provider"] == "IMPORT"
        assert row["persona"] == "ALUMNI"

    def test_auto_select_marks_selected(self, db_path, tmp_path):
        init_db()
        f = tmp_path / "leads.csv"
        f.write_text("name,title,linkedin,company\nA B,Eng,https://li/a,Joby\n")
        import_contacts(f, anthropic_client=_mk_client(), auto_select=True)
        conn = get_connection()
        try:
            row = conn.execute("SELECT state, selected FROM contacts").fetchone()
        finally:
            conn.close()
        assert row["state"] == "SELECTED" and row["selected"] == 1

    def test_empty_file_raises(self, db_path, tmp_path):
        init_db()
        f = tmp_path / "empty.csv"
        f.write_text("name,title\n")  # header only
        with pytest.raises(ContactImportError):
            import_contacts(f, anthropic_client=_mk_client(), company="Joby")

    def test_multi_company_grouped(self, db_path, tmp_path):
        init_db()
        f = tmp_path / "multi.json"
        f.write_text(json.dumps([
            {"full_name": "A B", "company": "Joby", "title": "Eng"},
            {"full_name": "C D", "company": "Sierra Space", "title": "Eng"},
        ]))
        summary = import_contacts(f, anthropic_client=_mk_client())
        assert summary["joby"]["imported"] == 1
        assert summary["sierra-space"]["imported"] == 1
