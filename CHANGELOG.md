# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

## [Unreleased]

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
