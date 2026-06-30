"""
tests/test_classify_host.py
Host-token classification seam (#50): apply_classification + build_classify_context
(pure) and the network_classify_host CLI bridge (context | apply). No LLM.
"""

from __future__ import annotations

import argparse
import json

from src.agents.finder import apply_classification, build_classify_context
from src.cli.network_classify_host import run_apply, run_classify_host, run_context
from src.core.schemas import ContactCandidate, FocusArea, Persona

# --------------------------------------------------------------------------- #
# apply_classification (pure post-processing)
# --------------------------------------------------------------------------- #


class TestApplyClassification:
    def test_engineer_passthrough(self):
        p, f, h = apply_classification("PEER_ENGINEER", "COMPOSITE_DESIGN", "led wing-box team")
        assert p is Persona.PEER_ENGINEER
        assert f is FocusArea.COMPOSITE_DESIGN
        assert h == "led wing-box team"

    def test_alumni_focus_forced(self):
        # ALUMNI always lands on ALUMNI_ACADEMIC regardless of the model's guess.
        _, f, _ = apply_classification("ALUMNI", "COMPOSITE_DESIGN", "")
        assert f is FocusArea.ALUMNI_ACADEMIC

    def test_recruiter_focus_forced(self):
        _, f, _ = apply_classification("RECRUITER", "MANUFACTURING", "")
        assert f is FocusArea.PEER

    def test_invalid_persona_defaults(self):
        p, f, _ = apply_classification("BOGUS", "NOPE", None)
        assert p is Persona.PEER_ENGINEER
        assert f is FocusArea.PEER

    def test_none_persona_defaults(self):
        p, f, _ = apply_classification(None, None, None)
        assert p is Persona.PEER_ENGINEER
        assert f is FocusArea.PEER

    def test_empty_hook_signal_is_none(self):
        _, _, h = apply_classification("PEER_ENGINEER", "PEER", "   ")
        assert h is None

    def test_long_hook_signal_trimmed(self):
        long_signal = "x" * 200
        _, _, h = apply_classification("PEER_ENGINEER", "PEER", long_signal)
        assert h is not None and len(h) <= 80


# --------------------------------------------------------------------------- #
# build_classify_context (pure grounding)
# --------------------------------------------------------------------------- #


class TestBuildClassifyContext:
    def test_grounding_shape(self):
        cand = ContactCandidate(
            full_name="Alice Smith", title="Composites Engineer",
            snippet="led 787 wing-box stress team", company_slug="acme",
        )
        ctx = build_classify_context(cand, "acme")
        assert ctx["full_name"] == "Alice Smith"
        assert ctx["title"] == "Composites Engineer"
        assert ctx["snippet"] == "led 787 wing-box stress team"
        assert "PEER_ENGINEER" in ctx["persona_options"]
        assert "COMPOSITE_DESIGN" in ctx["focus_options"]
        assert "hook_signal" in ctx["instruction"]

    def test_missing_title_and_snippet_default(self):
        cand = ContactCandidate(full_name="Bob", company_slug="acme")
        ctx = build_classify_context(cand, "acme")
        assert ctx["title"] == "Unknown"
        assert ctx["snippet"] == ""


# --------------------------------------------------------------------------- #
# CLI bridge
# --------------------------------------------------------------------------- #


def _ctx_args(**kw):
    base = {"verb": "context", "name": "Alice Smith", "title": "Composites Engineer",
            "snippet": "led wing-box team", "company": "acme"}
    base.update(kw)
    return argparse.Namespace(**base)


def _apply_args(**kw):
    base = {"verb": "apply", "persona": "PEER_ENGINEER", "focus": "COMPOSITE_DESIGN",
            "hook_signal": "led wing-box team"}
    base.update(kw)
    return argparse.Namespace(**base)


class TestCLI:
    def test_context_json(self, capsys):
        assert run_context(_ctx_args()) == 0
        ctx = json.loads(capsys.readouterr().out)
        assert ctx["full_name"] == "Alice Smith"
        assert "persona_options" in ctx

    def test_context_missing_name(self, capsys):
        assert run_context(_ctx_args(name="  ")) == 1
        assert "missing --name" in json.loads(capsys.readouterr().out)["error"]

    def test_apply_json(self, capsys):
        assert run_apply(_apply_args()) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["persona"] == "PEER_ENGINEER"
        assert out["focus_area"] == "COMPOSITE_DESIGN"
        assert out["hook_signal"] == "led wing-box team"

    def test_apply_forces_alumni_focus(self, capsys):
        run_apply(_apply_args(persona="ALUMNI", focus="COMPOSITE_DESIGN"))
        assert json.loads(capsys.readouterr().out)["focus_area"] == "ALUMNI_ACADEMIC"

    def test_dispatch_context(self, capsys):
        run_classify_host(_ctx_args())
        assert json.loads(capsys.readouterr().out)["full_name"] == "Alice Smith"

    def test_dispatch_apply(self, capsys):
        run_classify_host(_apply_args())
        assert json.loads(capsys.readouterr().out)["persona"] == "PEER_ENGINEER"
