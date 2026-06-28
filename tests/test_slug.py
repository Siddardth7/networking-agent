"""
tests/test_slug.py
FINDER_AUDIT D8 (#27): one canonical slugify, shared by importer/Serper/Apify,
so the same company never cross-links to two `companies` rows.
"""

from __future__ import annotations

from src.core.slug import slugify


def test_spaces_become_dashes():
    assert slugify("Joby Aviation") == "joby-aviation"


def test_punctuation_collapsed():
    # The exact D8 offender: Serper kept the comma/period, the others stripped
    # them. Both must now agree on this slug.
    assert slugify("Joby Aviation, Inc.") == "joby-aviation-inc"


def test_leading_trailing_separators_trimmed():
    assert slugify("  *GE* ") == "ge"


def test_empty_yields_unknown():
    assert slugify("") == "unknown"
    assert slugify("!!!") == "unknown"


def test_idempotent_on_existing_slug():
    assert slugify("general-electric") == "general-electric"


def test_serper_apify_importer_agree():
    # The three call sites used to diverge; prove they collapse to one value.
    from src.agents import importer  # noqa: PLC0415
    from src.providers import apify, serper  # noqa: PLC0415

    name = "Joby Aviation, Inc."
    assert importer.slugify(name) == apify.slugify(name) == serper.slugify(name) == slugify(name)
