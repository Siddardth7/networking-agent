"""
tests/test_artifact_writer.py
Tests for src/agents/artifact_writer.py — Step 7.3 exit gate.

Exit criteria:
  1. Artifact file exists at expected path (<slug>/<YYYY-MM-DD>-run.md) inside
     a tmp_path-injected output dir.
  2. All 3 channels appear in the file per contact.
  3. Company state = APPROVED after write.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from src.agents.artifact_writer import write_artifact
from src.core.db import get_connection, init_db, with_writer
from src.core.schemas import Channel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the DB to a temp file and initialise the schema."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    return db_path


def _seed_company_with_contacts(
    slug: str = "acme-corp",
    name: str = "Acme Corp",
    company_state: str = "DRAFTED",
    num_contacts: int = 2,
    contact_state: str = "DRAFTED",
    add_drafts: bool = True,
) -> tuple[int, list[int]]:
    """Insert a company + contacts + (optionally) one draft per channel.

    Returns (company_id, contact_ids).
    """
    with with_writer() as conn:
        cur = conn.execute(
            "INSERT INTO companies (slug, name, state) VALUES (?, ?, ?)",
            (slug, name, company_state),
        )
        company_id = cur.lastrowid

        contact_ids: list[int] = []
        for i in range(num_contacts):
            cur = conn.execute(
                """INSERT INTO contacts
                   (company_id, full_name, title, linkedin_url, email, hook, state,
                    persona, focus_area)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'PEER_ENGINEER', 'COMPOSITE_DESIGN')""",
                (
                    company_id,
                    f"Engineer {i + 1}",
                    f"Composites Engineer {i + 1}",
                    f"https://linkedin.com/in/eng{i + 1}",
                    f"eng{i + 1}@acme.com",
                    "great composites work",
                    contact_state,
                ),
            )
            contact_ids.append(cur.lastrowid)

        if add_drafts:
            for cid in contact_ids:
                for channel in Channel:
                    subject = "Test subject" if channel == Channel.COLD_EMAIL else None
                    body = f"Draft body for {channel.value} — contact {cid}"
                    conn.execute(
                        "INSERT INTO drafts (contact_id, channel, body, "
                        "subject, version, quality_flag) "
                        "VALUES (?, ?, ?, ?, 1, 0)",
                        (cid, channel.value, body, subject),
                    )

    return company_id, contact_ids


# ---------------------------------------------------------------------------
# Test 1: File exists at the expected path
# ---------------------------------------------------------------------------


class TestArtifactFilePath:
    def test_file_created_at_expected_path(self, tmp_path: Path) -> None:
        """Artifact must be at <output_dir>/<slug>/<YYYY-MM-DD>-run.md."""
        company_id, _ = _seed_company_with_contacts(slug="acme-corp")
        output_dir = tmp_path / "drafts"

        result_path = write_artifact(company_id, _output_dir=output_dir)

        today = datetime.date.today().isoformat()
        expected = output_dir / "acme-corp" / f"{today}-run.md"
        assert result_path == expected
        assert result_path.exists(), f"Artifact file not found at {result_path}"

    def test_returns_path_object(self, tmp_path: Path) -> None:
        company_id, _ = _seed_company_with_contacts(slug="beta-corp")
        output_dir = tmp_path / "drafts"

        result = write_artifact(company_id, _output_dir=output_dir)

        assert isinstance(result, Path)

    def test_file_is_not_empty(self, tmp_path: Path) -> None:
        company_id, _ = _seed_company_with_contacts(slug="gamma-corp")
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)

        assert path.stat().st_size > 0

    def test_slug_directory_created(self, tmp_path: Path) -> None:
        """The per-slug subdirectory must be created even if it didn't exist."""
        company_id, _ = _seed_company_with_contacts(slug="new-company")
        output_dir = tmp_path / "drafts"

        write_artifact(company_id, _output_dir=output_dir)

        assert (output_dir / "new-company").is_dir()

    def test_invalid_company_id_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="No company found"):
            write_artifact(99999, _output_dir=tmp_path / "drafts")


