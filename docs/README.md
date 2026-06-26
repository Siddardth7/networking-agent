# Documentation index

All project documentation, grouped by purpose. Filenames are stable —
code, tests, and cross-doc links reference these paths, so they are not moved
into subfolders.

## 📍 Start here

| Doc | What it is |
|---|---|
| [ROADMAP.md](ROADMAP.md) | **Canonical plan** — where we are, the phased version ladder (v0.5 → v1.0), and the public-release gate. Read this first. |
| [ANTI_AI_DETECTION.md](ANTI_AI_DETECTION.md) | **The moat thread** (started #6) — why "would a recruiter flag this as AI in 20s?" is a first-class gate, and the deterministic tell scanner + LLM `tone` dimension that enforce it. Extended each release. |
| [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md) | **Process contract** — the mandatory per-issue + per-version gates (ponytail → tester → 95% line+branch loop → `/code-review` → ponytail-review). Mirrored as pinned issue #1. Read before starting any issue. |
| [MARKET_GAP_AND_FEATURE_IDEAS_2026-06-21.md](MARKET_GAP_AND_FEATURE_IDEAS_2026-06-21.md) | The 2026 market thesis (ATS reality, referral math, why generic AI outreach fails) and the ranked feature ideas behind the roadmap. |

## 🔎 Sourcing & inputs (research + design)

| Doc | What it is |
|---|---|
| [LEAD_SOURCING_RESEARCH_2026-06-21.md](LEAD_SOURCING_RESEARCH_2026-06-21.md) | Where contacts come from — current source, the 2026 provider landscape (Apollo/PDL/Serper/Apify, Proxycurl's shutdown), paid-vs-free analysis, the 30-day campaign plan, and the $0 plan. |
| [FLEXIBLE_INPUT_DESIGN_2026-06-21.md](FLEXIBLE_INPUT_DESIGN_2026-06-21.md) | The source-agnostic input architecture — the canonical contact record every source normalizes to, and how the importer feeds the unchanged Drafter. |

## 🤝 Cowork + Chrome producer (the contact-capture side)

| Doc | What it is |
|---|---|
| [CHROME_PRODUCER_CONTRACT.md](CHROME_PRODUCER_CONTRACT.md) | **The agreed interface** — I/O paths (`runs/…`), the honored fields, the daily cycle. The stable contract the producer builds against. |
| [chrome-capture.example.json](chrome-capture.example.json) | A reference capture file (locked in by a contract regression test). |
| [COWORK_CHROME_BRIEF_2026-06-21.md](COWORK_CHROME_BRIEF_2026-06-21.md) | The brief handed to Cowork: context + questions for it to self-assess and design its workflow. |
| [COWORK_CHROME_PRODUCER_RESPONSE_2026-06-21.md](COWORK_CHROME_PRODUCER_RESPONSE_2026-06-21.md) | Cowork's capability assessment, proposed workflow, and daily commands. |
| [HANDOFF_TO_ENGINEERING_2026-06-21.md](HANDOFF_TO_ENGINEERING_2026-06-21.md) | The wiring handoff that turned the producer response into the implemented contract. |

## 🧪 Trials, validation & reference

| Doc | What it is |
|---|---|
| [TRIAL_AST_SPACEMOBILE_2026-06-13.md](TRIAL_AST_SPACEMOBILE_2026-06-13.md) | Live Drafter trial run + scorecard (the model for future Finder trials). |
| [FINDER_AUDIT_2026-06-26.md](FINDER_AUDIT_2026-06-26.md) | **Finder defect catalog** (issue #3) — 12 defects across discovery/classify/hook, severity-ranked, routed to fix issues. The Finder's first audit. |
| [CLASSIFY_SCORECARD_2026-06-26.md](CLASSIFY_SCORECARD_2026-06-26.md) | **Classify-accuracy baseline** (issue #4) — persona 100% / focus 68%; the focus gap is mostly unspecified-convention behavior, routed to #5. Bar: persona 100% / focus ≥95%. Reproduce with `python -m src.eval.classify_scorecard`. |
| [COSTS.md](COSTS.md) | Per-run cost / quota breakdown. |
| [INSTALL_SMOKE_TEST.md](INSTALL_SMOKE_TEST.md) | Manual install smoke-test checklist. |
| [DESIGN_DRIFT_v0.2.0.md](DESIGN_DRIFT_v0.2.0.md) | Recorded design-vs-implementation drift notes. |

---

*Changelog lives at [`../CHANGELOG.md`](../CHANGELOG.md); usage docs at
[`../README.md`](../README.md) and `../commands/`.*
