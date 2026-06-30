"""
tests/test_classify_host.py
Host-token classification seam (#50): apply_classification + build_classify_context
(pure) and the network_classify_host CLI bridge (context | apply). No LLM.
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

from src.agents.finder import apply_classification, build_classify_context
from src.cli.network_classify_host import (
    run_apply,
    run_classify_host,
    run_context,
    run_discover,
    run_ingest,
)
from src.core.db import get_connection, init_db
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


# --------------------------------------------------------------------------- #
# discover verb (HTTP-only candidate emission)
# --------------------------------------------------------------------------- #

MOD = "src.cli.network_classify_host"


class TestDiscover:
    def test_emits_candidates_with_grounding(self, capsys, monkeypatch):
        cand = ContactCandidate(
            full_name="Alice Smith", title="Composites Engineer",
            snippet="led 787 wing-box stress team", company_slug="acme",
        )
        monkeypatch.setattr(f"{MOD}.build_discovery_chain", lambda cfg: ([object()], None))
        monkeypatch.setattr(f"{MOD}._discover", lambda *a, **k: [cand])
        rc = run_discover(argparse.Namespace(verb="discover", slug="acme", limit=5, location=None))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1
        assert out[0]["candidate"]["full_name"] == "Alice Smith"
        assert out[0]["context"]["snippet"] == "led 787 wing-box stress team"
        assert "persona_options" in out[0]["context"]

    def test_missing_slug(self, capsys):
        rc = run_discover(argparse.Namespace(verb="discover", slug="  ", limit=5, location=None))
        assert rc == 1
        assert "missing slug" in json.loads(capsys.readouterr().out)["error"]

    def test_no_provider_configured(self, capsys, monkeypatch):
        def _raise(cfg):
            raise ValueError("No discovery provider configured")
        monkeypatch.setattr(f"{MOD}.build_discovery_chain", _raise)
        rc = run_discover(argparse.Namespace(verb="discover", slug="acme", limit=5, location=None))
        assert rc == 1
        assert "No discovery provider" in json.loads(capsys.readouterr().out)["error"]


# --------------------------------------------------------------------------- #
# ingest verb (save host-classified candidates, no LLM)
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _payload(**cls):
    cand = ContactCandidate(
        full_name="Alice Smith", title="Composites Engineer",
        snippet="led 787 wing-box stress team", company_slug="ignored-on-read",
    )
    base = {"persona": "PEER_ENGINEER", "focus_area": "COMPOSITE_DESIGN",
            "hook_signal": "led 787 wing-box stress team"}
    base.update(cls)
    return [{"candidate": cand.model_dump(mode="json"), "classification": base}]


def _stdin(monkeypatch, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def _ingest(slug="acme"):
    return argparse.Namespace(verb="ingest", slug=slug)


class TestIngest:
    def test_saves_with_host_classification_no_llm(self, capsys, monkeypatch, tmp_db):
        # anthropic_client is None inside ingest; if any LLM path fired it would
        # raise AttributeError. A clean save proves the path is LLM-free.
        _stdin(monkeypatch, json.dumps(_payload()))
        rc = run_ingest(_ingest())
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ingested"] == 1
        assert out["contacts"] == ["Alice Smith"]

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT persona, focus_area, hook FROM contacts WHERE full_name = 'Alice Smith'"
            ).fetchone()
            company = conn.execute(
                "SELECT state FROM companies WHERE slug = 'acme'"
            ).fetchone()
        finally:
            conn.close()
        assert row["persona"] == "PEER_ENGINEER"
        assert row["focus_area"] == "COMPOSITE_DESIGN"
        # Tier-0 hook_signal becomes the hook deterministically (no LLM).
        assert row["hook"] == "led 787 wing-box stress team"
        assert company["state"] == "FOUND"

    def test_company_slug_overridden_from_arg(self, capsys, monkeypatch, tmp_db):
        # The candidate carried "ignored-on-read"; ingest reslugs it to the arg.
        _stdin(monkeypatch, json.dumps(_payload()))
        run_ingest(_ingest(slug="boeing"))
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT c.slug FROM companies c JOIN contacts k ON k.company_id = c.id "
                "WHERE k.full_name = 'Alice Smith'"
            ).fetchone()
        finally:
            conn.close()
        assert row["slug"] == "boeing"

    def test_alumni_focus_override_applied(self, capsys, monkeypatch, tmp_db):
        _stdin(monkeypatch, json.dumps(_payload(persona="ALUMNI")))
        run_ingest(_ingest())
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT persona, focus_area FROM contacts WHERE full_name = 'Alice Smith'"
            ).fetchone()
        finally:
            conn.close()
        assert row["persona"] == "ALUMNI"
        assert row["focus_area"] == "ALUMNI_ACADEMIC"

    def test_missing_slug(self, capsys):
        assert run_ingest(argparse.Namespace(verb="ingest", slug="")) == 1
        assert "missing slug" in json.loads(capsys.readouterr().out)["error"]

    def test_bad_json(self, capsys, monkeypatch):
        _stdin(monkeypatch, "{not json")
        assert run_ingest(_ingest()) == 1
        assert "invalid JSON" in json.loads(capsys.readouterr().out)["error"]

    def test_not_a_list(self, capsys, monkeypatch):
        _stdin(monkeypatch, json.dumps({"candidate": {}}))
        assert run_ingest(_ingest()) == 1
        assert "must be a JSON list" in json.loads(capsys.readouterr().out)["error"]

    def test_item_missing_candidate(self, capsys, monkeypatch):
        _stdin(monkeypatch, json.dumps([{"classification": {}}]))
        assert run_ingest(_ingest()) == 1
        assert "'candidate'" in json.loads(capsys.readouterr().out)["error"]

    def test_empty_list_ingests_nothing(self, capsys, monkeypatch, tmp_db):
        _stdin(monkeypatch, json.dumps([]))
        assert run_ingest(_ingest()) == 0
        assert json.loads(capsys.readouterr().out)["ingested"] == 0

    def test_dispatch_discover(self, capsys, monkeypatch):
        monkeypatch.setattr(f"{MOD}.build_discovery_chain", lambda cfg: ([object()], None))
        monkeypatch.setattr(f"{MOD}._discover", lambda *a, **k: [])
        run_classify_host(argparse.Namespace(verb="discover", slug="acme", limit=5, location=None))
        assert json.loads(capsys.readouterr().out) == []

    def test_dispatch_ingest(self, capsys, monkeypatch, tmp_db):
        _stdin(monkeypatch, json.dumps(_payload()))
        run_classify_host(_ingest())
        assert json.loads(capsys.readouterr().out)["ingested"] == 1
