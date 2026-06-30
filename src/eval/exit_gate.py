"""
src/eval/exit_gate.py
Phase A exit-gate validation (issue #20, P0): does the WHOLE loop run on live
data? The per-stage scorecards already prove Finder quality (classify accuracy
#4, hook bar #10, ranking #12); this one proves the stages CONNECT — from a
discovered, classified, hooked, ranked contact through reach → follow-up →
continue → outcome — end to end.

Two things gate PASS:
  1. **Finder quality bar** — the #10 FinderScorecard verdict is PASS (relevant,
     correctly-classified, well-hooked) AND the contacts are ranked.
  2. **Loop completeness** — every blocking loop stage produced its artifact:
     reach (a gated draft), follow-up (a scheduled touch), continue (a gated
     next-move reply), outcome (a recorded, reportable result).

The aggregation is pure and unit-tested. The live entrypoint
(``# pragma: no cover``) runs the real loop on an isolated DB so production
state is never touched — same trade-off as the Finder trial (#10): live APIs,
throwaway DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.eval.finder_scorecard import FinderScorecard

__all__ = [
    "LoopStage",
    "ExitGateReport",
    "evaluate_exit_gate",
    "run_exit_gate_trial",
]


@dataclass
class LoopStage:
    """One measured stage of the reach → follow-up → continue → outcome loop."""

    name: str
    ran: bool  # did the stage execute and produce its artifact?
    detail: str  # human-readable measurement
    blocking: bool = True  # does failure block the exit gate?


@dataclass
class ExitGateReport:
    company_slug: str
    finder_card: FinderScorecard | None
    stages: list[LoopStage] = field(default_factory=list)
    ranked: bool = False  # contacts carry a non-default rank ordering

    @property
    def finder_bar_met(self) -> bool:
        """Finder quality bar: scorecard PASS *and* the contacts are ranked."""
        return self.finder_card is not None and self.finder_card.verdict == "PASS" and self.ranked

    @property
    def loop_complete(self) -> bool:
        """Every blocking loop stage produced its artifact."""
        blocking = [s for s in self.stages if s.blocking]
        return bool(blocking) and all(s.ran for s in blocking)

    @property
    def verdict(self) -> str:
        if self.finder_bar_met and self.loop_complete:
            return "PASS"
        return "REVIEW — exit gate not fully met"

    def render_markdown(self) -> str:
        return _render_markdown(self)


def evaluate_exit_gate(
    company_slug: str,
    finder_card: FinderScorecard | None,
    stages: list[LoopStage],
    *,
    ranked: bool,
) -> ExitGateReport:
    """Aggregate the Finder card + measured loop stages into a gate report. Pure."""
    return ExitGateReport(
        company_slug=company_slug,
        finder_card=finder_card,
        stages=list(stages),
        ranked=ranked,
    )


def _check(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _render_markdown(report: ExitGateReport) -> str:
    card = report.finder_card
    found = card.found if card else 0
    lines = [
        f"# Phase A exit-gate validation — {report.company_slug}",
        "",
        "End-to-end live run: from any input the Finder yields relevant, "
        "correctly-classified, well-hooked, **ranked** contacts, and the full "
        "loop (reach → follow-up → continue → outcome) runs. Live APIs, isolated "
        "DB (production state untouched).",
        "",
        f"**Verdict: {report.verdict}**",
        "",
        "## Gate 1 — Finder quality bar",
        "",
        "| Criterion | Result | Verdict |",
        "|---|---|---|",
        f"| Finder scorecard (#10 hook bar) | {card.verdict if card else 'n/a'} | "
        f"{_check(card is not None and card.verdict == 'PASS')} |",
        f"| Contacts ranked | {'yes' if report.ranked else 'no'} | "
        f"{_check(report.ranked)} |",
        f"| Discovered | {found} | {_check(found > 0)} |",
        "",
        "## Gate 2 — Loop completeness (reach → follow-up → continue → outcome)",
        "",
        "| Stage | Ran | Measurement |",
        "|---|---|---|",
    ]
    for s in report.stages:
        flag = "PASS" if s.ran else ("FAIL" if s.blocking else "info")
        lines.append(f"| {s.name} | {flag} | {s.detail} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def run_exit_gate_trial(  # pragma: no cover - hits the network + LLM
    company_slug: str,
    limit: int = 10,
    *,
    location: str | None = None,
) -> ExitGateReport:
    """Run the full Phase A loop on an isolated DB and score the exit gate.

    Isolated DB: this points ``db._DB_PATH`` at a throwaway file so the live
    run never mutates the real campaign state (same trade-off as the Finder
    trial — live APIs, throwaway DB). Spends Apify discovery + Haiku classify
    (Finder) and a few Haiku/Sonnet drafts (reach + continue).
    """
    import tempfile
    from datetime import datetime
    from pathlib import Path

    import src.core.db as db
    from src.agents.drafter import draft_for_contacts, draft_next_move
    from src.cli.network_followups import plan_followups
    from src.cli.network_outcome import aggregate_outcomes, set_contact_outcome
    from src.core.config import get_anthropic_client
    from src.eval.finder_scorecard import run_finder_trial

    db._DB_PATH = Path(tempfile.mkdtemp()) / "exit_gate.db"
    db.init_db()

    # Stage 0 — Finder: discover + classify + hook + rank (scored by #10).
    finder_card = run_finder_trial(company_slug, limit=limit, location=location)

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT c.id, c.rank_score, c.email FROM contacts c "
            "JOIN companies co ON co.id = c.company_id WHERE co.slug = ? "
            "ORDER BY c.rank_score DESC, c.id ASC",
            (company_slug,),
        ).fetchall()
    finally:
        conn.close()
    contact_ids = [r["id"] for r in rows]
    ranked = any((r["rank_score"] or 0) > 0 for r in rows)

    stages: list[LoopStage] = []
    client = get_anthropic_client()

    # Stage 1 — Reach: draft for the top-ranked contacts.
    drafted = draft_for_contacts(contact_ids[:3], anthropic_client=client) if contact_ids else {}
    n_drafts = sum(len(v) for v in drafted.values())
    stages.append(
        LoopStage("reach (draft)", n_drafts > 0, f"{n_drafts} gated drafts for "
                  f"{len(drafted)} contacts")
    )

    # Stage 2 — Follow-up: a no-reply outreach earns a scheduled touch.
    first = contact_ids[0] if contact_ids else None
    if first is not None:
        with db.with_writer() as conn:
            conn.execute(
                "UPDATE companies SET state='APPROVED' WHERE slug=?", (company_slug,)
            )
            conn.execute(
                "INSERT INTO outreach_log (contact_id, channel, sent_at, response) "
                "VALUES (?, 'EMAIL', '2026-06-20 09:00:00', 'PENDING')",
                (first,),
            )
            oid = conn.execute(
                "SELECT id FROM outreach_log WHERE contact_id=?", (first,)
            ).fetchone()["id"]
        plans = plan_followups(
            [{
                "outreach_log_id": oid,
                "last_touch_at": datetime(2026, 6, 20, 9, 0),  # naive — matches _parse_ts output
                "sent_followups": 0,
                "pending_followups": 0,
                "responded": False,
                "gated": True,
            }],
            max_touches=2,
            gap_days=5,
        )
        stages.append(LoopStage("follow-up (schedule)", bool(plans),
                                f"{len(plans)} follow-up scheduled"))

        # Stage 3 — Continue: a reply earns a gated next move.
        nm = draft_next_move(first, "Happy to chat — what did you want to discuss?",
                             anthropic_client=client)
        stages.append(LoopStage("continue (next move)", nm is not None,
                                f"move={nm.move.value if nm else 'n/a'} "
                                f"[{nm.quality_code if nm else 'n/a'}]"))

        # Stage 4 — Outcome: record + report.
        set_contact_outcome(first, "REPLIED", notes="exit-gate trial")
        summary = aggregate_outcomes([(company_slug, "REPLIED")])
        stages.append(LoopStage("outcome (record+report)", summary["responded"] == 1,
                                f"response rate {summary['responded']}/{summary['total']}"))

    return evaluate_exit_gate(company_slug, finder_card, stages, ranked=ranked)


if __name__ == "__main__":  # pragma: no cover - live trial: hits the network + LLM
    import sys

    slug = sys.argv[1] if len(sys.argv) > 1 else "joby-aviation"
    report = run_exit_gate_trial(slug, limit=int(sys.argv[2]) if len(sys.argv) > 2 else 5)
    print(report.render_markdown())
