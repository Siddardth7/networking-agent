---
description: Classify contacts on the HOST Claude's tokens (no API key) — the two-phase flow that moves persona/focus/hook classification off the Anthropic API onto host tokens.
---

# /network-classify-here

The host-token version of the Finder's classify step (issue #50, **option a —
two-phase flow**). Today `find_contacts` classifies each contact *inline* with an
API call; this moves that judgment onto **your** (the host model's) tokens, via
the `networking-classifier` subagent (`model: sonnet`).

## The two-phase flow

1. **Discover (deterministic, no LLM)** — get the raw candidates. For now this
   means an imported leads file (the importer parses any Apollo/Apify/manual file
   without an LLM), or the existing discovery providers:
   ```
   python -m src.cli.network_import <file> --company "<Company>" --validate
   ```
   *(The fully-wired `discover` verb that runs Apify/Serper and emits candidates
   for host classification is the next slice under #50.)*

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

The classify **seam** (grounding + canonicalize + subagent) is in place. The
remaining wiring under #50 — a `discover` verb that emits candidates and an
`ingest` verb that saves host-classified contacts end-to-end — is the next slice.
