"""
tests/test_finder_scorecard.py
Pure scoring logic for the Finder trial scorecard (#10). The live entrypoint
hits the network and is excluded from coverage; everything here is offline.
"""

from __future__ import annotations

from src.eval.finder_scorecard import ContactRow, score_contacts


def _row(name="A", title="Composites Engineer", persona="PEER_ENGINEER",
         focus="COMPOSITE_DESIGN", hook="your composites work",
         url="https://linkedin.com/in/a", email=None) -> ContactRow:
    return ContactRow(
        full_name=name, title=title, persona=persona, focus_area=focus,
        hook=hook, linkedin_url=url, email=email,
    )


class TestVerdict:
    def test_clean_run_passes(self):
        rows = [_row(name="A", url="https://li.com/a"),
                _row(name="B", hook="led 787 stress team", url="https://li.com/b")]
        card = score_contacts("acme", 5, rows)
        assert card.found == 2
        assert card.hooks_ok
        assert card.verdict == "PASS"

    def test_empty_fails(self):
        card = score_contacts("acme", 5, [])
        assert card.verdict == "FAIL — no contacts discovered"

    def test_generic_hook_blocks_pass(self):
        card = score_contacts("acme", 5, [_row(hook="GENERIC")])
        assert card.generic_hooks == 1
        assert not card.hooks_ok
        assert card.verdict.startswith("REVIEW")

    def test_none_hook_counts_as_generic(self):
        card = score_contacts("acme", 5, [_row(hook=None)])
        assert card.generic_hooks == 1

    def test_verbatim_news_hook_blocks_pass(self):
        card = score_contacts(
            "acme", 5,
            [_row(hook="Acme Reports First Quarter 2026 Financial Results")],
        )
        assert card.verbatim_news_hooks == 1
        assert not card.hooks_ok
        assert card.verdict.startswith("REVIEW")


class TestDistributionsAndFlags:
    def test_persona_focus_distribution(self):
        rows = [
            _row(name="A", persona="PEER_ENGINEER", focus="COMPOSITE_DESIGN"),
            _row(name="B", persona="PEER_ENGINEER", focus="STRUCTURAL_ANALYSIS"),
            _row(name="C", persona="RECRUITER", focus="PEER"),
        ]
        card = score_contacts("acme", 5, rows)
        assert card.by_persona == {"PEER_ENGINEER": 2, "RECRUITER": 1}
        assert card.by_focus["COMPOSITE_DESIGN"] == 1

    def test_email_counted(self):
        card = score_contacts("acme", 5, [_row(email="a@acme.com"), _row(email=None)])
        assert card.with_email == 1

    def test_stale_title_flagged(self):
        card = score_contacts("acme", 5, [_row(name="Old Hand", title="Retired Engineer")])
        assert any("stale" in f for f in card.targeting_flags)

    def test_missing_linkedin_flagged_and_counted(self):
        card = score_contacts("acme", 5, [_row(name="No URL", url=None)])
        assert card.missing_linkedin == 1
        assert any("no LinkedIn" in f for f in card.targeting_flags)


class TestRender:
    def test_markdown_has_scorecard_and_contacts(self):
        rows = [_row(name="Jane Doe", hook="your composites work")]
        md = score_contacts("ast-spacemobile", 5, rows).render_markdown()
        assert "# Finder trial — ast-spacemobile" in md
        assert "Verdict: PASS" in md
        assert "Jane Doe" in md
        assert "| Criterion | Target | Result | Verdict |" in md

    def test_markdown_renders_targeting_flags_section(self):
        md = score_contacts(
            "acme", 5, [_row(name="Old Hand", title="Retired Engineer")]
        ).render_markdown()
        assert "## Targeting flags (for human review)" in md
        assert "Old Hand" in md
