"""
src/eval/finder_scorecard.py
Finder-output scorecard (issue #10): the live-trial quality bar that closes
"develop the Finder like the Drafter", mirroring the AST SpaceMobile drafter
trial (docs/TRIAL_AST_SPACEMOBILE_2026-06-13.md).

The Finder is Discover → Enrich → Classify → Hook → Save. This scores its
*output* on objective, ground-truth-free checks — the same shape the AST trial
used for drafts (0 fabricated, 0 placeholder, hooks pass the whitelist):

  - discovery yield   (found vs the requested limit)
  - hook quality      (GENERIC / verbatim-news / whitelist-pass — the #9 bar)
  - classify spread   (persona / focus distribution — sanity, not accuracy;
                       accuracy is the labeled-set scorecard, issue #4)
  - targeting flags    (retired/former titles, missing LinkedIn — for the human)

The scoring is pure and unit-tested. The live entrypoint (``# pragma: no cover``)
runs ``find_contacts`` and reads the rows back — it hits the network, so only
the scoring logic is covered, matching ``classify_scorecard``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.agents.finder import is_acceptable_hook, looks_like_verbatim_news

__all__ = ["ContactRow", "FinderScorecard", "score_contacts", "run_finder_trial"]

# Titles that signal the person is not a current, reachable employee — the AST
# trial flagged one retired contact by hand; this catches the obvious cases.
_STALE_TITLE_RE = re.compile(r"(?i)\b(retired|former|ex-|emeritus|seeking|open to work)\b")


@dataclass
class ContactRow:
    """The subset of a saved contact the scorecard reads (DB row or candidate)."""

    full_name: str
    title: str | None = None
    persona: str | None = None
    focus_area: str | None = None
    hook: str | None = None
    linkedin_url: str | None = None
    email: str | None = None


@dataclass
class FinderScorecard:
    company_slug: str
    limit: int
    rows: list[ContactRow]
    by_persona: dict[str, int] = field(default_factory=dict)
    by_focus: dict[str, int] = field(default_factory=dict)
    generic_hooks: int = 0
    verbatim_news_hooks: int = 0
    whitelist_pass: int = 0
    missing_linkedin: int = 0
    with_email: int = 0
    targeting_flags: list[str] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.rows)

    @property
    def hooks_ok(self) -> bool:
        """The #9 hook bar: no GENERIC, no verbatim news, every hook whitelisted."""
        return (
            self.found > 0
            and self.generic_hooks == 0
            and self.verbatim_news_hooks == 0
            and self.whitelist_pass == self.found
        )

    @property
    def verdict(self) -> str:
        # Parity bar = the AST hook criteria. Discovery yield and classify spread
        # are reported for the reviewer; only the hook bar gates PASS, because
        # classify *accuracy* is owned by the labeled-set scorecard (#4) and
        # discovery *count* depends on the company, not Finder quality.
        if self.found == 0:
            return "FAIL — no contacts discovered"
        return "PASS" if self.hooks_ok else "REVIEW — hook bar not met"

    def render_markdown(self) -> str:
        return _render_markdown(self)


def score_contacts(company_slug: str, limit: int, rows: list[ContactRow]) -> FinderScorecard:
    """Score a list of discovered contacts. Pure — no DB, no network."""
    card = FinderScorecard(company_slug=company_slug, limit=limit, rows=rows)
    for r in rows:
        card.by_persona[r.persona or "?"] = card.by_persona.get(r.persona or "?", 0) + 1
        card.by_focus[r.focus_area or "?"] = card.by_focus.get(r.focus_area or "?", 0) + 1

        hook = r.hook
        if not hook or hook == "GENERIC":
            card.generic_hooks += 1
        else:
            if looks_like_verbatim_news(hook):
                card.verbatim_news_hooks += 1
            if is_acceptable_hook(hook):
                card.whitelist_pass += 1

        if not r.linkedin_url:
            card.missing_linkedin += 1
        if r.email:
            card.with_email += 1
        if r.title and _STALE_TITLE_RE.search(r.title):
            card.targeting_flags.append(f"{r.full_name}: stale/unreachable title — '{r.title}'")
        if not r.linkedin_url:
            card.targeting_flags.append(f"{r.full_name}: no LinkedIn URL (can't connect)")
    return card


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.0f}%" if d else "—"


