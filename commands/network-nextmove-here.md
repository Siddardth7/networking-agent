---
description: Draft the reply-aware next move using the HOST Claude's tokens (no API key) — you write it via the networking-nextmove subagent, the deterministic gate flags any issue.
---

# /network-nextmove-here

The host-token version of `/network-nextmove`: **you** (the host model) write the
next move, so no `ANTHROPIC_API_KEY` topup is needed (issue #50). Sonnet is the
right model, so delegate the writing to the `networking-nextmove` subagent.

## Usage

```
/network-nextmove-here <contact-id> "<their reply, verbatim>"
/network-nextmove-here <contact-id> "<reply>" --move SCHEDULE_CALL
/network-nextmove-here <contact-id> "<reply>" --channel COLD_EMAIL --outcome POC
```

## What you do

1. **Get the grounding** (deterministic — classifies the move, assembles voice +
   the reply, no LLM):
   ```
   python -m src.cli.network_nextmove_host context <contact_id> "<reply>" [--move M] [--channel C] [--outcome O]
   ```
   JSON includes `move` (the classified next move), `move_instruction`, the
   contact facts, `voice_doc`, `fact_discipline`, and `channel_constraints`.

2. **Write the reply** by delegating to the `networking-nextmove` subagent
   (`model: sonnet`), passing the grounding. It returns ONLY the message (and a
   `Subject:` line for cold email).

3. **Gate it** (deterministic humanize + `hard_check`; next moves are not
   persisted — they're printed for you to review and send):
   ```
   printf '%s' "<the message body>" | python -m src.cli.network_nextmove_host gate <CHANNEL>
   ```
   Prints `{"quality_code", "body"}`. On `HARD_FAIL` (leaked placeholder,
   unapproved metric, or length), ask the subagent to fix it and re-gate.

## The move

Classified from the reply (override with `--move`): `THANK_INTRO` (they offered an
intro / outcome=POC), `SPONSORSHIP_QUESTION` (they raised sponsorship/visa),
`SCHEDULE_CALL` (open to talk — the default), `REFERRAL_ASK` (they mentioned
hiring/roles).
