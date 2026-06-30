---
description: Classify contacts on the HOST Claude's tokens (no API key) — the two-phase flow that moves persona/focus/hook classification off the Anthropic API onto host tokens.
---

# /network-classify-here

The host-token version of the Finder's classify step (issue #50, **option a —
two-phase flow**). Today `find_contacts` classifies each contact *inline* with an
API call; this moves that judgment onto **your** (the host model's) tokens, via
the `networking-classifier` subagent (`model: sonnet`).

## The two-phase flow

1. **Discover (deterministic, no LLM)** — get the raw candidates. Either an
   imported leads file (the importer parses any Apollo/Apify/manual file without an
   LLM), or the wired `discover` verb that runs Apify/Serper and emits each
   candidate with its grounding (see `/network-find-here` for the full
   discover→classify→ingest loop):
   ```
   python -m src.cli.network_import <file> --company "<Company>" --validate
   # or, run discovery directly:
   python -m src.cli.network_classify_host discover <slug> --limit <N>
   ```

2. **Classify each (host tokens)** — for each candidate, build the grounding and
   delegate the judgment to the `networking-classifier` subagent:
   ```
   python -m src.cli.network_classify_host context \
     --name "<name>" --title "<title>" --snippet "<snippet>" --company "<slug>"
   ```
   The subagent returns `{persona, focus_area, hook_signal}`.

3. **Canonicalize (deterministic)** — fold the host classification into the exact
   labels the pipeline stores (applies the non-engineer focus override + trims the
   hook signal):
   ```
   python -m src.cli.network_classify_host apply \
     --persona <P> --focus <F> --hook-signal "<S>"
   ```
   → `{persona, focus_area, hook_signal}` ready to ingest.

## Why

Moving classification onto host tokens removes another Anthropic-API dependency,
so the plugin needs no `ANTHROPIC_API_KEY` topup. The deterministic parts —
discovery (HTTP), the #5 focus convention, hook trimming — stay in tested Python;
only the judgment moves to the host model.

## Status

The classify **seam** (grounding + canonicalize + subagent) is in place, and the
`discover`/`ingest` verbs now wire it end-to-end — see `/network-find-here` for
the full discover → classify-per-candidate → ingest loop on host tokens.