def _render_markdown(card: FinderScorecard) -> str:
    n = card.found
    lines = [
        f"# Finder trial — {card.company_slug}",
        "",
        f"Live Finder run, LinkedIn-only (email enrichment off). "
        f"Requested {card.limit}, discovered **{n}**.",
        "",
        "## Scorecard (parity bar = the AST drafter trial's hook criteria)",
        "",
        "| Criterion | Target | Result | Verdict |",
        "|---|---|---|---|",
        f"| Discovery yield | > 0 | **{n}/{card.limit}** | "
        f"{'PASS' if n else 'FAIL'} |",
        f"| GENERIC hooks | 0 | **{card.generic_hooks}/{n}** | "
        f"{'PASS' if card.generic_hooks == 0 else 'FAIL'} |",
        f"| Verbatim-news hooks | 0 | **{card.verbatim_news_hooks}/{n}** | "
        f"{'PASS' if card.verbatim_news_hooks == 0 else 'FAIL'} |",
        f"| Hook whitelist | all pass | **{card.whitelist_pass}/{n}** "
        f"({_pct(card.whitelist_pass, n)}) | "
        f"{'PASS' if card.whitelist_pass == n and n else 'FAIL'} |",
        f"| Missing LinkedIn | low | **{card.missing_linkedin}/{n}** | info |",
        "",
        f"**Verdict: {card.verdict}**",
        "",
        "## Classify spread (reported, not gated — accuracy is the #4 labeled scorecard)",
        "",
        f"- Persona: {_fmt_dist(card.by_persona)}",
        f"- Focus:   {_fmt_dist(card.by_focus)}",
        "",
    ]
    if card.targeting_flags:
        lines.append("## Targeting flags (for human review)")
        lines.append("")
        lines += [f"- {f}" for f in card.targeting_flags]
        lines.append("")
    lines.append("## Contacts")
    lines.append("")
    lines.append("| Name | Title | Persona | Focus | Hook |")
    lines.append("|---|---|---|---|---|")
    for r in card.rows:
        lines.append(
            f"| {r.full_name} | {r.title or '—'} | {r.persona or '—'} | "
            f"{r.focus_area or '—'} | {r.hook or 'GENERIC'} |"
        )
    return "\n".join(lines) + "\n"


def _fmt_dist(d: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or "—"


def run_finder_trial(  # pragma: no cover - hits the network
    company_slug: str,
    limit: int = 15,
    *,
    location: str | None = None,
) -> FinderScorecard:
    """Run the live Finder pipeline for *company_slug* and score the result.

    LinkedIn-only: no email providers are injected and the enable-email toggle
    defaults off, so this spends zero Hunter/Apollo quota (matching the AST
    trial). Spends: Apify discovery (billed per page), one Serper news call, and
    one Haiku classify per contact.
    """
    from src.agents.finder import find_contacts
    from src.core.db import get_connection, init_db

    init_db()
    find_contacts(company_slug, limit=limit, location=location)

    conn = get_connection()
    try:
        db_rows = conn.execute(
            "SELECT c.full_name, c.title, c.persona, c.focus_area, c.hook, "
            "c.linkedin_url, c.email "
            "FROM contacts c JOIN companies co ON co.id = c.company_id "
            "WHERE co.slug = ? ORDER BY c.id",
            (company_slug,),
        ).fetchall()
    finally:
        conn.close()

    rows = [
        ContactRow(
            full_name=r["full_name"],
            title=r["title"],
            persona=r["persona"],
            focus_area=r["focus_area"],
            hook=r["hook"],
            linkedin_url=r["linkedin_url"],
            email=r["email"],
        )
        for r in db_rows
    ]
    return score_contacts(company_slug, limit, rows)
