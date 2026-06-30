"""
tests/test_network_timing.py
Timing intelligence (#18, A7): location→timezone mapping + Tue-Thu morning window.

Covers the heuristic (cities, state codes, international, unknown→UTC), the
send-window selection across timezones, and the CLI.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from src.cli.network_timing import (
    location_to_timezone,
    recommend_for_contacts,
    recommend_send_time,
    run_timing,
)
from src.core.db import get_connection, init_db, with_writer


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("src.core.db._DB_PATH", db_path)
    init_db()
    return tmp_path


# --------------------------------------------------------------------------- #
# location_to_timezone
# --------------------------------------------------------------------------- #


class TestLocationToTimezone:
    def test_none_is_utc(self):
        assert location_to_timezone(None) == UTC

    def test_empty_is_utc(self):
        assert location_to_timezone("") == UTC

    def test_unknown_is_utc(self):
        assert location_to_timezone("Atlantis") == UTC

    def test_us_state_code_token(self):
        assert location_to_timezone("Dayton, OH") == ZoneInfo("America/New_York")

    def test_state_code_not_matched_inside_word(self):
        # 'ca' must not fire inside 'Chicago' — token match, not substring.
        assert location_to_timezone("Chicago, IL") == ZoneInfo("America/Chicago")

    @pytest.mark.parametrize(
        "loc,zone",
        [
            ("Plano, TX", "America/Chicago"),  # city unknown → TX token
            ("Reno, NV", "America/Los_Angeles"),  # city unknown → NV token
            ("Cleveland, OH", "America/New_York"),  # city unknown → OH token
        ],
    )
    def test_state_token_only_when_city_unknown(self, loc, zone):
        # Exercises the token map (no substring city match) and the tokenizer.
        assert location_to_timezone(loc) == ZoneInfo(zone)

    def test_city_wins_over_state_code(self):
        # San Francisco (Pacific) beats the CA token (also Pacific here, but the
        # city rule fires first regardless of order).
        assert location_to_timezone("San Francisco, CA") == ZoneInfo("America/Los_Angeles")

    def test_trailing_delimiter_tokenizes(self):
        # Token extracted cleanly despite the trailing punctuation.
        assert location_to_timezone("NV,") == ZoneInfo("America/Los_Angeles")

    def test_international_city(self):
        assert location_to_timezone("London, England") == ZoneInfo("Europe/London")

    def test_international_country(self):
        assert location_to_timezone("Bengaluru, India") == ZoneInfo("Asia/Kolkata")

    def test_mountain_phoenix(self):
        assert location_to_timezone("Phoenix, AZ") == ZoneInfo("America/Phoenix")

    @pytest.mark.parametrize(
        "loc,zone",
        [
            ("Austin, TX", "America/Chicago"),
            ("Denver, CO", "America/Denver"),
            ("Seattle, WA", "America/Los_Angeles"),
            ("Boston, MA", "America/New_York"),
            ("Tokyo, Japan", "Asia/Tokyo"),
            ("Toronto, Ontario", "America/Toronto"),
        ],
    )
    def test_representative_locations(self, loc, zone):
        assert location_to_timezone(loc) == ZoneInfo(zone)


# --------------------------------------------------------------------------- #
# recommend_send_time
# --------------------------------------------------------------------------- #

# A reference Monday 2026-06-01 12:00 UTC.
MON_NOON_UTC = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class TestRecommendSendTime:
    def test_returns_tuesday_morning(self):
        # From Monday noon UTC, the next Tue-Thu 09:00 ET is Tue 2026-06-02.
        send = recommend_send_time("New York, NY", MON_NOON_UTC)
        assert send.weekday() == 1  # Tuesday
        assert send.hour == 9
        assert send.tzinfo == ZoneInfo("America/New_York")

    def test_window_is_tue_wed_thu(self):
        send = recommend_send_time("New York, NY", MON_NOON_UTC)
        assert send.weekday() in (1, 2, 3)

    def test_skips_to_next_week_from_friday(self):
        # Friday 2026-06-05 12:00 UTC → next window is Tue 2026-06-09.
        fri = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
        send = recommend_send_time("New York, NY", fri)
        assert send.weekday() == 1
        assert send.date() == datetime(2026, 6, 9).date()

    def test_same_day_before_9am_returns_today(self):
        # Tuesday 2026-06-02 06:00 ET (=10:00 UTC) → today 09:00 ET still ahead.
        tue_early = datetime(2026, 6, 2, 10, 0, tzinfo=UTC)
        send = recommend_send_time("New York, NY", tue_early)
        assert send.date() == datetime(2026, 6, 2).date()
        assert send.hour == 9

    def test_same_day_after_9am_rolls_forward(self):
        # Tuesday 2026-06-02 18:00 UTC (=14:00 ET) → past 09:00, roll to Wed.
        tue_late = datetime(2026, 6, 2, 18, 0, tzinfo=UTC)
        send = recommend_send_time("New York, NY", tue_late)
        assert send.weekday() == 2  # Wednesday
        assert send.date() == datetime(2026, 6, 3).date()

    def test_local_time_differs_by_timezone(self):
        # Same instant, two coasts → both 09:00 *local*, different UTC offsets.
        et = recommend_send_time("New York, NY", MON_NOON_UTC)
        pt = recommend_send_time("San Francisco, CA", MON_NOON_UTC)
        assert et.hour == pt.hour == 9
        assert et.utcoffset() != pt.utcoffset()

    def test_unknown_location_uses_utc(self):
        send = recommend_send_time(None, MON_NOON_UTC)
        assert send.tzinfo == UTC
        assert send.hour == 9
        assert send.weekday() in (1, 2, 3)


# --------------------------------------------------------------------------- #
# recommend_for_contacts + dispatch
# --------------------------------------------------------------------------- #


def _seed_contact(name: str, location: str | None, slug: str = "acme") -> int:
    with with_writer() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (slug, name, state) VALUES (?, ?, 'FOUND')",
            (slug, slug.title()),
        )
        co = conn.execute("SELECT id FROM companies WHERE slug=?", (slug,)).fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO contacts (company_id, full_name, location) VALUES (?, ?, ?)",
            (co, name, location),
        )
        return int(cur.lastrowid)


class TestRecommendForContacts:
    def test_empty(self, capsys):
        assert recommend_for_contacts(now=MON_NOON_UTC) == 0
        assert "No contacts yet." in capsys.readouterr().out

    def test_lists_each_contact_with_window(self, capsys):
        _seed_contact("Alice Smith", "Dayton, OH")
        _seed_contact("Bob Lee", "San Francisco, CA")
        assert recommend_for_contacts(now=MON_NOON_UTC) == 0
        out = capsys.readouterr().out
        assert "Alice Smith @ acme" in out
        assert "Bob Lee @ acme" in out
        assert "send Tue" in out  # both land Tuesday morning

    def test_unknown_location_label(self, capsys):
        _seed_contact("Carol Null", None)
        recommend_for_contacts(now=MON_NOON_UTC)
        assert "unknown→UTC" in capsys.readouterr().out

    def test_default_now_path(self):
        # Exercise the now=None branch (uses real clock); just assert success.
        _seed_contact("Dave Now", "Boston, MA")
        assert recommend_for_contacts() == 0

    def test_dispatch(self, capsys):
        run_timing(argparse.Namespace())
        assert "No contacts yet." in capsys.readouterr().out


def test_persisted_location_round_trips():
    cid = _seed_contact("Eve Loc", "Austin, TX")
    conn = get_connection()
    try:
        row = conn.execute("SELECT location FROM contacts WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()
    assert row["location"] == "Austin, TX"
