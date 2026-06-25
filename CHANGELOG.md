# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

## [Unreleased]

### Fixed
- **LinkedIn connection notes no longer lost to length HARD_FAIL (live-validation
  finding).** The drafter already gave an over-length `LINKEDIN_CONNECTION` note
  one corrective regen, but the model could still return marginally over the
  280-char cap (observed against a real Apollo export: 287 vs 280) — and those
  drafts HARD_FAILed, dead on arrival. The drafter now deterministically
  auto-trims a still-over note to fit (keeping leading whole sentences; word-
  boundary + ellipsis fallback) and marks it `SOFT_FLAG` so the reviewer sees it
  was machine-shortened rather than silently sent. New `_trim_to_char_limit()`
  helper, covered by unit + integration tests.
- **Drafter batch concurrency tuned for the Anthropic Tier-1 limit (live-
  validation finding).** A full batch at the old fixed `_MAX_WORKERS=6` sustained
  throughput above the Tier-1 **input-tokens-per-minute** ceiling (50k ITPM),
  so `max_retries=8` could not recover and large imports partially failed with
  `RateLimitError` (failed contacts correctly rolled back to `SELECTED`, fully
  retryable). Concurrency is now `min(_MAX_WORKERS ceiling, Config.drafter_max_workers)`
  with a new `drafter_max_workers` default of `3` (keeps a batch under the
  Tier-1 ITPM ceiling out of the box; raise it on higher tiers).
- **Resolved 3 pre-existing `ruff` lint issues** (import ordering in
  `drafter.py`, two `E501` over-length lines in `config.py` and
  `test_humanizer.py`). `ruff check` is now clean.

### Changed
- **README rewritten** as a professional landing page (animated header, badges,
  a mermaid pipeline diagram, market-thesis intro, collapsible sections). Fixes
  stale content: `v0.2.0` status → current, the `<your-username>` install
  placeholder → real repo URL, and the wrong `data.db` reference → `state.db`.
  Adds `/network-import`, the v0.3–v0.4 features, and roadmap/docs links. Plus a
  `docs/README.md` index so the docs folder is navigable.

### Added
- **Per-source contribution logging ("no silent caps", ROADMAP A1).** The import
  path now reports exactly what each source contributed and what it dropped, so a
  thin or lossy file is never mistaken for full coverage. New
  `parse_contacts_file_with_report()` tallies `rows_read` / `usable` and a
  per-reason `dropped` breakdown (`no_name` / `no_company` / `duplicate`);
  `import_contacts()` now returns `{"by_company": {...}, "contribution": {...}}`
  and `/network-import` prints a `Source '<src>': N row(s) read → M usable
  (dropped: …)` line before the per-company summary. `parse_contacts_file()` is
  unchanged (still returns the contact list).
- **Source-agnostic contact input (flexible-input design).** Contacts no longer
  have to come from the Serper Finder. The Finder's second half was extracted
  into a shared `ingest_contacts()` (enrich → classify → hook → save), and a new
  `src/agents/importer.py` normalizes any leads file — Apollo export, Apify
  scrape, Serper/Cowork+Chrome JSON, or a hand-compiled CSV/JSON — into the
  canonical contact record and runs it through that same path. Headers are
  matched by alias (`Person Linkedin Url` / `profileUrl` / `linkedin` →
  `linkedin_url`, etc.); `persona`/`focus_area`/`hook` are generated when absent
  and honored when the file supplies them; a supplied `email` skips Hunter. New
  `/network-import <file> [--company] [--source] [--draft] [--validate]` command
  gives a frictionless "leads file in → drafts out" path (reusing ask-rotation,
  the marketer approval loop, and the artifact); wired as a CLI entry point at
  `src/cli/network_import.py` (`python -m src.cli.network_import <file> --draft`),
  matching the other `network_*` commands. `validate_contacts_file()` is
  the producer-contract check for the Cowork + Chrome automation. The Finder's
  Serper path is unchanged (byte-for-byte behavior; 534 prior tests still green).
  Design: `docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md`.
