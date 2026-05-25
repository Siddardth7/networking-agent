---
description: Run the full networking pipeline for a company (Finder → Drafter → Marketer). Resumes automatically from current state if partially complete.
---

# /network-run

Run the end-to-end outreach pipeline for a target company. The pipeline discovers
contacts, lets you select which to message, drafts personalized outreach in three
channels, walks you through an approval loop, and writes a final Markdown artifact.

## Usage

```
/network-run <company-slug>
```

| Argument | Description |
|---|---|
| `company-slug` | URL-friendly company identifier (e.g. `spacex`, `blue-origin`) |

## What It Does

### Full Run (new company)

1. **Preflight** — runs `/network-check` and halts on any ✗ error
2. **Finder** — discovers contacts via Serper + Hunter, classifies persona + focus area, generates hooks
3. **Selection gate** — numbered list; choose contacts to draft for (`1,3`, `all`, `none`)
4. **Drafter** — parallel fan-out generates 3 channels per contact (LinkedIn connection, post-connection, cold email)
5. **Marketer** — interactive approval loop; `APPROVE`, `SKIP`, or `REVISE "<feedback>"`
6. **Artifact** — writes `~/.networking-agent/drafts/<slug>/YYYY-MM-DD-run.md`

### Resume (partially complete company)

The pipeline resumes from wherever it was interrupted:

| Stored State | Resumes At |
|---|---|
| `FOUND` | Selection gate |
| `SELECTED` | Drafter (only contacts not yet drafted) |
| `DRAFTED` | Marketer approval loop |
| `APPROVED` | Prints "Nothing to do; outreach_log entries pending send." |

## Examples

```
/network-run spacex
/network-run blue-origin
/network-run northrop-grumman
```

## Implementation

Calls `run_pipeline(company_slug)` in `src/orchestrator.py`.

State is persisted in `~/.networking-agent/state.db`. Run `/network-status`
to inspect current pipeline state for any company.

## Costs

Each full run costs approximately $0.10–0.30 at Claude Haiku rates (depends on
number of contacts selected and revision rounds). See `docs/COSTS.md` for a
detailed breakdown.
