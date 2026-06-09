# AUDIT_TRACKER_FABLE5 — consolidated issue list for v0.2.0

Sources: STRICT_AUDIT_v0.1.0.md (SA), DRAFTER_REVIEW_2026-06-02.md (DR),
DRAFTER_ROOT_CAUSE_AUDIT.md (RC), DRAFTER_AUDIT_2026-06-06.md (DA — latest,
wins on conflict), FIX_AGENT_PROMPT.md (FX), COUNCIL_VERDICT_v0.1.md (CV),
Fable-5 discovery (F5). Working tree already contains the June-6 session's
uncommitted Layer 1–6 work (critic.py, shared.py, migrations 002/003,
critic_trace persistence) — issues those changes already closed are listed
as RESOLVED(pre) and will be committed as part of this pass.

Severity: P0 safety/correctness · P1 quality holes blocking unattended runs
· P2 repetition/tone/variety · P3 docs/ergonomics.

| ID | Source | Sev | Area | Symptom | Root cause hypothesis | Planned fix | Status |
|----|--------|-----|------|---------|----------------------|-------------|--------|
| A1 | DA §1 P0-3.2 | P0 | drafter/guardrails | `[RESEARCH_NEEDED]` still *generated* (caught by gate, not prevented) | Generator not given an explicit regen path; placeholder only detected post-hoc | Detect placeholder after first generation → one regen with explicit anti-placeholder instruction; hard_check remains backstop | RESOLVED |
| A2 | F5 / DA §6 | P0 | artifact_writer/drafter | HARD_FAIL bodies serialized verbatim to DB + artifact incl. placeholder tokens | No scrub step between hard_check and serialization | Redact bracketed placeholder tokens from body before DB insert when placeholder hard-fail fires; artifact never renders a raw placeholder | RESOLVED |
| A3 | DA §3 | P0 | critic | ~93% hold rate (28/30) — unattended runs impossible | Decision rule "any dim < 3" + critic penalizing missing APPROVED FACTS as grounding failure | New decision rule: hold iff any dim ≤ 1 OR ≥ 3 dims < 3; clarify grounded_facts rubric (absence of facts ≠ fabrication); regression fixtures from real June-6 score vectors; hold rate on fixture set = 33% (band 20–40%) | RESOLVED |
| A4 | DA §4.1 | P1 | finder | Company-news string pasted verbatim as hook (Michael Tucker, Tanveer) | `_generate_hook` Tier 4 returns raw `company_news` snippet | Verbatim-news detector (dates/headline separators/financial-results phrasing); news may inform phrasing but never be the hook; title-derived hook tier inserted before news fallback | RESOLVED |
| A5 | DA §1 P1-3.5 | P2 | finder | Hooks fall back to category labels ("your manufacturing and quality background") | Tier 3 buckets are categories, not signals | Hook-shape whitelist (`is_acceptable_hook`); new title-derived tier ("your work as <title>") preferred over category buckets; category labels remain last-resort before GENERIC | RESOLVED |
| A6 | DA §1 P1-3.4 | P1 | drafter | Template fatigue: same opener across 8+ contacts (Layer 1-A unbuilt) | Per-contact thread isolation; no batch state | Thread-safe per-run opener registry; same normalized opener used > N contacts (config `opener_max_repeats`, default 2) triggers regen with anti-phrase | RESOLVED |
| A7 | DA §3a | P1 | drafter | Multi-ask drafts ("15 minutes… or if there's someone else") trigger critic holds | One-ask rule advisory-only at generation time | Deterministic `detect_multi_ask` in guardrails + ONE-ASK prompt block; detection triggers one regen; residual → SOFT_FLAG | RESOLVED |
| A8 | DA §1 P2-3.10 | P2 | drafter | Self-intro repeated in body and signature | No dedup rule in prompt or code | `detect_redundant_intro` (identity markers appearing ≥2×) + prompt rule "identity exactly once"; detection triggers regen; residual → SOFT_FLAG | RESOLVED |
| A9 | DA §5.1 | P1 | artifact_writer/guardrails | Artifact never explains *why* a draft was held (esp. HARD_FAIL, which has no trace) | hard_check reason discarded; `_format_critic_trace` omits `reason` | Persist hard_check reason as a minimal trace JSON in `critic_trace`; artifact renders a `Held because:` line for every HARD_FAIL/CRITIC_HOLD draft | RESOLVED |
| A10 | DA §1 (new critic) | P0 | critic | Critic decisions opaque → can't calibrate | Trace not persisted (pre-June-6) | RESOLVED(pre): migration 003 + critic_trace threading through drafter/dispatch/marketer/artifact (June-6 session, uncommitted) — committed in this pass | RESOLVED |
| A11 | RC §2.1 | P1 | finder | `shared_signals` never written; profiles never read; hooks category-only | Layer 1 unbuilt at v0.1.1 | RESOLVED(pre): classifier extracts `hook_signal` from snippet (Tier 0), `shared_signals` written, company-news search added (June-6 work, uncommitted) | RESOLVED |
| A12 | RC §2.2/§2.4 | P0 | drafter | Fabricated metrics; no fact discipline; no provenance | Layer 2 unbuilt at v0.1.1 | RESOLVED(pre): ProvenancedBullet, APPROVED FACTS block, FACT DISCIPLINE, hard_check numeric provenance | RESOLVED |
| A13 | RC §2.5 | P0 | guardrails | 4-phrase blocklist was the whole gate | Layer 3 unbuilt at v0.1.1 | RESOLVED(pre): voice.md forbidden-phrases merge, placeholder detector, metric provenance, config-driven lengths | RESOLVED |
| A14 | RC §2.7 | P0 | marketer | quality flag had no teeth | Layer 5 unbuilt at v0.1.1 | RESOLVED(pre): HARD_FAIL/CRITIC_HOLD block approval without `--force`; batch checkpoint in orchestrator | RESOLVED |
| A15 | RC §2.6 | P1 | dispatch | Revision path less grounded than first draft; duplicated constants | Layer 6 unbuilt at v0.1.1 | RESOLVED(pre): shared.py extraction; revision prompt reuses full grounding | RESOLVED |
| A16 | DA §4.3 | P1 | drafter/prompt | "Saw your recent posts" attributing company news to the person | Fact-discipline doesn't cover signal attribution | Add attribution rule to FACT DISCIPLINE: company-level news may never be described as the recipient's own posts/work | RESOLVED |
| A17 | SA P10 | P2 | drafter/config | voice.md / resume_library.yaml prompt-injection: no size cap | Trust-the-user model undocumented | Size-cap voice doc at 16 KB on load (truncate + warn); document trust model in README | RESOLVED |
| A18 | SA P11 | P2 | config | TOCTOU on config permission check (stat then open) | Separate stat/open | fstat the opened descriptor instead | RESOLVED |
| A19 | SA P12 | P2 | quota_manager | Cross-process quota race (deferred BEGIN) | Implicit deferred transaction | `BEGIN IMMEDIATE` for the quota increment transaction | RESOLVED |
| A20 | SA P13 | P2 | shared/finder | LLM response shape unguarded (`response.content[0]`, `tool_block.input`) | Happy-path assumption | Guard empty content (typed error) and non-dict tool input (fallback) | RESOLVED |
| A21 | SA P14 | P2 | finder | Hunter domain always inferred from slug; `companies.domain` ignored | Inference predates the column | Use `companies.domain` when present; fall back to inference | RESOLVED |
| A22 | SA P15 | P3 | shared | ThreadPoolExecutor-per-LLM-call for timeout | Pre-httpx-timeout pattern | wontfix: dispatch calls are once-per-REVISE and the executor pattern is tested and correct; swapping to httpx timeouts risks behavior change for zero user-visible gain in v0.2.0 | WONTFIX |
| A23 | SA P16 | P3 | drafter | `read_text()` without encoding for persona/voice | Default-encoding assumption | `encoding="utf-8"` on all template/voice reads | RESOLVED |
| A24 | SA P18/P19 | P3 | purge/audit-log | purge.log perms; no state-transition audit log | v0.2 deferred items in audit | wontfix: explicitly deferred to a future release by SA itself; out of v0.2.0 scope (no unattended send yet) | WONTFIX |
| A25 | SA P20 | P3 | providers | httpx.Client never closed | No lifecycle hook | Add `close()` to providers; call in tests via fixture | RESOLVED |
| A26 | SA P21 | P3 | drafter | voice/library paths ignore NETWORKING_AGENT_CONFIG override | Hardcoded Path.home() | Derive voice/library paths from the config-dir resolution | RESOLVED |
| A27 | F5 | P3 | repo | 254 ruff errors; 56 files unformatted | Lint never run as gate | `ruff check --fix` + `ruff format`; manual fixes for remainder; both clean | RESOLVED |
| A28 | F5 | P3 | repo | Dev venv was Python 3.9 vs requires-python >=3.11; `pip install -e .` broken (no build-system) | venv predates pyproject | Rebuild .venv on python3.11; add [build-system] + setuptools package config to pyproject | RESOLVED |
| A29 | F5 | P3 | src hygiene | print() throughout marketer/cli/orchestrator/artifact_writer; no logging module | CLI-first design | Justified: marketer REPL + CLI commands use print as their user interface (SA: "acceptable for a CLI"); artifact_writer's path print is consumed by the CLI contract. logging.getLogger added to agents' non-UI error paths | RESOLVED |
| A30 | DA §4.6 / CV | P1 | pipeline | Cold-email path unvalidated (no verified emails in June-6 run); Hunter quota exhausted | External quota, not code | wontfix: requires live Hunter quota (external blocker) — recorded in OPEN_QUESTIONS_FABLE5.md; cold-email code paths are unit-tested | WONTFIX |
| A31 | F5 | P3 | docs | CHANGELOG/README/DESIGN do not describe the June-6 critic work or v0.2.0 changes | Work uncommitted | CHANGELOG [0.2.0] section; README quality-gate section; DESIGN.md patches where implementation diverged; version bump | RESOLVED |
| A32 | DA §5.6 / spec §4.2 | P0 | critic/tests | No regression fixtures from the June-6 run | Fixtures never captured | 30 real score vectors from state.db encoded as fixtures; assertions: hold rate 20–40%, Morgan+Marc POST pass, Yueyang POST + Nathan CONN fail, placeholder strings HARD_FAIL | RESOLVED |
| A33 | spec §4.8 | P1 | dispatch/providers | Dispatch/quota/retry hygiene exposed by tests | — | Verified: test_dispatch (2 files), test_hunter, test_quota_manager, test_retry all present and green; coverage dispatch 81%→raised, hunter/quota covered | RESOLVED |

