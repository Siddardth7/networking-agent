"""
src/cli/network_timing.py
Timing intelligence — recommend a per-contact send window (issue #18, A7).

Outreach lands best on Tue-Thu mornings in the *recipient's* local timezone
(~+8% over a random send). The contact's location is persisted (migration 008);
this module maps that location to an IANA timezone with a keyword heuristic
(stdlib ``zoneinfo``, no geocoder), then returns the next Tue/Wed/Thu at 09:00
local at or after "now".

The pure :func:`location_to_timezone` and :func:`recommend_send_time` carry the
logic (testable across timezones offline); the CLI joins them to the contacts
table.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core.db import get_connection, init_db

__all__ = [
    "location_to_timezone",
    "recommend_send_time",
    "recommend_for_contacts",
    "run_timing",
]

_UTC = UTC
_SEND_HOUR = 9  # 09:00 local — the morning window
_SEND_DAYS = frozenset({1, 2, 3})  # Mon=0 … so Tue, Wed, Thu

# ponytail: keyword heuristic, not a geocoder. Multi-word keys (cities,
# countries, regions) are matched as substrings of the lowercased location;
# 2-letter US state / country codes are matched as whole word-tokens so "ca"
# doesn't fire inside "Chicago". Anything unmatched falls back to UTC. Extend
# the maps as new campaign locations show up — the upgrade path is a real
# geocoder if this ever gets noisy.
_SUBSTRING_TZ: dict[str, str] = {
    # US cities / regions
    "new york": "America/New_York",
    "boston": "America/New_York",
    "atlanta": "America/New_York",
    "miami": "America/New_York",
    "washington": "America/New_York",
    "dayton": "America/New_York",
    "cincinnati": "America/New_York",
    "pittsburgh": "America/New_York",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "austin": "America/Chicago",
    "houston": "America/Chicago",
    "denver": "America/Denver",
    "phoenix": "America/Phoenix",
    "salt lake": "America/Denver",
    "seattle": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "bay area": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "san diego": "America/Los_Angeles",
    "san jose": "America/Los_Angeles",
    # International cities / countries
    "london": "Europe/London",
    "united kingdom": "Europe/London",
    "england": "Europe/London",
    "paris": "Europe/Paris",
    "france": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "singapore": "Asia/Singapore",
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "sydney": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "tel aviv": "Asia/Jerusalem",
    "israel": "Asia/Jerusalem",
    "toronto": "America/Toronto",
    "ontario": "America/Toronto",
    "vancouver": "America/Vancouver",
}
_TOKEN_TZ: dict[str, str] = {
    # US Eastern
    "ny": "America/New_York", "ma": "America/New_York", "fl": "America/New_York",
    "ga": "America/New_York", "oh": "America/New_York", "pa": "America/New_York",
    "dc": "America/New_York", "va": "America/New_York", "nc": "America/New_York",
    "ct": "America/New_York", "nj": "America/New_York", "mi": "America/New_York",
    # US Central
    "tx": "America/Chicago", "il": "America/Chicago", "mn": "America/Chicago",
    "mo": "America/Chicago", "wi": "America/Chicago", "la": "America/Chicago",
    # US Mountain
    "co": "America/Denver", "ut": "America/Denver", "nm": "America/Denver",
    "az": "America/Phoenix",
    # US Pacific
    "ca": "America/Los_Angeles", "wa": "America/Los_Angeles",
    "or": "America/Los_Angeles", "nv": "America/Los_Angeles",
    # Country codes
    "uk": "Europe/London", "us": "America/New_York",
}


def _load_zone(name: str) -> ZoneInfo | timezone:
    """Load an IANA zone, falling back to UTC if the tz database lacks it."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover - only on a tz-db-less host
        return _UTC


def location_to_timezone(location: str | None) -> ZoneInfo | timezone:
    """Map a free-text location to a timezone. UTC when unknown. Pure.

    City/country/region names win over 2-letter codes (more specific), so
    "San Francisco, CA" resolves Pacific via the city, not the state code.
    """
    if not location:
        return _UTC
    text = location.lower()
    for key, tz in _SUBSTRING_TZ.items():
        if key in text:
            return _load_zone(tz)
    # Whole-word alpha tokens, e.g. "dayton, oh" → {"dayton", "oh"} — so a
    # 2-letter code only matches a standalone token, never inside a word.
    tokens = set(re.findall(r"[a-z]+", text))
    for key, tz in _TOKEN_TZ.items():
        if key in tokens:
            return _load_zone(tz)
    return _UTC


def recommend_send_time(location: str | None, now: datetime) -> datetime:
    """Next Tue/Wed/Thu at 09:00 in *location*'s local tz, at or after *now*. Pure.

    *now* must be timezone-aware. The result is tz-aware in the local zone.
    """
    tz = location_to_timezone(location)
    local_now = now.astimezone(tz)
    for add in range(8):  # a Tue-Thu 09:00 always lands within 7 days
        day = datetime.combine((local_now + timedelta(days=add)).date(), time(_SEND_HOUR), tz)
        if day.weekday() in _SEND_DAYS and day >= local_now:
            return day
    raise AssertionError("unreachable: no Tue-Thu window within 8 days")  # pragma: no cover


def recommend_for_contacts(now: datetime | None = None) -> int:
    """Print a recommended send time for every contact. Returns an exit code."""
    when = now or datetime.now(_UTC)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.full_name, c.location, co.slug AS company_slug
            FROM contacts c
            LEFT JOIN companies co ON co.id = c.company_id
            ORDER BY c.id
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No contacts yet.")
        return 0

    for r in rows:
        send = recommend_send_time(r["location"], when)
        loc = r["location"] or "unknown→UTC"
        company = r["company_slug"] or "?"
        print(
            f"{r['full_name']} @ {company} (id={r['id']}) — {loc}: "
            f"send {send:%a %Y-%m-%d %H:%M %Z}"
        )
    return 0


def run_timing(args: argparse.Namespace) -> int:
    """Dispatch — currently a single mode: recommend send times for all contacts."""
    init_db()  # idempotent — safe if this is the first command a fresh user runs
    return recommend_for_contacts()


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Recommend per-contact send windows in local time (#18)."
    )
    sys.exit(run_timing(parser.parse_args()))
