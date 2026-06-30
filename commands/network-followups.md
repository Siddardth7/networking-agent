---
description: Schedule capped, value-add follow-ups for no-reply outreach, or list scheduled follow-ups with --list.
---

# /network-followups

Queue timed follow-up touches for outreach that hasn't drawn a reply. A no-reply
send earns a follow-up scheduled a few days after the last touch (the 4-7 day
sweet spot), capped so the cadence stays non-spammy. Research: 2-3 touches lift
reply rate 20-30%+ over a single send.

Scheduling is **gated by the marketer artifact** — only outreach whose company
reached `APPROVED` (the state the artifact write sets) is eligible. The cap is
enforced at schedule time, so a follow-up is never scheduled past it, and a
re-run never double-books an already-pending follow-up.

## Usage

```
/network-followups          # schedule every due follow-up
/network-followups --list   # list scheduled follow-ups (pending + sent)
```

## When a follow-up is due

A prior outreach is scheduled a follow-up only when **all** hold:

| Condition            | Meaning                                                     |
|----------------------|-------------------------------------------------------------|
| Gated                | Company is `APPROVED`/`SENT` (cleared the marketer artifact) |
| No reply             | No `outreach_log` response and no per-contact outcome        |
| Under the cap        | Sent follow-ups `< followup_max_touches` (default 2)         |
| Not already queued   | No pending (unsent) follow-up exists for that outreach       |
| Has a last touch     | The original send (or last sent follow-up) has a timestamp   |

The new touch is scheduled `followup_gap_days` (default 5, within 4-7) after the
last touch.

## Config (`pipeline:`)

| Key                    | Default | Meaning                                          |
|------------------------|---------|--------------------------------------------------|
| `followup_max_touches` | `2`     | Max value-add follow-ups per no-reply outreach   |
| `followup_gap_days`    | `5`     | Days after last touch to schedule a follow-up    |

## Exit Codes

| Code | Meaning  |
|------|----------|
| 0    | Success  |

## Implementation

- Module: `src/cli/network_followups.py` → `run_followups(args)`
- Pure planner: `plan_followups(rows, max_touches=, gap_days=)`
- DB tables written: `followups` (`outreach_log_id`, `scheduled_at`)
- DB tables read: `outreach_log`, `followups`, `contacts`, `companies`
