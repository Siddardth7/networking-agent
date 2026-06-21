"""
tests/test_network_import.py — Tests for src/cli/network_import.py

Verifies the CLI wiring around the importer using dependency-injected import /
validate functions (no DB or LLM calls):
1. --validate OK → return 0; FAILED → return 1.
2. import success → return 0 and prints a per-company summary.
3. --draft passes auto_select+draft through and reports draft counts.
4. ContactImportError → return 1.
5. missing file arg → return 1.
"""

from __future__ import annotations

import argparse

from src.cli.network_import import run_import


def _args(file="leads.csv", company=None, location=None, source="auto",
          draft=False, validate=False) -> argparse.Namespace:
    return argparse.Namespace(
        file=file, company=company, location=location,
        source=source, draft=draft, validate=validate,
    )


class TestValidate:
    def test_validate_ok_returns_0(self, capsys):
        def fake_validate(path, source, default_company=None):
            return {"ok": True, "count": 3, "errors": [], "warnings": ["w1"]}

        rc = run_import(_args(validate=True), _validate_fn=fake_validate)
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK" in out and "3 usable" in out and "w1" in out

    def test_validate_failed_returns_1(self, capsys):
        def fake_validate(path, source, default_company=None):
            return {"ok": False, "count": 0, "errors": ["row 0: missing full_name"],
                    "warnings": []}

        rc = run_import(_args(validate=True), _validate_fn=fake_validate)
        assert rc == 1
        assert "FAILED" in capsys.readouterr().out


class TestImport:
    def test_import_success_returns_0_and_summarizes(self, capsys):
        captured = {}

        def fake_import(path, **kwargs):
            captured.update(kwargs)
            return {"joby-aviation": {"imported": 2, "contact_ids": [1, 2], "drafted": 0}}

        rc = run_import(_args(company="Joby"), _import_fn=fake_import)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Imported 2" in out and "joby-aviation: 2 imported" in out
        assert captured["auto_select"] is False and captured["draft"] is False

    def test_draft_flag_passes_through_and_reports(self, capsys):
        captured = {}

        def fake_import(path, **kwargs):
            captured.update(kwargs)
            return {"joby": {"imported": 2, "contact_ids": [1, 2], "drafted": 6}}

        rc = run_import(_args(draft=True), _import_fn=fake_import)
        assert rc == 0
        assert captured["auto_select"] is True and captured["draft"] is True
        out = capsys.readouterr().out
        assert "6 drafts generated" in out and "Total drafts: 6" in out

    def test_import_error_returns_1(self, capsys):
        from src.agents.importer import ContactImportError

        def fake_import(path, **kwargs):
            raise ContactImportError("no usable contacts")

        rc = run_import(_args(), _import_fn=fake_import)
        assert rc == 1
        assert "Import failed" in capsys.readouterr().err

    def test_missing_file_returns_1(self, capsys):
        rc = run_import(_args(file=None))
        assert rc == 1
        assert "required" in capsys.readouterr().err
