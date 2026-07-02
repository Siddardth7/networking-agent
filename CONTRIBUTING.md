# Contributing

Thanks for looking at the networking-agent. This file is the whole process —
you shouldn't need to ask how things work before opening a PR.

## Dev setup

```bash
git clone https://github.com/Siddardth7/networking-agent
cd networking-agent
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # or: pip install -r requirements.txt
pytest -q                        # full suite — hermetic, no keys needed
ruff check src/ tests/
```

Tests never touch your real `~/.networking-agent/` (they monkeypatch config
and DB paths to temp dirs) and never call the network — except the **opt-in
live smoke** (`tests/test_live_smoke.py`), which catches provider contract
drift the mocks can't:

```bash
NETWORKING_AGENT_LIVE_SMOKE=1 pytest tests/test_live_smoke.py -v --no-cov
```

(Costs ~1 Serper credit + ~$0.0001 Anthropic; keys must be in your shell env
or `config.yaml` — the suite deliberately ignores `.env`.)

## The bar (what CI enforces)

- **Coverage gate:** `pytest` runs with `--cov-branch` and a hard
  `fail_under` (currently **98%** line+branch, repo-wide). New modules are
  expected at 100%; the gate only ratchets up, never down.
- **Lint:** `ruff check src/ tests/` must be clean.
- **Every PR runs CI** (`.github/workflows/ci.yml`); green is required to
  merge.

## How work is structured

- **One issue per change**, on a GitHub milestone. Issue #1 (pinned) is the
  canonical Definition of Done every issue follows — read it first.
- **Small slices ship dark when possible**: land the deterministic layer with
  tests before the feature that drives it.
- **Squash-merge PRs**; the PR body explains what/why and quotes the test
  evidence. Commit style: `feat(scope): …` / `fix(scope): …` /
  `test(scope): …` / `chore(release): …`.
- **CHANGELOG.md** (Keep a Changelog): every user-visible change adds an
  entry under `[Unreleased]` in the same PR.

## Architecture ground rules

- **Host-token inversion (issue #50):** the host Claude model does LLM
  judgment (classify / draft / critique) via `model: sonnet` subagents;
  Python is deterministic only (HTTP discovery, parsing, dedup, ranking,
  regex guardrails, persistence). Don't add Anthropic API calls to the
  default path — the API orchestrator exists solely as the `--api` headless
  fallback.
- **Profile-driven, never person-driven (issue #61):** no user-specific
  strings (schools, fields, employers, identities) in `src/` — they belong on
  `src/core/profile.py`'s `Profile`, with the built-in default reproducing
  existing behavior byte-for-byte (tests assert this).
- **No silent caps:** every drop, skip, or shortfall is logged or reported.
  A thin result must never look like a full one.
- **Fact discipline:** the drafter may only state facts from the resume
  library (with provenance) and the contact record. Nothing that weakens the
  anti-fabrication guardrails will be merged.
- **Migrations:** numbered SQL in `src/core/migrations/` +
  `PRAGMA user_version`; bump the `LATEST_MIGRATION` constants and their
  tests together.

## Good first contributions

Check the open issues for the current milestone (smallest first), or improve
the example profiles in `config/*.example.*` for a field we don't cover yet —
that's real user value with zero pipeline risk.
