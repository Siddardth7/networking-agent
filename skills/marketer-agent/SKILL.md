---
name: marketer-agent
description: "Approval loop agent. Renders drafted contacts with persona, hook, and all 3 channel drafts. Accepts APPROVE, REVISE, SKIP, SHOW verbs. REVISE dispatches to dispatch_revision() via the structured DESIGN §8.3 protocol. On final APPROVE, writes outreach artifact."
---

# Marketer Agent — Approval Loop

The Marketer Agent runs after `draft_for_contacts()` completes. It presents drafted outreach to the user for review and drives the APPROVE/REVISE/SKIP/SHOW loop.

## Entry Point

```python
from src.agents.marketer import run_approval_loop, ApprovalResult

result: ApprovalResult = run_approval_loop(company_id)
```

`ApprovalResult` fields:
- `approved_contact_ids: list[int]` — contacts whose drafts were approved
- `skipped_contact_ids: list[int]` — contacts that were skipped
- `outreach_log_ids: list[int]` — IDs of outreach_log rows written (sent_at=NULL)
- `quit_early: bool` — True if user quit before processing all contacts

## Contact Block Format

For each contact in DRAFTED state:

```
============================================================
Contact [N] — <full_name>
============================================================
  ⚠️  N drafts flagged for quality — review highlighted blocks carefully.   ← if quality_flag=1
  Persona:    <persona>
  Focus:      <focus_area>
  LinkedIn:   <linkedin_url>
  Email:      <email> (✓ verified | unverified)
  Hook:       <hook>
  Signals:    <shared_signals>

  ── LINKEDIN_CONNECTION (v1) ──
  <char/word count>
  ----------------------------------------
    <draft body>

  ── LINKEDIN_POST_CONNECTION (v1) ──
  ...

  ── COLD_EMAIL (v1)  ⚠️  QUALITY FLAG ──
  Subject: <subject>
  <char/word count>
  ...
```

## Verbs

| Verb | Syntax | Effect |
|---|---|---|
| `APPROVE all` | `APPROVE all` | Approve all pending contacts, write outreach_log rows |
| `APPROVE <N>` | `APPROVE 2` | Approve contact N (uses all channel drafts) |
| `REVISE <N> <CHANNEL> "<feedback>"` | `REVISE 1 COLD_EMAIL "Too formal"` | Dispatch revision (see protocol below) |
| `SKIP <N>` | `SKIP 3` | Skip; contact stays DRAFTED |
| `SHOW <N> raw` | `SHOW 2 raw` | Print raw draft body to stdout |
| `quit` / `q` | `quit` | Exit early; `quit_early=True` |

## REVISE Dispatch Protocol (DESIGN §8.3)

When the user types `REVISE <N> <CHANNEL> "<feedback>"`, the Marketer calls:

```python
from src.agents.dispatch import dispatch_revision
from src.core.schemas import DraftDispatchRequest, DraftDispatchResponse

req = DraftDispatchRequest(
    contact_id=<int>,
    channel=<Channel>,
    prior_draft_id=<int>,
    feedback="<user feedback string>",
)
resp: DraftDispatchResponse = dispatch_revision(req)
```

**Input schema** (`DraftDispatchRequest`):
- `contact_id: int`
- `channel: Channel`
- `prior_draft_id: Optional[int]`
- `feedback: Optional[str]`
- `voice_doc_path: Optional[str]`
- `max_attempts: int = 2`

**Output schema** (`DraftDispatchResponse`):
- `status: str` — `"OK"` | `"GUARDRAIL_FLAGGED"` | `"ERROR"`
- `new_draft_id: Optional[int]`
- `new_version: Optional[int]` — always `max(existing_version) + 1`
- `body: Optional[str]`
- `subject: Optional[str]`
- `quality_flag: bool`
- `error_message: Optional[str]`

**User-visible progress** (printed by Marketer before/after dispatch):
```
  Regenerating COLD_EMAIL draft for Alice Eng...
  ✓ New version v2 ready.
```

**Error path**: `"Revision failed: <msg>. Original draft retained."`

**Idempotency**: second REVISE on same (contact, channel) → `version = max + 1 = 3`

## On APPROVE

For each approved contact:
1. `UPDATE drafts SET approved = 1` for all draft_ids
2. `INSERT INTO outreach_log (contact_id, draft_id, channel, sent_at=NULL)`
3. `UPDATE contacts SET state = 'APPROVED'`

After all contacts processed: `UPDATE companies SET state = 'APPROVED'`

## Artifact Write (triggered by `write_artifact`)

After final approval, `src/agents/artifact_writer.py`:
- Path: `~/.networking-agent/drafts/<slug>/<YYYY-MM-DD>-run.md`
- Contains: company header + per-contact section with all 3 final drafts in code blocks
- Company state: `DRAFTED → APPROVED`

## DB State Transitions

```
contacts: DRAFTED → APPROVED  (on APPROVE)
companies: DRAFTED → APPROVED  (when any contact approved)
outreach_log: rows written with sent_at=NULL
```

## Error Handling

| Scenario | Behavior |
|---|---|
| LLM call > 90s | `status=ERROR`; original draft retained |
| Guardrail trips twice | `status=GUARDRAIL_FLAGGED`; flagged draft saved |
| DB write fails | `status=ERROR`; no state change |
| Contact not found | Error printed; loop continues |

## Implementation Files

- `src/agents/marketer.py` — `run_approval_loop()`, `parse_verb()`, `ApprovalResult`
- `src/agents/dispatch.py` — `dispatch_revision()` (DESIGN §8.3 protocol)
- `src/agents/artifact_writer.py` — `write_artifact()`
- `src/core/schemas.py` — `DraftDispatchRequest`, `DraftDispatchResponse`
