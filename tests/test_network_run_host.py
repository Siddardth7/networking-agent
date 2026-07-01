"""
tests/test_network_run_host.py
Host-token run planner (#50): build_run_plan + the plan CLI. Read-only, no LLM.
"""

from __future__ import annotations

import argparse
import json

import pytest

from src.cli.network_run_host import (
    apply_selection,
    build_run_plan,
    run_plan,
    run_run_host,
    run_select,
)
from src.core.db import get_connection, init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _company(slug="acme", state="NEW") -> int:
    with with_writer() as conn:
        conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, ?)",
            (slug, slug.title(), state),
        )
        return int(conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()["id"])


def _contact(company_id, name, state="NEW", rank=0) -> int:
    with with_writer() as conn:
        return int(conn.execute(
            "INSERT INTO contacts (company_id, full_name, title, persona, focus_area, "
            "hook, rank_score, state) VALUES (?,?,?,?,?,?,?,?)",
            (company_id, name, "Engineer", "PEER_ENGINEER", "PEER", "your work", rank, state),
        ).lastrowid)


class TestBuildRunPlan:
    def test_unknown_company_plans_discover(self):
        plan = build_run_plan("ghost")
        assert plan["company"] is None
        assert plan["state"] == "NEW"
        assert plan["next"] == "discover"
        assert plan["items"] == []

    def test_new_company_plans_discover(self):
        _company("acme", "NEW")
        plan = build_run_plan("acme")
        assert plan["next"] == "discover"
        assert plan["company"]["slug"] == "acme"

    def test_found_plans_select_with_ranked_contacts(self):
        cid = _company("acme", "FOUND")
        _contact(cid, "Low", rank=1)
        _contact(cid, "High", rank=9)
        plan = build_run_plan("acme")
        assert plan["next"] == "select"
        # Rank-ordered: highest first.
        assert [c["full_name"] for c in plan["items"]] == ["High", "Low"]

    def test_selected_plans_draft_with_selected_only(self):
        cid = _company("acme", "SELECTED")
        _contact(cid, "Picked", state="SELECTED")
        _contact(cid, "Skipped", state="NEW")
        plan = build_run_plan("acme")
        assert plan["next"] == "draft"
        assert [c["full_name"] for c in plan["items"]] == ["Picked"]

    def test_selected_but_all_drafted_plans_approve(self):
        # No company SELECTED→DRAFTED transition exists; once every selected
        # contact has been drafted (contact state DRAFTED, none left SELECTED),
        # the planner must advance to approve instead of stalling on draft/[].
        cid = _company("acme", "SELECTED")
        _contact(cid, "Done", state="DRAFTED")
        plan = build_run_plan("acme")
        assert plan["next"] == "approve"
        assert plan["items"] == []

    def test_selected_partial_draft_still_drafts_remainder(self):
        cid = _company("acme", "SELECTED")
        _contact(cid, "Drafted", state="DRAFTED")
        _contact(cid, "Pending", state="SELECTED")
        plan = build_run_plan("acme")
        assert plan["next"] == "draft"
        assert [c["full_name"] for c in plan["items"]] == ["Pending"]

    def test_drafted_plans_approve(self):
        _company("acme", "DRAFTED")
        plan = build_run_plan("acme")
        assert plan["next"] == "approve"
        assert plan["items"] == []

    def test_approved_plans_done(self):
        _company("acme", "APPROVED")
        assert build_run_plan("acme")["next"] == "done"

    def test_unknown_state_plans_unknown(self):
        _company("acme", "WONKY")
        plan = build_run_plan("acme")
        assert plan["next"] == "unknown"
        assert plan["items"] == []


class TestSelect:
    def _states(self, company_id):
        conn = get_connection()
        try:
            crows = {r["full_name"]: r["state"] for r in conn.execute(
                "SELECT full_name, state FROM contacts WHERE company_id = ?", (company_id,)
            ).fetchall()}
            co = conn.execute(
                "SELECT state FROM companies WHERE id = ?", (company_id,)
            ).fetchone()["state"]
            return crows, co
        finally:
            conn.close()

    def test_marks_selected_and_company(self):
        cid = _company("acme", "FOUND")
        a = _contact(cid, "Alice")
        _contact(cid, "Bob")
        result = apply_selection("acme", [a])
        assert result["selected"] == [a]
        crows, co = self._states(cid)
        assert crows["Alice"] == "SELECTED" and crows["Bob"] == "NEW"
        assert co == "SELECTED"

    def test_foreign_contact_id_ignored(self):
        cid = _company("acme", "FOUND")
        other = _company("other", "FOUND")
        x = _contact(other, "Outsider")
        result = apply_selection("acme", [x])
        # Not in acme → not applied → company stays FOUND.
        assert result["selected"] == []
        assert self._states(cid)[1] == "FOUND"

    def test_unknown_company(self):
        assert "error" in apply_selection("ghost", [1])

    def test_run_select_cli(self, capsys):
        cid = _company("acme", "FOUND")
        a = _contact(cid, "Alice")
        assert run_select("acme", f"{a}") == 0
        assert json.loads(capsys.readouterr().out)["selected"] == [a]

    def test_run_select_no_ids(self, capsys):
        assert run_select("acme", " , ,") == 1
        assert "no valid ids" in json.loads(capsys.readouterr().out)["error"]

    def test_run_select_missing_slug(self, capsys):
        assert run_select("", "1") == 1
        assert "missing slug" in json.loads(capsys.readouterr().out)["error"]

    def test_run_select_unknown_company_rc1(self, capsys):
        assert run_select("ghost", "1") == 1
        assert "company not found" in json.loads(capsys.readouterr().out)["error"]

    def test_dispatch_select(self, capsys):
        cid = _company("acme", "FOUND")
        a = _contact(cid, "Alice")
        run_run_host(argparse.Namespace(verb="select", slug="acme", ids=str(a)))
        assert json.loads(capsys.readouterr().out)["selected"] == [a]


class TestCLI:
    def test_plan_prints_json(self, capsys):
        _company("acme", "FOUND")
        assert run_plan("acme") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["next"] == "select"

    def test_missing_slug(self, capsys):
        assert run_plan("  ") == 1
        assert "missing slug" in json.loads(capsys.readouterr().out)["error"]

    def test_dispatch(self, capsys):
        _company("acme", "APPROVED")
        run_run_host(argparse.Namespace(verb="plan", slug="acme"))
        assert json.loads(capsys.readouterr().out)["next"] == "done"
