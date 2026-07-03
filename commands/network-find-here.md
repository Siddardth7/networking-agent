---
description: Find + classify contacts end-to-end on the HOST Claude's tokens (no API key) — discover (HTTP) → classify each (host tokens) → ingest. The host-token replacement for /network-find's inline LLM classify.
---

# /network-find-here


> **Shell note (Windows):** the commands below use the plugin's Python runner. In bash / WSL / Git-Bash use `"${CLAUDE_PLUGIN_ROOT}/bin/nag" …` exactly as written; in **native PowerShell** substitute the runner with `& "$env:CLAUDE_PLUGIN_ROOT\bin\nag.ps1" …` (same module and args).

The end-to-end host-token Finder (issue #50). `/network-find` discovers contacts
and classifies each with an **API** call; this runs the same discovery (HTTP, no
LLM) and moves the persona/focus/hook judgment onto **your** (the host model's)
tokens via the `networking-classifier` subagent (`model: sonnet`), then saves the
contacts with no Anthropic client. No `ANTHROPIC_API_KEY` topup needed.

## Flow

1. **Discover (deterministic, HTTP — no LLM)** — run the Finder's Apify→Serper
   discovery for the company and emit each raw candidate with its classify
   grounding:
   ```
   "${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_classify_host discover <slug> --limit <N> [--location "<L>"]
   ```
   → a JSON list of `{"candidate": {…}, "context": {…}}`.

2. **Classify each (host tokens)** — for every item, hand the `context` object to
   the `networking-classifier` subagent. It returns
   `{persona, focus_area, hook_signal}` per candidate. Build the ingest payload by
   pairing each original `candidate` with its `classification`:
   ```json
   [{"candidate": {…}, "classification": {"persona": "...", "focus_area": "...", "hook_signal": "..."}}, …]
   ```

3. **Ingest (deterministic — no LLM)** — pipe that payload to `ingest`. It
   canonicalizes each classification (the #5 non-engineer focus override + hook
   trim), generates the hook deterministically, enriches emails (Hunter→Apollo,
   when enabled), saves one `contacts` row per candidate, and advances the
   company NEW→FOUND:
   ```
   echo "<payload>" | "${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_classify_host ingest <slug>
   ```
   → `{"ingested": <N>, "contacts": ["<name>", …]}`.

## Why

Discovery (HTTP), the #5 focus convention, hook generation, and email enrichment
all stay in tested deterministic Python; only the classify judgment moves to the
host model. `ingest_contacts` is already LLM-free once persona+focus+hook are
pre-set, so it runs with `anthropic_client=None`.

## Notes

- `discover` needs a discovery key (`APIFY_API_KEY` or `SERPER_API_KEY`); it errors
  cleanly if neither is set.
- The candidate's `company_slug` is reslugged to the `<slug>` arg on ingest, so the
  contact always lands under the company you name.
- For an already-compiled leads file (no discovery needed), use `/network-import`
  then classify per-row the same way, or `/network-classify-here` for one-offs.
