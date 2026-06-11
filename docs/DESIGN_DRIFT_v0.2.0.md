# DESIGN.md drift notes — v0.2.0

The authoritative DESIGN.md lives in the parent project directory, outside
this repository, and is intentionally not edited from here. This file records
where the v0.2.0 implementation has diverged from that design so the next
DESIGN revision can fold these in.

## §6 Hook generation (design: 5-tier priority, D10)

Design said: UIUC > past employer > title specialty > company news (v0.2) >
GENERIC.

Implemented (v0.2.0):

- **Tier 0 (new):** a specific signal extracted by the classifier from the
  LinkedIn snippet, gated by an `is_acceptable_hook` shape whitelist.
- Tier 1 UIUC, Tier 2 shared employer, Tier 3 title specialty — as designed.
- **Tier 3.5 (new):** title-derived hook (`your work as <title>`) before
  GENERIC.
- **Company news is NEVER a hook.** The deferred Tier-4 was implemented and
  then removed after the 2026-06-06 run pasted raw news snippets into hooks
  (DRAFTER_AUDIT_2026-06-06 §4.1). News is stored in `shared_signals` as
  phrasing material only, and a verbatim-news detector rejects news-shaped
  strings in every tier.

## §6 Reputation guardrails (design: 4-phrase blocklist)

Implemented as a layered gate: merged voice.md forbidden-phrase blocklist,
deterministic hard checks (placeholder tokens, numeric provenance against
APPROVED FACTS, config-driven length limits), generation-fault regens
(placeholder / multi-ask / redundant intro / opener variety), and a Sonnet
critic (6-dimension rubric) whose trace persists to `drafts.critic_trace`.
The design's single quality_flag became a 4-state `quality_code`
(OK / SOFT_FLAG / HARD_FAIL / CRITIC_HOLD) enforced by the marketer gate.

## §4 Drafter (design: per-contact isolation)

A thread-safe per-run `OpenerRegistry` is now shared across contact workers
to enforce cross-contact opener variety (`quality.opener_max_repeats`,
default 2). Cold email is skipped when the contact has no address.

## §2 Schema

Migrations 002 (`drafts.quality_code`) and 003 (`drafts.critic_trace`) extend
the designed 6-table schema.

## Models

Design assumed Haiku for all LLM calls. v0.2.0 uses Haiku for generation and
classification, and Sonnet (`SONNET_MODEL`) for the critic pass.

## File locations

`voice.md` and `resume_library.yaml` now resolve relative to the config file
(honoring `NETWORKING_AGENT_CONFIG`) instead of a hardcoded
`~/.networking-agent/`.