## Fix Order (spec §4)

1. **A1 + A2** — placeholder prevention upstream + never serialize placeholder bodies. (P0)
2. **A3 + A32** — critic recalibration to 20–40% band + June-6 regression fixtures. (P0)
3. **A4 + A5** — hook generation: verbatim-news detector, hook-shape whitelist, title-derived tier. (P1/P2)
4. **A6** — variety layer: cross-contact opener diversification (N configurable, default 2). (P1)
5. **A7** — single-ask enforcement at drafter level. (P1)
6. **A8** — self-intro deduplication. (P2)
7. **A9** — held_reason in artifact for every held draft (incl. HARD_FAIL trace persistence). (P1)
8. **A16** — attribution rule (company news ≠ "your posts"). (P1)
9. **A33** — dispatch/quota/retry test hygiene verification. (P1)
10. **A17–A21, A23, A25, A26** — security/robustness polish from STRICT_AUDIT. (P2/P3)
11. **A27 + A28** — lint, format, venv/build fixes; type hints + docstrings sweep. (P3)
12. **A10–A15 commits** — commit the June-6 uncommitted work in logical units.
13. **A31** — docs: CHANGELOG 0.2.0, pyproject bump, README, DESIGN patches.

## Verification gate results

Filled at the end of the pass — see CHANGELOG and final report.

## Grep-gate justifications (spec §6)

- `print(` in `src/cli/*`, `src/agents/marketer.py`, `src/orchestrator.py`,
  `src/agents/artifact_writer.py`: the interactive REPL and CLI commands use
  stdout as their user interface; STRICT_AUDIT explicitly accepted this.
  Non-UI error paths now use `logging.getLogger(__name__)`.
- `[RESEARCH_NEEDED]` string: appears only in `guardrails.py` (detector),
  `drafter.py` (anti-placeholder prompt instruction), and tests.
- No TODO/FIXME/XXX/breakpoint()/bare-except in `src/` (verified by grep).
