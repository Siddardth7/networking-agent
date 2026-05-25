---
description: Show pipeline state per company (state, contact count, draft count, quota remaining). With a company slug, show detailed per-contact view. With --update, record outreach response (PENDING/POSITIVE/NEGATIVE/NO_RESPONSE/IRRELEVANT).
---

# /network-status

Show a snapshot of the networking pipeline: per-company progress, contact and draft counts, quota remaining, and outreach log state.

## Usage

```
/network-status
/network-status <company-slug>
/network-status --update <log-id> --response <VALUE> [--notes "..."]
```

**Examples:**
```
/network-status
/network-status acme-corp
/network-status --update 7 --response POSITIVE --notes "Replied, wants to chat"
/network-status --update 3 --response NO_RESPONSE
```

## Modes

### No arguments — summary table

Prints one row per company with columns:

| Column   | Description                                      |
|----------|--------------------------------------------------|
| SLUG     | Company slug                                     |
| STATE    | Pipeline state (NEW / FOUND / SELECTED / SENT)  |
| CONTACTS | Total contacts discovered                        |
| DRAFTS   | Total drafts generated                           |
| OUTREACH | Total outreach_log entries                       |

Followed by a provider quota section showing remaining queries for `serper` and `hunter` this month.

### With `<company-slug>` — detailed view

Lists every contact at that company with:
- Name, title, contact state
- Each draft: channel, version, quality flag, approval status
- Each outreach log entry: log id, channel, sent date, response, notes

### With `--update` — record outreach response

Updates a single `outreach_log` row. `--response` is required.

Valid response values:

| Value         | Meaning                              |
|---------------|--------------------------------------|
| `PENDING`     | Sent, awaiting reply                 |
| `NO_RESPONSE` | No reply after follow-up window      |
| `POSITIVE`    | Positive reply / interested          |
| `NEGATIVE`    | Declined or not interested           |
| `IRRELEVANT`  | Out-of-scope or wrong contact        |

## Exit Codes

| Code | Meaning                                              |
|------|------------------------------------------------------|
| 0    | Success                                              |
| 1    | Unknown company slug, unknown log id, or bad input   |

## Implementation

- Module: `src/cli/network_status.py` → `run_status(args)`
- DB tables read: `companies`, `contacts`, `drafts`, `outreach_log`, `quota`
- DB tables written: `outreach_log` (response, notes columns)