# ---------------------------------------------------------------------------
# Test 2: All 3 channels appear in the file per contact
# ---------------------------------------------------------------------------


class TestArtifactContent:
    def test_all_three_channels_present(self, tmp_path: Path) -> None:
        """LINKEDIN_CONNECTION, LINKEDIN_POST_CONNECTION, COLD_EMAIL must appear."""
        company_id, _ = _seed_company_with_contacts(num_contacts=1)
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        # Check for label text (from _CHANNEL_LABELS)
        assert "LinkedIn Connection Request" in content
        assert "LinkedIn Post-Connection Message" in content
        assert "Cold Email" in content

    def test_all_three_channels_per_contact_with_two_contacts(self, tmp_path: Path) -> None:
        """With 2 contacts, each contact's 3 channel drafts must be present."""
        company_id, contact_ids = _seed_company_with_contacts(num_contacts=2)
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        # Each contact name must appear
        assert "Engineer 1" in content
        assert "Engineer 2" in content

        # Draft bodies for each contact × channel must be in the file
        for cid in contact_ids:
            for channel in Channel:
                draft_body = f"Draft body for {channel.value} — contact {cid}"
                assert draft_body in content, (
                    f"Expected draft body for contact {cid}, channel {channel.value} "
                    f"not found in artifact"
                )

    def test_channel_labels_appear_in_correct_order(self, tmp_path: Path) -> None:
        """Channels must appear in Channel enum order within each contact section."""
        company_id, _ = _seed_company_with_contacts(num_contacts=1)
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        pos_lc = content.index("LinkedIn Connection Request")
        pos_lpc = content.index("LinkedIn Post-Connection Message")
        pos_ce = content.index("Cold Email")
        assert pos_lc < pos_lpc < pos_ce

    def test_company_header_present(self, tmp_path: Path) -> None:
        company_id, _ = _seed_company_with_contacts(name="Acme Corp", slug="acme-corp")
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        assert "Acme Corp" in content
        assert "acme-corp" in content

    def test_contact_metadata_present(self, tmp_path: Path) -> None:
        """Name, title, linkedin_url, email, and hook must appear in the artifact."""
        company_id, _ = _seed_company_with_contacts(num_contacts=1)
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        assert "Engineer 1" in content
        assert "Composites Engineer 1" in content
        assert "https://linkedin.com/in/eng1" in content
        assert "eng1@acme.com" in content
        assert "great composites work" in content

    def test_cold_email_subject_in_artifact(self, tmp_path: Path) -> None:
        """Cold email subject must appear before the body block."""
        company_id, _ = _seed_company_with_contacts(num_contacts=1)
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        assert "Test subject" in content

    def test_draft_bodies_in_code_blocks(self, tmp_path: Path) -> None:
        """Draft bodies must be wrapped in fenced code blocks (```)."""
        company_id, _ = _seed_company_with_contacts(num_contacts=1)
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        # There should be at least 3 code fences (one per channel)
        fence_count = content.count("```")
        # Each code block uses 2 fences; 3 channels × 2 fences = 6
        assert fence_count >= 6, f"Expected ≥6 code fences, got {fence_count}"

    def test_latest_version_draft_used(self, tmp_path: Path) -> None:
        """When multiple versions exist, the highest version draft must be used."""
        company_id, contact_ids = _seed_company_with_contacts(num_contacts=1)
        cid = contact_ids[0]

        # Add version 2 for LINKEDIN_CONNECTION
        with with_writer() as conn:
            conn.execute(
                "INSERT INTO drafts (contact_id, channel, body, version, quality_flag) "
                "VALUES (?, ?, ?, 2, 0)",
                (cid, Channel.LINKEDIN_CONNECTION.value, "Version 2 body — updated"),
            )

        output_dir = tmp_path / "drafts"
        path = write_artifact(company_id, _output_dir=output_dir)
        content = path.read_text(encoding="utf-8")

        assert "Version 2 body — updated" in content
        # The original v1 body should NOT appear (superseded)
        v1_body = f"Draft body for {Channel.LINKEDIN_CONNECTION.value} — contact {cid}"
        assert v1_body not in content

    def test_no_contacts_produces_graceful_output(self, tmp_path: Path) -> None:
        """A company with no contacts must still produce a valid file."""
        with with_writer() as conn:
            cur = conn.execute(
                "INSERT INTO companies (slug, name, state) "
                "VALUES ('empty-co', 'Empty Co', 'DRAFTED')"
            )
            company_id = cur.lastrowid

        output_dir = tmp_path / "drafts"
        path = write_artifact(company_id, _output_dir=output_dir)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Empty Co" in content

    def test_run_date_in_artifact_and_filename(self, tmp_path: Path) -> None:
        """The today ISO date must appear in both the filename and the artifact body."""
        company_id, _ = _seed_company_with_contacts()
        output_dir = tmp_path / "drafts"

        path = write_artifact(company_id, _output_dir=output_dir)
        today = datetime.date.today().isoformat()

        assert today in path.name
        content = path.read_text(encoding="utf-8")
        assert today in content


