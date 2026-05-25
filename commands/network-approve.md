---
description: "Launch the interactive approval loop for drafted contacts. Supports APPROVE, REVISE, SKIP, SHOW verbs. On APPROVE all, writes .md artifact for manual send."
---

# /network-approve

Review drafted outreach messages for a company and approve, revise, or skip them interactively.

## Usage

```
/network-approve <company-slug>
/network-approve <company-slug> --contact <N>
```

**Examples:**
```
/network-approve lockheed-martin
/network-approve spacex --contact 2
```

`--contact N` renders only that numbered contact from the list (single-contact mode).

## What Happens

1. Loads all contacts in `DRAFTED` state for the company.
2. Renders each contact block with persona, LinkedIn/email, hook, and all 3 channel drafts (with char/word counts).
3. Waits for your verb command. Loop continues until `APPROVE all` or `quit`.
4. On final approval: writes `.md` artifact to `~/.networking-agent/drafts/<slug>/<YYYY-MM-DD>-run.md` and transitions company state `DRAFTED → APPROVED`.

## Commands

| Verb | Syntax | Action |
|---|---|---|
| `APPROVE all` | `APPROVE all` | Approve all pending contacts |
| `APPROVE <N>` | `APPROVE 1` | Approve a single contact by list number |
| `REVISE <N> <CHANNEL> "<feedback>"` | `REVISE 2 COLD_EMAIL "Too formal"` | Regenerate one draft with feedback |
| `SKIP <N>` | `SKIP 3` | Skip this contact (leave in DRAFTED state) |
| `SHOW <N> raw` | `SHOW 1 raw` | Print raw draft text for a contact |
| `quit` / `q` | `quit` | Exit the loop without finalizing |

## REVISE Dispatch Protocol

When you type `REVISE`, the Marketer Agent calls `dispatch_revision()` in `src/agents/dispatch.py`:

- Prints `"Regenerating <channel> draft for <name>..."` before the LLM call.
- On success: prints `"✓ New version v<N> ready."` and reprints the contact block.
- On guardrail flag: `⚠️ Revision flagged by quality guardrail (vN saved).`
- On failure: `Revision failed: <msg>. Original draft retained.`

Channels: `LINKEDIN_CONNECTION`, `LINKEDIN_POST_CONNECTION`, `COLD_EMAIL`

## Quality Flags

If any draft has `quality_flag=1`, a `⚠️ N drafts flagged for quality` banner appears at the top of that contact's block. Review flagged blocks carefully before approving.

## State Machine

```
DRAFTED  →  APPROVED  (after APPROVE all or all contacts processed)
```

## Prerequisites

Run `/network-draft <company-slug>` first to generate drafts.

## Implementation

- Approval loop: `src/agents/marketer.py` → `run_approval_loop(company_id)`
- Dispatch protocol: `src/agents/dispatch.py` → `dispatch_revision(req)`
- Artifact writer: `src/agents/artifact_writer.py` → `write_artifact(company_id)`
- Skill reference: `skills/marketer-agent/SKILL.md`
