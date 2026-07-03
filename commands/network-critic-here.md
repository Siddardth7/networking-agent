---
description: Run the Layer-4 critic judgment over a saved draft on the HOST Claude's tokens (no API key) — the host-token version of the Sonnet critic that downgrades weak drafts to CRITIC_HOLD.
---

# /network-critic-here


> **Shell note (Windows):** the commands below use the plugin's Python runner. In bash / WSL / Git-Bash use `"${CLAUDE_PLUGIN_ROOT}/bin/nag" …` exactly as written; in **native PowerShell** substitute the runner with `& "$env:CLAUDE_PLUGIN_ROOT\bin\nag.ps1" …` (same module and args).

The host-token version of the Finder/drafter's **critic** step (issue #50).
`/network-draft-here` saves a draft with only the deterministic *safety* gate
applied (humanize → hard_check); the critic — the **judgment** step that scores
specificity, single-ask discipline, tone, grounded facts, economy, and relevance
— is left to **you** (the host model), via the `networking-critic` subagent
(`model: sonnet`). No `ANTHROPIC_API_KEY` topup needed.

## Flow (per saved draft)

1. **Ground (deterministic, no LLM)** — build the critique grounding for the draft:
   ```
   "${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_critic_host context <draft_id>
   ```
   → JSON: `recipient`, `channel`, `approved_facts`, the `draft`, the six-dimension
   `rubric`, and the `hold_rule`.

2. **Score (host tokens)** — hand that grounding to the `networking-critic`
   subagent. It returns, strictly scored:
   ```json
   {"specificity": 0-5, "one_ask": 0-5, "tone": 0-5, "grounded_facts": 0-5, "economy": 0-5, "relevance": 0-5, "issues": ["..."]}
   ```

3. **Apply (deterministic)** — fold the scores into the verdict and persist it:
   ```
   echo "<scores-json>" | "${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_critic_host apply <draft_id>
   ```
   This runs the recalibrated hold rule (`evaluate_scores`) **and** the
   deterministic AI-tell backstop, writes the `critic_trace`, and downgrades the
   draft OK/SOFT_FLAG → `CRITIC_HOLD` when held (a HARD_FAIL is never touched —
   it's already blocked). → `{draft_id, quality_code, passed, reason}`.

## Why

The score→verdict decision (the hold rule + the AI-tell scanner) and persistence
stay in tested deterministic Python; only the rubric *judgment* moves to host
tokens — the same split as the draft/classify/next-move seams. The API critic
(`critique_draft`) is unchanged as the headless fallback.

## Notes

- The grounding reconstructs the `approved_facts` the drafter saw via the same
  achievement match, so the `grounded_facts` rubric is faithful.
- A held draft surfaces in `/network-check` / the marketer render as `CRITIC_HOLD`
  with the per-dimension scores and issues from the trace.
