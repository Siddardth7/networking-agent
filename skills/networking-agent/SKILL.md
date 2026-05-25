---
name: networking-agent
description: Entry-point orchestrator for the networking-agent plugin. Routes user intent through the full outreach pipeline (Finder → Drafter → Marketer) via state machine. Invoke when the user wants to run the full pipeline (/network-run), check status (/network-status), or needs routing to a sub-agent skill. This skill reads the company's current pipeline state from SQLite and dispatches to the appropriate sub-agent or CLI command.
---

# Networking Agent — Orchestrator Skill

This is the **entry-point skill** for the networking-agent plugin. It orchestrates the 3-agent outreach pipeline.

## Role

Route user requests to the correct pipeline stage based on company state:

| Company State | Action |
|---|---|
| NEW | Run preflight → Finder → selection gate → Drafter → Marketer |
| FOUND | Re-show selection gate |
| SELECTED | Re-run Drafter (fill missing channels only) |
| DRAFTED | Launch Marketer approval loop |
| APPROVED | Show "nothing to do; outreach_log entries pending send" |

## When to Invoke

- User types `/network-run <company-slug>`
- User asks to "run the networking pipeline for <company>"
- User needs routing and it's unclear which sub-agent to call

## Sub-Skills Delegated To

- `finder-agent` — contact discovery + enrichment + hook generation
- `drafter-agent` — parallel draft generation per contact × channel
- `marketer-agent` — approval loop + REVISE dispatch + artifact writer

## Error Modes

- DB not initialized → surface `/network-check` to diagnose
- API keys missing → surface `/network-check`
- Company not found in DB → prompt user to add to companies.csv

## Implementation

Entry point: `src/orchestrator.py` → `run_pipeline(company_slug)`.

The orchestrator looks up (or creates) the company by slug, reads its state,
and dispatches to the appropriate pipeline stage. All pipeline steps are
injected as parameters so they can be stubbed in tests.

### Resume Paths

```
NEW      → run_checks() → find_contacts() → run_selection_gate()
         → draft_for_contacts() → run_approval_loop() → write_artifact()

FOUND    → run_selection_gate() → draft_for_contacts()
         → run_approval_loop() → write_artifact()

SELECTED → draft_for_contacts(contacts_in_SELECTED_state)
         → run_approval_loop() → write_artifact()

DRAFTED  → run_approval_loop() → write_artifact()

APPROVED → print "Nothing to do; outreach_log entries pending send."
```

Any non-NEW state prints: `Resuming pipeline for <name> from state=<STATE>...`

### Preflight Short-Circuit

For NEW companies, `run_checks()` (i.e. `/network-check`) is called first.
If it returns exit code 1 (any ✗), the orchestrator halts and asks the user
to fix the errors before retrying.

### Mid-Run Resilience

If the process is killed after partial drafting (company state = SELECTED,
some contacts already DRAFTED), the next `/network-run` invocation resumes
from SELECTED and only drafts contacts still in SELECTED state — it never
re-drafts contacts already in DRAFTED state.
