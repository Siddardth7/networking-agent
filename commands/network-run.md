---
description: Run the full networking pipeline for a company end-to-end on the HOST Claude's tokens (no API key) — discover → classify → ingest → select → draft → critic → approve → artifact. Resumes from current state. Use --api for the headless Anthropic-API fallback.
---

# /network-run


> **Shell note (Windows):** the commands below use the plugin's Python runner. In bash / WSL / Git-Bash use `"${CLAUDE_PLUGIN_ROOT}/bin/nag" …` exactly as written; in **native PowerShell** substitute the runner with `& "$env:CLAUDE_PLUGIN_ROOT\bin\nag.ps1" …` (same module and args).

Run the end-to-end outreach pipeline for a target company. **By default this runs
on host tokens** (issue #50): the deterministic Python bridges do discovery, gating,
and persistence; **you** (the host model) do the writing/judgment via the `model:
sonnet` subagents. No `ANTHROPIC_API_KEY` topup needed. Pass `--api` to fall back to
the headless Python orchestrator (`run_pipeline`) that calls the Anthropic API —
for CI / unattended runs.

## Usage

```
/network-run <company-slug>          # host-token orchestration (default)
/network-run <company-slug> --api    # headless Anthropic-API fallback
```

## Host-token orchestration (default)

The planner is the driver. After **every** step, re-run it to get the next action:

```
"${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_run_host plan <slug>
```

→ `{company, state, next, items}`. Dispatch on `next`:

| `next` | What you do |
|---|---|
| `discover` | Preflight `/network-check`, then run `/network-find-here <slug>` (discover → classify each candidate via the `networking-classifier` subagent → ingest). Advances the company to `FOUND`. |
| `select` | `items` is the rank-ordered contacts. Present them **with a one-line why each** — coach mode (#78): read the contact's `rank_reasons` (the per-signal score breakdown, e.g. "confirmed alumnus, 1st-degree connection") and hook back in plain language, so the user learns the strategy while choosing. Ask which to draft for. Persist the choice: `network_run_host select <slug> --ids 1,3,5`. Advances to `SELECTED`. |
| `draft` | `items` is the SELECTED contacts. For each, for each channel (`LINKEDIN_CONNECTION`, `LINKEDIN_POST_CONNECTION`, `COLD_EMAIL`): run `/network-draft-here` (context → `networking-drafter` subagent → save) to get a `draft_id`, then **immediately** `/network-critic-here` on that `draft_id` (context → `networking-critic` subagent → apply). Critiquing inline avoids re-enumerating drafts. |
| `approve` | Run `/network-approve <slug>` — the interactive marketer loop (`APPROVE` / `SKIP` / `REVISE`), which writes the `.md` artifact on full approval. When presenting a draft, name its ask angle and hook source in one line (coach mode, #78) — "this one asks about hiring climate; the hook is her 787 stress work from the profile snippet" — so the review teaches, not just gates. |
| `done` | Nothing to do; outreach_log entries are pending manual send. |

The loop is fully **resumable**: the planner reads persisted state, so a run
interrupted at any step picks up exactly where it left off — same state machine as
the API path, just host-driven.

### Resume map

| Stored company state | `next` |
|---|---|
| `NEW` (or unknown slug) | `discover` |
| `FOUND` | `select` |
| `SELECTED` | `draft` |
| `DRAFTED` | `approve` |
| `APPROVED` | `done` |

## Headless fallback (`--api`)

`/network-run <slug> --api` calls `run_pipeline(company_slug)` in
`src/orchestrator.py` — the original single-entrypoint orchestrator that runs the
Finder/Drafter/Critic/Marketer on the Anthropic API (Haiku/Sonnet). Use it for
unattended/CI runs where no host model is driving. It costs ~$0.10–0.30 per run at
Claude rates (see `docs/COSTS.md`); the default host path costs no API credit.

## Notes

- **Coach mode (#78)**: the user may not know why the agent works this way —
  explain choices in one line as you present them, using data already on the
  rows (`rank_reasons`, the hook text, the assigned ask angle). For the full
  strategy conversation, point them at `/network-coach`.
- State lives in `~/.networking-agent/state.db`; run `/network-status` to inspect.
- The agent never touches LinkedIn — discovery is off-platform and the human sends.
- Both paths converge on the same DB rows and the same Markdown artifact.
