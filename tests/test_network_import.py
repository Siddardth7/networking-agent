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

import pytest

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
            return {
                "by_company": {
                    "joby-aviation": {"imported": 2, "contact_ids": [1, 2], "drafted": 0}
                },
                "contribution": {"source": "apollo", "rows_read": 3, "usable": 2,
                                 "dropped": {"no_name": 1, "no_company": 0, "duplicate": 0}},
            }

        rc = run_import(_args(company="Joby"), _import_fn=fake_import)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Imported 2" in out and "joby-aviation: 2 imported" in out
        # "No silent caps": the contribution line surfaces reads + drops.
        assert "3 row(s) read" in out and "2 usable" in out and "1 no-name" in out
        assert captured["auto_select"] is False and captured["draft"] is False

    def test_draft_flag_passes_through_and_reports(self, capsys):
        captured = {}

        def fake_import(path, **kwargs):
            captured.update(kwargs)
            return {
                "by_company": {"joby": {"imported": 2, "contact_ids": [1, 2], "drafted": 6}},
                "contribution": {"source": "auto", "rows_read": 2, "usable": 2,
                                 "dropped": {"no_name": 0, "no_company": 0, "duplicate": 0}},
            }

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

    def test_file_not_found_returns_1(self, capsys):
        """FileNotFoundError from import_fn → returns 1 with message (lines 88-90)."""
        def fake_import(path, **kwargs):
            raise FileNotFoundError("no such file")

        rc = run_import(_args(file="nonexistent.csv"), _import_fn=fake_import)
        assert rc == 1
        assert "file not found" in capsys.readouterr().err


class TestLazyImports:
    def test_validate_fn_none_uses_real_importer(self, monkeypatch, tmp_path, capsys):
        """_validate_fn=None triggers the lazy import (line 58)."""
        from src.agents import importer as importer_module

        fake_report = {"ok": True, "count": 1, "errors": [], "warnings": []}

        monkeypatch.setattr(importer_module, "validate_contacts_file",
                            lambda path, source, default_company=None: fake_report)

        rc = run_import(_args(file="x.csv", validate=True))
        assert rc == 0
        assert "OK" in capsys.readouterr().out

    def test_import_fn_none_uses_real_importer(self, monkeypatch, capsys):
        """_import_fn=None triggers the lazy import (line 71)."""
        from src.agents import importer as importer_module

        fake_summary = {
            "by_company": {"acme": {"imported": 1, "contact_ids": [1], "drafted": 0}},
            "contribution": {
                "source": "manual", "rows_read": 1, "usable": 1,
                "dropped": {"no_name": 0, "no_company": 0, "duplicate": 0},
            },
        }
        monkeypatch.setattr(importer_module, "import_contacts",
                            lambda path, **kwargs: fake_summary)

        rc = run_import(_args(file="x.csv"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Imported 1" in out


def test_main_entrypoint_validate(monkeypatch, tmp_path):
    """Cover the __main__ argparse block + run_import wiring via the DB-free
    --validate path (catches CLI/argparse regressions). Issue #25."""
    import runpy
    import sys

    f = tmp_path / "leads.json"
    f.write_text('{"company": "acme", "contacts": [{"full_name": "Ada Lovelace"}]}')
    monkeypatch.setattr(sys, "argv", ["network-import", str(f), "--validate"])
    # Drop the cached import so runpy re-executes it cleanly as __main__.
    monkeypatch.delitem(sys.modules, "src.cli.network_import", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("src.cli.network_import", run_name="__main__")
    assert exc.value.code == 0
