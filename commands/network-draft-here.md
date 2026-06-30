---
description: Draft outreach for SELECTED contacts using the HOST Claude's tokens (no API key) — you write each message via the networking-drafter subagent, then the deterministic gate persists it.
---

# /network-draft-here

Draft on **host tokens**. Unlike `/network-draft` (which runs the Python pipeline
and calls the Anthropic API with a separate key), this command makes **you** —
the host model — do the writing, so no `ANTHROPIC_API_KEY` topup is needed. This
is the issue #50 host-token path; Sonnet is the right model for the writing, so
delegate each draft to the `networking-drafter` subagent.

## Usage

```
/network-draft-here <company-slug>
```

## What you do

For the company's contacts in `SELECTED` state, for each channel
(`LINKEDIN_CONNECTION`, `LINKEDIN_POST_CONNECTION`, and `COLD_EMAIL` **only if
the contact has an email**):

1. **Get the grounding** (deterministic, no LLM):
   ```
   python -m src.cli.network_draft_host context <contact_id> <CHANNEL>
   ```
   This prints JSON: contact facts, `persona_template`, `voice_doc`,
   `approved_facts`, `fact_discipline`, and `channel_constraints`.

2. **Write the message** by delegating to the `networking-drafter` subagent (it
   pins `model: sonnet`), passing the grounding JSON. It returns ONLY the message
   text (and a `Subject:` line for cold email). Follow its rules: 4-part voice,
   fact discipline, exactly one ask, under the channel's length cap, anchored on
   the hook.

3. **Gate + persist** (deterministic — runs humanize + `hard_check` and writes
   the draft, marking the contact `DRAFTED`):
   ```
   printf '%s' "<the message body>" | \
     python -m src.cli.network_draft_host save <contact_id> <CHANNEL> --subject "<subject>"
   ```
   It prints `{"draft_id", "quality_code", "body", "subject"}`. If
   `quality_code` is `HARD_FAIL`, the message leaked a placeholder, stated an
   unapproved metric, or busted the length cap — ask the subagent to fix the
   named problem and re-save.

## Finding the contacts

```
python -m src.cli.selection_gate <company-slug>   # lists SELECTED contacts + ids
```
or query directly for `state = 'SELECTED'` contacts of the company.

## After drafting

Hand off to the approval loop the same way `/network-run` does — review each
draft's `quality_code`, then `/network-approve`.

## Why this exists

Distributing the plugin shouldn't require users to fund a separate Anthropic API
account. When the plugin runs inside Claude Code / desktop / app, the host
session's tokens cover the writing. The deterministic safety gate
(`hard_check`) stays in Python regardless of who wrote the text.
