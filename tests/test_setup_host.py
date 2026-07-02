"""
tests/test_setup_host.py
Onboarding bridge (#76): status / scaffold / validated writes. Hermetic —
every test points the config dir at tmp_path.
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

import src.core.config as config_module
from src.cli.network_setup_host import run_setup_host

VALID_RESUME = """
projects:
  - id: p1
    title: "Payments platform"
    type: INDUSTRY
    focus_areas: [PEER]
    bullets:
      - id: b1
        text: "Cut checkout p99 latency 40% by moving auth to the edge."
        keywords: [backend, latency]
"""


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.delenv("NETWORKING_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("NETWORKING_AGENT_PROFILE", raising=False)
    monkeypatch.setattr(config_module, "_config_path", tmp_path / "config.yaml")
    return tmp_path


def _run(capsys, *argv, stdin: str | None = None, monkeypatch=None) -> tuple[int, dict]:
    ns = argparse.Namespace(verb=argv[0])
    if argv[0] == "write":
        ns.target = argv[1]
    if stdin is not None:
        assert monkeypatch is not None
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    rc = run_setup_host(ns)
    return rc, json.loads(capsys.readouterr().out)


class TestStatus:
    def test_fresh_dir_nothing_exists(self, tmp_config, capsys):
        rc, out = _run(capsys, "status")
        assert rc == 0
        for key in ("config", "profile", "voice", "resume_library"):
            assert out[key]["exists"] is False
        # No profile.yaml = the built-in default profile is in effect.
        assert out["profile"]["using_builtin_default"] is True
        assert out["profile"]["valid"] is True
        assert "COMPOSITE_DESIGN" in out["profile"]["focus_areas"]

    def test_config_reports_unfilled_sentinel_keys(self, tmp_config, capsys):
        from src.core.config import write_default_config

        write_default_config(tmp_config / "config.yaml")
        rc, out = _run(capsys, "status")
        assert rc == 0
        assert out["config"]["valid"] is True
        assert "anthropic_api_key" in out["config"]["unfilled_keys"]

    def test_config_invalid_yaml_reported(self, tmp_config, capsys):
        (tmp_config / "config.yaml").write_text('keys: "unclosed\n', encoding="utf-8")
        rc, out = _run(capsys, "status")
        assert out["config"]["valid"] is False
        assert "not valid YAML" in out["config"]["error"]

    def test_profile_summary_and_invalid(self, tmp_config, capsys):
        (tmp_config / "profile.yaml").write_text(
            "name: swe\nfocus_areas:\n  - name: BACKEND\n    description: backend\n",
            encoding="utf-8",
        )
        rc, out = _run(capsys, "status")
        assert out["profile"] == {
            "path": str(tmp_config / "profile.yaml"),
            "exists": True,
            "valid": True,
            "name": "swe",
            "focus_areas": ["BACKEND", "PEER", "ALUMNI_ACADEMIC"],
            "using_builtin_default": False,
        }
        (tmp_config / "profile.yaml").write_text('name: "unclosed\n', encoding="utf-8")
        import os

        os.utime(tmp_config / "profile.yaml", ns=(1, 1))
        rc, out = _run(capsys, "status")
        assert out["profile"]["valid"] is False
        assert "not valid YAML" in out["profile"]["error"]

    def test_profile_missing_named_ref_is_a_finding(self, tmp_config, capsys, monkeypatch):
        monkeypatch.setenv("NETWORKING_AGENT_PROFILE", "ghost")
        rc, out = _run(capsys, "status")
        assert rc == 0  # status never crashes
        assert out["profile"]["valid"] is False
        assert "ghost" in out["profile"]["error"]

    def test_voice_status_and_oversize_warning(self, tmp_config, capsys):
        (tmp_config / "voice.md").write_text("I write like me.", encoding="utf-8")
        rc, out = _run(capsys, "status")
        assert out["voice"]["valid"] is True
        assert out["voice"]["chars"] == 16
        (tmp_config / "voice.md").write_text("x" * (16 * 1024 + 1), encoding="utf-8")
        rc, out = _run(capsys, "status")
        assert "truncates" in out["voice"]["warning"]

    def test_resume_status_counts_and_invalid(self, tmp_config, capsys):
        (tmp_config / "resume_library.yaml").write_text(VALID_RESUME, encoding="utf-8")
        rc, out = _run(capsys, "status")
        assert out["resume_library"]["valid"] is True
        assert out["resume_library"]["projects"] == 1
        assert out["resume_library"]["bullets"] == 1
        (tmp_config / "resume_library.yaml").write_text(
            "projects:\n  - id: p1\n", encoding="utf-8"  # missing required fields
        )
        rc, out = _run(capsys, "status")
        assert out["resume_library"]["valid"] is False
        assert out["resume_library"]["error"]


class TestScaffold:
    def test_creates_then_reports_existing(self, tmp_config, capsys):
        rc, out = _run(capsys, "scaffold")
        assert rc == 0 and out["created"] is True
        assert (tmp_config / "config.yaml").exists()
        rc, out = _run(capsys, "scaffold")
        assert rc == 0 and out["created"] is False  # never overwrites


class TestWrite:
    def test_write_profile_roundtrip(self, tmp_config, capsys, monkeypatch):
        rc, out = _run(
            capsys, "write", "profile",
            stdin="# my profile\nschool_name: MIT\n", monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert out["backup"] is None and out["warnings"] == []
        # Raw text written — the comment survives.
        assert (tmp_config / "profile.yaml").read_text().startswith("# my profile")
        from src.core.profile import load_profile

        assert load_profile().school_name == "MIT"

    def test_write_profile_warns_on_unknown_key(self, tmp_config, capsys, monkeypatch):
        rc, out = _run(
            capsys, "write", "profile",
            stdin="template_dir: /tmp/x\n", monkeypatch=monkeypatch,  # typo'd key
        )
        assert rc == 0
        assert any("template_dir" in w for w in out["warnings"])

    def test_write_profile_rejects_bad_yaml_and_non_mapping(
        self, tmp_config, capsys, monkeypatch
    ):
        (tmp_config / "profile.yaml").write_text("school_name: MIT\n", encoding="utf-8")
        rc, out = _run(
            capsys, "write", "profile", stdin='x: "unclosed\n', monkeypatch=monkeypatch
        )
        assert rc == 1 and "not valid YAML" in out["error"]
        rc, out = _run(capsys, "write", "profile", stdin="- a\n- list\n", monkeypatch=monkeypatch)
        assert rc == 1 and "mapping" in out["error"]
        # A rejected write leaves the existing file untouched.
        assert (tmp_config / "profile.yaml").read_text() == "school_name: MIT\n"

    def test_write_backs_up_existing(self, tmp_config, capsys, monkeypatch):
        (tmp_config / "voice.md").write_text("old voice", encoding="utf-8")
        rc, out = _run(capsys, "write", "voice", stdin="new voice", monkeypatch=monkeypatch)
        assert rc == 0
        assert out["backup"] is not None and out["backup"].endswith(".bak")
        from pathlib import Path

        assert Path(out["backup"]).read_text() == "old voice"
        assert (tmp_config / "voice.md").read_text() == "new voice"
        # Same-second second write → distinct backup name.
        rc, out2 = _run(capsys, "write", "voice", stdin="third voice", monkeypatch=monkeypatch)
        assert out2["backup"] != out["backup"]

    def test_write_voice_rejects_empty_warns_oversize(self, tmp_config, capsys, monkeypatch):
        rc, out = _run(capsys, "write", "voice", stdin="   \n", monkeypatch=monkeypatch)
        assert rc == 1 and "empty" in out["error"]
        rc, out = _run(
            capsys, "write", "voice", stdin="x" * (16 * 1024 + 1), monkeypatch=monkeypatch
        )
        assert rc == 0 and any("truncates" in w for w in out["warnings"])

    def test_write_resume_valid_invalid_and_empty(self, tmp_config, capsys, monkeypatch):
        rc, out = _run(capsys, "write", "resume", stdin=VALID_RESUME, monkeypatch=monkeypatch)
        assert rc == 0 and out["warnings"] == []
        assert (tmp_config / "resume_library.yaml").exists()
        rc, out = _run(
            capsys, "write", "resume",
            stdin="projects:\n  - id: p1\n", monkeypatch=monkeypatch,
        )
        assert rc == 1 and out["error"]
        rc, out = _run(capsys, "write", "resume", stdin="projects: []\n", monkeypatch=monkeypatch)
        assert rc == 0 and any("no projects" in w for w in out["warnings"])
        rc, out = _run(
            capsys, "write", "resume", stdin='x: "unclosed\n', monkeypatch=monkeypatch
        )
        assert rc == 1 and "not valid YAML" in out["error"]


def test_module_entrypoint(tmp_config):
    """__main__ block routes argv → verbs (runpy, mirrors network_import's test)."""
    import runpy
    import sys as _sys
    from unittest import mock

    with mock.patch.object(_sys, "argv", ["network_setup_host", "status"]):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("src.cli.network_setup_host", run_name="__main__")
    assert exc.value.code == 0
