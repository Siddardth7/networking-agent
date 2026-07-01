"""
tests/test_network_jobs_host.py
Tests for src/cli/network_jobs_host.py — the Application-mode bridge (#59).
plan (parse feed + persist postings + emit work items) and link (stdin → join).
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest

from src.cli.network_jobs_host import run_jobs_host, run_link, run_plan
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import ContactCandidate


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.db._DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr("src.providers.quota_manager._DB_PATH", tmp_path / "state.db")
    init_db()
    return tmp_path


def _feed_file(tmp_path: Path, *postings) -> str:
    feed = {"schema": "application-feed/v1", "profile_ref": "default",
            "applications": list(postings)}
    p = tmp_path / "feed.json"
    p.write_text(json.dumps(feed), encoding="utf-8")
    return str(p)


_POSTING = {
    "job_id": "ja-1", "company": "Joby Aviation", "role_title": "Quality Engineer",
    "location": "Dayton, OH", "target_keywords": ["quality", "MRB"], "job_url": "https://x/1",
}


def _stdin(monkeypatch, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestPlan:
    def test_persists_postings_and_emits_work_items(self, tmp_db, tmp_path, capsys):
        feed = _feed_file(tmp_path, _POSTING)
        rc = run_plan(argparse.Namespace(verb="plan", feed=feed))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert len(out["postings"]) == 1
        item = out["postings"][0]
        assert item["job_id"] == "ja-1"
        assert item["company_slug"] == "joby-aviation"
        assert item["target_keywords"] == ["quality", "MRB"]
        assert item["precaptured_contacts"] == 0
        assert out["report"]["usable"] == 1
        # applications row persisted
        conn = get_connection()
        try:
            row = conn.execute("SELECT role_title FROM applications WHERE job_id='ja-1'").fetchone()
        finally:
            conn.close()
        assert row["role_title"] == "Quality Engineer"

    def test_report_surfaces_dropped_postings(self, tmp_db, tmp_path, capsys):
        bad = {"company": "Acme"}  # missing job_id + role_title
        feed = _feed_file(tmp_path, _POSTING, bad)
        run_plan(argparse.Namespace(verb="plan", feed=feed))
        out = json.loads(capsys.readouterr().out)
        assert len(out["postings"]) == 1
        assert out["report"]["dropped"]["invalid"] == 1

    def test_reruns_upsert_not_duplicate(self, tmp_db, tmp_path, capsys):
        feed = _feed_file(tmp_path, _POSTING)
        run_plan(argparse.Namespace(verb="plan", feed=feed))
        run_plan(argparse.Namespace(verb="plan", feed=feed))
        conn = get_connection()
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        finally:
            conn.close()
        assert n == 1

    def test_malformed_feed_reports_error(self, tmp_db, tmp_path, capsys):
        p = tmp_path / "feed.json"
        p.write_text("{not json", encoding="utf-8")
        rc = run_plan(argparse.Namespace(verb="plan", feed=str(p)))
        assert rc == 1
        assert "parse failed" in json.loads(capsys.readouterr().out)["error"]

    def test_precaptured_contacts_counted(self, tmp_db, tmp_path, capsys):
        posting = dict(_POSTING, contacts=[{"full_name": "Jane", "company_slug": "joby-aviation"}])
        run_plan(argparse.Namespace(verb="plan", feed=_feed_file(tmp_path, posting)))
        out = json.loads(capsys.readouterr().out)
        assert out["postings"][0]["precaptured_contacts"] == 1


# ---------------------------------------------------------------------------
# link
# ---------------------------------------------------------------------------


def _seed_contact(slug="joby-aviation", name="Jane Doe", url="https://linkedin.com/in/jane"):
    with with_writer() as conn:
        cid = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, 'Joby', 'FOUND')", (slug,)
        ).lastrowid
        conn.execute(
            "INSERT INTO contacts (company_id, full_name, linkedin_url) VALUES (?, ?, ?)",
            (cid, name, url),
        )
        conn.execute(
            "INSERT INTO applications (job_id, company, role_title) VALUES ('ja-1', 'Joby', 'QE')"
        )


class TestLink:
    def test_links_raw_candidate_dicts(self, tmp_db, monkeypatch, capsys):
        _seed_contact()
        cand = ContactCandidate(
            full_name="Jane Doe", company_slug="joby-aviation",
            linkedin_url="https://linkedin.com/in/jane",
        )
        _stdin(monkeypatch, json.dumps([cand.model_dump(mode="json")]))
        rc = run_link(argparse.Namespace(verb="link", job_id="ja-1", slug="joby-aviation"))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out == {"job_id": "ja-1", "linked": 1, "unresolved": 0}

    def test_accepts_discover_shaped_items(self, tmp_db, monkeypatch, capsys):
        _seed_contact()
        cand = ContactCandidate(
            full_name="Jane Doe", company_slug="joby-aviation",
            linkedin_url="https://linkedin.com/in/jane",
        )
        _stdin(monkeypatch, json.dumps([{"candidate": cand.model_dump(mode="json")}]))
        rc = run_link(argparse.Namespace(verb="link", job_id="ja-1", slug="joby-aviation"))
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["linked"] == 1

    def test_missing_ids(self, capsys):
        rc = run_link(argparse.Namespace(verb="link", job_id="", slug="joby"))
        assert rc == 1
        assert "missing job_id or slug" in json.loads(capsys.readouterr().out)["error"]

    def test_bad_json(self, monkeypatch, capsys):
        _stdin(monkeypatch, "{not json")
        rc = run_link(argparse.Namespace(verb="link", job_id="ja-1", slug="joby"))
        assert rc == 1
        assert "invalid JSON" in json.loads(capsys.readouterr().out)["error"]

    def test_not_a_list(self, monkeypatch, capsys):
        _stdin(monkeypatch, json.dumps({"candidate": {}}))
        rc = run_link(argparse.Namespace(verb="link", job_id="ja-1", slug="joby"))
        assert rc == 1
        assert "must be a JSON list" in json.loads(capsys.readouterr().out)["error"]

    def test_invalid_candidate_surfaced(self, monkeypatch, capsys):
        _stdin(monkeypatch, json.dumps([{"title": "no name"}]))  # missing required full_name
        rc = run_link(argparse.Namespace(verb="link", job_id="ja-1", slug="joby"))
        assert rc == 1
        assert "invalid candidate" in json.loads(capsys.readouterr().out)["error"]


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_plan(tmp_db, tmp_path, capsys, monkeypatch):
    feed = _feed_file(tmp_path, _POSTING)
    assert run_jobs_host(argparse.Namespace(verb="plan", feed=feed)) == 0


def test_dispatch_link(tmp_db, monkeypatch, capsys):
    _seed_contact()
    _stdin(monkeypatch, json.dumps([]))
    assert run_jobs_host(argparse.Namespace(verb="link", job_id="ja-1", slug="joby-aviation")) == 0