# ---------------------------------------------------------------------------
# Test 3: Company state = APPROVED after write
# ---------------------------------------------------------------------------


class TestCompanyStateTransition:
    def test_company_state_becomes_approved(self, tmp_path: Path) -> None:
        """write_artifact must transition company state DRAFTED → APPROVED."""
        company_id, _ = _seed_company_with_contacts(company_state="DRAFTED")
        output_dir = tmp_path / "drafts"

        write_artifact(company_id, _output_dir=output_dir)

        conn = get_connection()
        try:
            row = conn.execute("SELECT state FROM companies WHERE id = ?", (company_id,)).fetchone()
        finally:
            conn.close()

        assert row["state"] == "APPROVED"

    def test_company_state_approved_even_if_already_approved(self, tmp_path: Path) -> None:
        """write_artifact is idempotent — calling twice keeps state APPROVED."""
        company_id, _ = _seed_company_with_contacts(company_state="DRAFTED")
        output_dir = tmp_path / "drafts"

        write_artifact(company_id, _output_dir=output_dir)
        write_artifact(company_id, _output_dir=output_dir)  # second call

        conn = get_connection()
        try:
            row = conn.execute("SELECT state FROM companies WHERE id = ?", (company_id,)).fetchone()
        finally:
            conn.close()

        assert row["state"] == "APPROVED"

    def test_multiple_companies_only_target_approved(self, tmp_path: Path) -> None:
        """Only the target company must be transitioned; others are untouched."""
        company_id, _ = _seed_company_with_contacts(slug="target-co")
        # Insert a second company
        with with_writer() as conn:
            conn.execute(
                "INSERT INTO companies (slug, name, state) "
                "VALUES ('other-co', 'Other Co', 'DRAFTED')"
            )

        output_dir = tmp_path / "drafts"
        write_artifact(company_id, _output_dir=output_dir)

        conn = get_connection()
        try:
            target_state = conn.execute(
                "SELECT state FROM companies WHERE id = ?", (company_id,)
            ).fetchone()["state"]
            other_state = conn.execute(
                "SELECT state FROM companies WHERE slug = 'other-co'"
            ).fetchone()["state"]
        finally:
            conn.close()

        assert target_state == "APPROVED"
        assert other_state == "DRAFTED"  # unchanged

    def test_print_outputs_artifact_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """write_artifact must print the artifact path to stdout."""
        company_id, _ = _seed_company_with_contacts(slug="print-co")
        output_dir = tmp_path / "drafts"

        result_path = write_artifact(company_id, _output_dir=output_dir)

        captured = capsys.readouterr()
        assert str(result_path) in captured.out