- **Cowork + Chrome producer contract.** Agreed I/O for the read-only LinkedIn
  capture producer: `runs/<YYYY-MM-DD>-<slug>.json` outputs + `runs/targets.csv`
  queue (git-ignored), and three honored producer fields — `alumni_confirmed`
  (forces the ALUMNI persona, ground truth over the classifier), file-level
  `school`, and `connection_degree` (both surfaced in `shared_signals` for the
  reviewer). The published sample `docs/chrome-capture.example.json` is locked in
  by a contract regression test. Contract: `docs/CHROME_PRODUCER_CONTRACT.md`.

## [0.4.0] - 2026-06-20

Phase 3 — ask-rotation: same-company alumni/peers now get *different* questions
instead of the same script. The validation run also caught and fixed a serious
pre-existing bug where the Drafter fabricated the contact's employer.

### Added
- **Ask-rotation across same-company contacts (Phase 3).** When several
  contacts at the *same* company share a rotation-eligible persona — alumni or
  peer engineer — each is assigned a *distinct* ask angle before drafting, and
  that angle is injected into its generation prompt. Five alumni at one company
  now get five different questions (hiring climate / sponsorship / culture /
  transition / who-to-talk-to) instead of the same script. Peers rotate over a
  parallel set (day-to-day work / a specific project / culture / how they broke
  in / advice for someone finishing an MS). Assignment is deterministic and
  computed once up front (no extra LLM calls, race-free) via
  `drafter.assign_ask_angles`; recruiters (role-specific ask) and senior
  managers (no hard ask) are not rotated, and a lone contact gets no
  assignment — the model still picks the single best angle for them, exactly as
  before. New `enable_ask_rotation` config knob (default on). The alumni and
  peer persona templates' Close sections enumerate the rotatable angles to keep
  the prose in sync with the injected instruction.

