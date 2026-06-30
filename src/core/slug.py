"""
src/core/slug.py
One canonical company-name → slug. FINDER_AUDIT D8 (#27): the importer, Apify,
and Serper each slugified differently, so "Joby Aviation, Inc." became
"joby-aviation-inc" in two places and "joby-aviation,-inc." in Serper — the
same company cross-linked to two `companies` rows. Everyone calls this now.
"""

from __future__ import annotations

import re

__all__ = ["slugify", "canonical_linkedin_url"]


def slugify(name: str) -> str:
    """Lowercase *name*, collapse every run of non-alphanumerics to a single
    dash, and trim leading/trailing dashes. Empty input yields ``"unknown"``.

    Examples: ``"Joby Aviation, Inc."`` → ``"joby-aviation-inc"``;
    ``"GE"`` → ``"ge"``. No side effects.
    """
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "unknown"


def canonical_linkedin_url(url: str | None) -> str:
    """Normalize a LinkedIn URL to a stable cross-source dedup key (#24).

    Drops the scheme, a leading ``www.``, any ``?query``/``#fragment``, and the
    trailing slash, then lowercases — so the SAME person arriving from two
    sources (``https://www.linkedin.com/in/jane/`` from Apify vs
    ``http://linkedin.com/in/jane?utm=x`` from Apollo) collapses to one key
    (``linkedin.com/in/jane``). Falsy input → ``""`` (the caller falls back to
    name+company). No side effects.
    """
    if not url:
        return ""
    u = str(url).strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.split("?", 1)[0].split("#", 1)[0]
    u = u.removeprefix("www.")
    return u.rstrip("/")
