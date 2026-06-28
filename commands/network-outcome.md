---
description: Record a per-contact outreach outcome (REPLIED / POC / SPONSORSHIP_YES / SPONSORSHIP_NO / DECLINED), or list all recorded outcomes with --list.
---

# /network-outcome

Capture the feedback signal for a contact — did they reply, yield a point of
contact, or give a sponsorship answer? This is the relationship-level outcome
(distinct from a single message's `outreach_log` response), and it's the data
that later tunes the referral-ranking weights.

## Usage

```
/network-outcome <contact-id> <OUTCOME> [--notes "..."]
/network-outcome --list
```

**Examples:**
```
/network-outcome 42 REPLIED --notes "Answered on LinkedIn, friendly"
/network-outcome 42 POC --notes "Intro'd me to the hiring manager"
/network-outcome 42 SPONSORSHIP_YES --notes "Confirmed they sponsor H-1B"
/network-outcome 17 DECLINED
/network-outcome --list
```

## Outcomes

| Value             | Meaning                                            |
|-------------------|----------------------------------------------------|
| `REPLIED`         | Responded, no stronger signal yet                  |
| `POC`             | Yielded a point of contact / referral / intro      |
| `SPONSORSHIP_YES` | Confirmed sponsorship is available (the goal)      |
| `SPONSORSHIP_NO`  | Answered: no sponsorship                           |
| `DECLINED`        | Not interested / no                                |
| `NONE`            | Default — nothing recorded yet                     |

## Exit Codes

| Code | Meaning                                       |
|------|-----------------------------------------------|
| 0    | Success                                       |
| 1    | Invalid outcome value or unknown contact id   |

## Implementation

- Module: `src/cli/network_outcome.py` → `run_outcome(args)`
- DB tables written: `contacts` (`outcome`, `outcome_notes`, `outcome_at` — migration 007)
- DB tables read: `contacts`, `companies`