### Fixed
- **Drafter no longer fabricates the contact's employer.** The generation
  prompt never received the company name — only `company_id` — so when a
  persona template referenced "a fellow alum at {Company}" the model invented a
  plausible-but-wrong employer (Boeing, Lockheed, Skydweller) or leaked a
  literal `[Company]`. `_load_contact` now LEFT JOINs `companies` and the prompt
  carries a `Company:` line, plus a FACT DISCIPLINE rule pinning the model to
  that exact name (and to "your team" when it's Unknown). Surfaced by the Phase
  3 multi-alumni validation run; re-validated to zero fabrication.
- **Placeholder detector now catches mixed-case brackets.** The bracket gate was
  all-caps-only (`[COMPANY]`), so a title-case `[Company]` slipped through as
  OK. Widened to catch `[Company]` / `[Team Name]` / `[your team]` while still
  ignoring lowercase citation markers like `[smith2023]`.
- Corrected `config/default.yaml`'s `linkedin_char_limit` from a stale `200` to
  `280` (a v0.3.0 200→280 correction had missed this template file).

## [0.3.0] - 2026-06-19

Voice overhaul: the Drafter now writes in the user's own outreach voice
(a 4-part model, persona-tuned) instead of generic LLM phrasing. Validated
on an AST SpaceMobile re-run (30 drafts): zero fabrication, zero placeholder
leaks, zero over-length notes, full opener variety, and the "exactly the
kind of" AI tell eliminated (was 4/30).

### Added
- **4-Part Message Model** (Intro → Source → Hook → Close) encoded in
  `voice.md` and all four persona templates. Context-first openers ("how/why
  I found you") are now first-class; each persona specializes the Source,
  Hook, and Close (recruiters direct/role-specific; senior managers
  admire-and-stay-connected with no hard ask; peers ask about their work;
  alumni lead with the shared school and ask one rotating company question).
- **Humanizer** (`src/agents/humanizer.py`): a deterministic, grammatically
  safe post-generation pass that strips filler intensifiers the soft
  blocklist can't shake (the "exactly the kind/type/sort of …" /
  "exactly the direction" family). Wired into the Drafter after each
  generation so the tell never reaches the gate or the wire.
- **Length-regen** for LinkedIn connection notes: an over-cap note gets one
  compression pass before HARD_FAIL (closes the "no auto-trim" gap).

### Changed
- **LinkedIn connection-note cap corrected: 200 → 280 characters.** The 200
  value was a misread ("free-account hard limit"); LinkedIn's note cap is 300
  on all plans (free accounts are limited on note *count*, not length). 280
  is a safe margin (spaces count, emojis count double). Wired through config,
  guardrail, prompt text, and all persona templates.
- **Blocklist specificity-relaxed:** "I admire" and "I came across" are no
  longer blunt hard-bans — specific admiration/sourcing is a valid hook, and
  the critic's specificity dimension judges genericness. Unambiguous tells
  ("your impressive work", "exactly the kind of") remain hard-blocked.
- Anthropic client now uses `max_retries=8`: the parallel Drafter can burst
  past lower-tier input-token rate limits; the larger retry budget lets a run
  self-pace and complete instead of failing mid-batch.
- Refreshed `config/voice.example.md` to the 4-part model (the prior installed
  voice doc was stale: claimed a 300-char cap and instructed `[RESEARCH_NEEDED]`
  placeholders, both since removed).

## [0.2.2] - 2026-06-18

Quality release: closes the one actionable defect surfaced by the
AST SpaceMobile trial run (`docs/TRIAL_AST_SPACEMOBILE_2026-06-13.md`).

### Added
- Seed blocklist now flags the AI tell **"exactly the kind of"**, which
  appeared in 4/30 trial drafts (including an otherwise-sendable one). The
  phrase was already named in the humanizer's fix prompt but was not
  enforced by the guardrail. Detection is unit-tested in
  `tests/test_guardrails.py`.

## [0.2.1] - 2026-06-11

Free-quota release: default runs now spend zero Hunter credits and never
pay twice for the same search.

### Added
- Search-response cache (`search_cache` table, migration 004): Serper
  responses are cached in SQLite keyed by request payload with a
  configurable TTL (`providers.search_cache_ttl_days`, default 14 days;
  0 disables). Cache hits skip both the HTTP call and the quota
  increment, so re-runs, resumed runs, and trial iterations are free.

### Changed
- **Hunter email enrichment is now OPT-IN** (`pipeline.enable_email_enrichment`,
  default `false`). Rationale: the free tier is 25 lookups/month (~1.5 runs)
  while LinkedIn channels convert far better and need no email. With
  enrichment off, the finder requires no Hunter key, spends zero Hunter
  quota, marks contacts `EMAIL_DISABLED`, and the drafter skips cold email
  (existing tested path). An explicitly injected provider always wins over
  the toggle. **Migration note:** users who relied on cold-email drafting
  must set `pipeline.enable_email_enrichment: true` in config.yaml.
- `/network-check` no longer fails preflight on a missing Hunter key when
  enrichment is disabled (reports an informational skip instead).

### Tests
- 485 -> 505 passing; coverage 90.2%.

## [0.2.0] - 2026-06-10

Quality release: closes every open item from DRAFTER_AUDIT_2026-06-06 and the
remaining STRICT_AUDIT_v0.1.0 follow-ups. Full issue ledger with per-item
status: AUDIT_TRACKER_FABLE5.md.

### Added
- **A10-A15** Layers 1-6 from the 2026-06-06 session, previously uncommitted:
  snippet-derived hook signals + shared_signals (Layer 1), provenance-tagged
  APPROVED FACTS + FACT DISCIPLINE (Layer 2), hard guardrail gate (Layer 3),
  Sonnet critic with persisted critic_trace (Layer 4 + migration 003), the
  HARD_FAIL/CRITIC_HOLD marketer gate + batch checkpoint (Layer 5), and the
  shared drafter/dispatch implementation (Layer 6).
- **A6** Cross-contact opener diversification (Layer 1-A): a normalized opener
  may be used by at most `quality.opener_max_repeats` contacts per run
  (default 2); overuse triggers a corrective regen, persistent repeats are
  SOFT_FLAGged.
- **A7** Drafter-level single-ask enforcement: deterministic multi-ask
  detector (two questions / two ask-sentences / hedge-stacked ask) with one
  corrective regen; prompt gains an explicit one-ask rule.
- **A8** Self-intro deduplication: identity markers appearing twice (body +
  signature) trigger a regen; prompt instructs identity-once.
- **A9** Every held draft now carries a populated held reason: hard_check
  reasons persist to `drafts.critic_trace`, and the artifact + marketer render
  a `Held because:` line for HARD_FAIL and CRITIC_HOLD drafts.
- **A20** Typed `EmptyLLMResponseError` (src/core/errors.py) replaces
  IndexError/AttributeError on malformed LLM responses.
- **A25** `SerperProvider.close()` / `HunterProvider.close()` httpx lifecycle.
- **A32** Critic regression fixtures: the 30 real score vectors from the
  2026-06-06 Joby run are encoded in tests/test_critic_calibration.py.

### Changed
- **A3** Critic decision rule recalibrated: hold only on a severe dimension
  (score <= 1, including grounded_facts fabrication evidence) or more than two
  weak dimensions (< 3). Hold rate on the June-6 fixture set drops 93% -> 33%
  (target band 20-40%); Morgan/Marc passing drafts still pass, Yueyang
  multi-ask and Nathan placeholder still fail. grounded_facts rubric
  clarified: absence of APPROVED FACTS is not fabrication.
- **A5** Hook generation: new title-derived tier (`your work as <title>`)
  preferred over the GENERIC sentinel; `is_acceptable_hook` whitelist gates
  classifier signals.
- **A16** FACT DISCIPLINE rule 5: company-level news may never be attributed
  to the contact personally ("your recent posts").
- **A26** voice.md and resume_library.yaml now resolve next to config.yaml,
  honoring `NETWORKING_AGENT_CONFIG`.
- **A28** pyproject gains [build-system] + setuptools package config —
  `pip install -e .` works; version bumped to 0.2.0; dependencies declared.
- **A31** README documents the quality gate and the voice.md trust model.
  DESIGN-drift notes recorded in docs/DESIGN_DRIFT_v0.2.0.md (the original
  DESIGN.md lives outside this repository and is intentionally untouched).

### Fixed
- **A1** Placeholder tokens are now *prevented*, not just caught: a
  placeholder in the first generation triggers one corrective regen with an
  explicit anti-placeholder instruction before the hard gate runs.
- **A2** Placeholder bodies are never serialized: a draft that still contains
  a bracketed token after regen is HARD_FAILed and redacted
  (`(placeholder removed)`) before any DB insert, in both the drafter and the
  dispatch revision path.
- **A4** Raw company-news strings can no longer become hooks: verbatim-news
  detector (datelines, headline separators, press-release phrasing) rejects
  them in every tier; news stays in shared_signals as phrasing material.
- **A17** voice.md is size-capped at 16 KB on load (truncated with a logged
  warning) to bound prompt-injection/token blow-up from pasted templates.
- **A18** config.yaml permission check now fstats the open descriptor
  (TOCTOU window closed).
- **A19** Quota increments run under `BEGIN IMMEDIATE` — the check-then-update
  pair is atomic across processes, not just threads.
- **A21** Hunter lookups use the stored `companies.domain` when present; slug
  inference is only the fallback.
- **A23** Persona templates, voice doc, and resume library are read with
  explicit utf-8 encoding.
- **A27** Repo is ruff-lint and ruff-format clean (254 violations fixed).

### Removed
- Hook Tier 4 "company news as hook string" (replaced by the title-derived
  tier; news is context, never a hook).

### Wontfix (with reasons — see AUDIT_TRACKER_FABLE5.md)
- **A22** dispatch executor-per-call timeout pattern: once-per-REVISE, tested,
  zero user-visible gain from swapping to httpx timeouts.
- **A24** purge.log hardening + state-transition audit log: explicitly
  deferred by STRICT_AUDIT itself; no unattended send in v0.2.0.
- **A30** Cold-email path live validation: blocked on Hunter quota (external);
  recorded in OPEN_QUESTIONS_FABLE5.md. Unit coverage exists.

### Tests
- 409 -> 485 passing; coverage 90.2% (critic 100%, guardrails 99%, drafter 94%).

## [0.1.1] - 2026-05-25

Hotfix release addressing HIGH and MEDIUM findings from the pre-ship audit (STRICT_AUDIT_v0.1.0.md).

### Security
- **P9** Hunter.io API key no longer leaks into stderr tracebacks; httpx exceptions raised from Hunter calls are now caught and re-raised through a sanitized twin with `api_key=***` and a broken `__cause__` chain via `scrubbed_hunter_call()` (commits `a24b27c`, `72d7ab2`).

### Fixed
- **P6** Per-contact draft sequence in `src/agents/drafter.py` is now atomic — delete-v1, N channel inserts, and the DRAFTED state transition run inside a single `with_writer()` block, so a mid-sequence crash can no longer leave a contact marked DRAFTED with missing channel rows (commits `85576cd`, `6ae0ec3`).
- **P7** `draft_for_contacts` now drains all worker futures on failure and raises `DrafterPartialFailure(RuntimeError)` carrying `.partial_results` and `.errors`, so completed contacts are preserved when a peer worker raises (commits `7862442`, `b4843b4`).
- **P8** `network_purge` no longer follows symlinks when removing `~/.networking-agent/drafts/<slug>/`; the new `_safe_rmtree` helper refuses symlinked paths, emits a stderr warning, and records `fs=symlink-skipped` in both the stdout summary and the audit log (commits `fa209be`, `8255e0e`).

### Tests
- 240 → 252 passing; coverage 88.06%.

## [0.1.0] - 2026-05-25

### Added

#### Commands
- `/network-run <slug>` — full pipeline with state-machine resume (NEW→FOUND→SELECTED→DRAFTED→APPROVED)
- `/network-check` — preflight setup checks (SQLite, DB integrity, schema version, config perms, API key pings, voice doc)
- `/network-find <slug>` — run Finder Agent only (Discover→Enrich→Classify→Hook→Save)
- `/network-draft <slug>` — run Drafter Agent only (parallel fan-out, 3 channels per contact)
- `/network-approve <slug>` — run Marketer approval loop only (APPROVE/SKIP/REVISE)
- `/network-status [slug]` — show pipeline state for one or all companies
- `/network-dry-run <slug>` — simulate run without making API calls
- `/network-providers` — display API quota status (Serper, Hunter, Anthropic)
- `/network-purge [slug]` — delete company data from DB (GDPR compliance)

#### Agents
- **Finder Agent** (`src/agents/finder.py`) — 5-phase pipeline: LinkedIn profile discovery via Serper, email enrichment via Hunter, persona + focus-area classification via Claude Haiku, hook generation, SQLite persistence
- **Drafter Agent** (`src/agents/drafter.py`) — parallel fan-out (up to 6 workers), generates LinkedIn connection note + post-connection message + cold email per contact; guardrail regen on quality flag
- **Marketer Agent** (`src/agents/marketer.py`) — interactive approval loop with APPROVE/APPROVE ALL/SKIP/REVISE/SHOW commands; REVISE dispatches draft revision via Claude Haiku
- **Orchestrator** (`src/orchestrator.py`) — state-machine dispatcher, resumes from any interrupted state, preflight short-circuit on errors

#### Infrastructure
- SQLite state DB with WAL mode, single-writer lock, retry-on-busy (`src/core/db.py`)
- Schema migrations system (`src/core/migrations/`)
- Pydantic-validated config (YAML + env var fallback) (`src/core/config.py`)
- Provider abstraction with exponential-backoff retry and quota management (`src/providers/`)
- Artifact writer — Markdown output to `~/.networking-agent/drafts/<slug>/` (`src/agents/artifact_writer.py`)
- Dispatch protocol for REVISE sub-requests (`src/agents/dispatch.py`)
- Achievement matcher against resume library YAML (`src/agents/achievement_matcher.py`)
- Reputation guardrails — blocks over-used phrases from drafts (`src/agents/guardrails.py`)

#### Quality
- 238 tests, 87% line coverage on `src/`
- `pytest --cov-fail-under=80` enforced in `pyproject.toml`
- `claude plugin validate` passing

### Fixed (BLOCKING issues resolved during design review)
- **Word-boundary regex** in Tier 2 guardrail hook generation — prevented substring false positives
- **Tier 3 hook generation** — expanded to match `"structures"` keyword variant
- **Circular import** in Marketer/dispatch — deferred `dispatch_revision` import to avoid module-load cycle
- **Test fixture DB path patching** — corrected monkeypatch target for `src.core.db._DB_PATH`
- **REVISE lazy resolution** — `dispatch_revision` resolved at first call, not at module import
- **Selection gate state update** — company state now transitions to `SELECTED` atomically with contact updates
