# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

## [Unreleased]

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
