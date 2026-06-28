"""
src/core/slug.py
One canonical company-name → slug. FINDER_AUDIT D8 (#27): the importer, Apify,
and Serper each slugified differently, so "Joby Aviation, Inc." became
"joby-aviation-inc" in two places and "joby-aviation,-inc." in Serper — the
same company cross-linked to two `companies` rows. Everyone calls this now.
"""

from __future__ import annotations

import re

__all__ = ["slugify"]


def slugify(name: str) -> str:
    """Lowercase *name*, collapse every run of non-alphanumerics to a single
    dash, and trim leading/trailing dashes. Empty input yields ``"unknown"``.

    Examples: ``"Joby Aviation, Inc."`` → ``"joby-aviation-inc"``;
    ``"GE"`` → ``"ge"``. No side effects.
    """
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "unknown"
