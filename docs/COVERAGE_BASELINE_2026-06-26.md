# Coverage baseline — line + branch, all of `src/` (issue #21)

**Date:** 2026-06-26 · **Repo:** **89.28%** combined line+branch (633 tests) ·
**Gate target:** 95% at the v0.5.5 close (#7), ratcheting `fail_under` upward.
**Reproduce:** `pytest --cov=src --cov-branch --cov-report=term-missing`.

This is the umbrella baseline (#21): it ranks every module's gap and routes the
work to the per-module audit issues so each starts with concrete numbers. It is
the prerequisite for the 95% gate flip in #7.

## What it takes to reach 95%

Total coverable units = 3374 stmts + 1030 branches = **4404**; currently **450
uncovered** (310 stmts + 140 partial branches). 95% needs ~**230** of those
covered — i.e. roughly the **four biggest modules** below clear most of the gap.

## Modules below 95% — ranked by uncovered units (the work)

| Module | Cover | Uncovered (miss+brpart) | Routes to |
|---|---:|---:|---|
| `cli/network_check.py` | 72% | ~119 | #25 |
| `agents/marketer.py` | 84% | ~60 | #23 |
| `agents/dispatch.py` | 79% | ~32 | #23 |
| `providers/hunter.py` | 73% | ~31 | #22 (also rewritten by #13) |
| `agents/finder.py` | 91% | ~27 | #24 / #8 / #27 |
| `agents/drafter.py` | 93% | ~29 | #23 |
| `agents/importer.py` | 93% | ~17 | #24 |
| `cli/network_import.py` | 80% | ~17 | #25 |
| `core/config.py` | 88% | ~17 | #25 |
| `cli/network_purge.py` | 86% | ~19 | #25 |
| `orchestrator.py` | 86% | ~19 | #25 |
| `cli/network_status.py` | 91% | ~12 | #25 |
| `providers/quota_manager.py` | 91% | ~10 | #22 |
| `providers/pdl.py` | 92% | ~9 (dormant) | #22 (track-or-delete) |
| `providers/apify.py` | 93% | ~7 | #22 |
| `core/migrations.py` | 94% | ~2 | #25 |
| `core/search_cache.py` | 94% | ~2 | #25 |
| `providers/apollo.py` | 94% | ~2 | #22 |
| `cli/network_providers.py` | 94% | ~2 | #25 |
| `providers/retry.py` | 95% | ~3 | #22 (at bar) |
| `providers/serper.py` | 95% | ~7 | #22 (at bar) |
| `agents/artifact_writer.py` | 98% | ~3 | #23 (at bar) |
| `agents/guardrails.py` | 98% | ~3 | #23 (at bar) |

**Already at 100% (no action):** `agents/critic`, `agents/humanizer`,
`agents/achievement_matcher`, `agents/shared`, `core/schemas`, `core/db`,
`core/errors`, `eval/classify_scorecard`, `cli/network_dry_run`,
`cli/selection_gate`, all `__init__`.

## Per-issue routing (refreshes #22–#26)

- **#22 — providers:** hunter (73% — biggest provider gap; #13 rewrites it),
  quota_manager (91%), pdl (92% — **decide track vs delete**), apify (93%),
  apollo (94%); retry/serper at 95% (minor). Missing lines posted to #22.
- **#23 — drafter family (SCOPE BROADENED):** the original "drafter + humanizer +
  critic + guardrails" missed the biggest agent gaps. Now owns **drafter (93%),
  marketer (84%), dispatch (79%)**, artifact_writer (98%), guardrails (98%);
  critic/humanizer/achievement_matcher already 100%. marketer + dispatch are the
  highest-value targets in the whole repo after network_check.
- **#24 — importer + finder shared:** importer (93%), finder (91%). Note finder's
  uncovered branches overlap the audit defects owned by **#8** (discovery: D1
  silent-error path, provider-config branches) and **#27** (D10 sentinel) — those
  issues cover them; #24 owns the importer/shared-ingest branches.
- **#25 — core + CLI (+ orchestrator):** network_check (72% — **the single
  largest gap in the repo**), network_import (80%), network_purge (86%),
  orchestrator (86% — added here as top-level glue), config (88%),
  network_status (91%), migrations/search_cache/network_providers (94%).
- **#26 — test-realism:** not a coverage target (it adds a live-API smoke path);
  unaffected by this baseline.

## Path to closing v0.5.5 (#7)

1. **#25** (network_check alone is ~119 units → biggest single lift toward 95%).
2. **#23** (marketer + dispatch ≈ 92 units).
3. **#22** (hunter + the rest ≈ 55 units) and **#24/#8/#27** (finder + importer).
4. Re-run; when repo ≥95% line+branch, **#7** flips `fail_under` to 95 and tags.

Each audit follows the DoD (qa-expert supervises the test strategy; the live
scorecard/coverage loop is the gate). Several uncovered branches are the very
defects the Finder audit named (#3) — covering them and fixing them land together.
