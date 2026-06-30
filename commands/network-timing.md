---
description: Recommend a per-contact send window (next Tue-Thu morning in the contact's local timezone).
---

# /network-timing

Suggest *when* to send. Outreach lands best on **Tue-Thu mornings in the
recipient's local timezone** (~+8% over a random send). Each contact's location
is persisted (migration 008); this verb maps it to a timezone and returns the
next Tue/Wed/Thu at 09:00 local, at or after now.

## Usage

```
/network-timing
```

Prints one line per contact: name, company, location, and the recommended send
time in their local zone.

## How the timezone is resolved

A keyword heuristic (no geocoder) over the location string:

1. **City / country / region** names (`San Francisco`, `London`, `Bengaluru`,
   `Bay Area`) → their IANA zone. Checked first, so a city beats a state code.
2. **2-letter US state / country codes** as whole tokens (`OH`, `TX`, `CA`,
   `UK`) → their zone. Token match, so `CA` never fires inside `Chicago`.
3. **Unknown / missing** location → falls back to **UTC**.

The map is intentionally small — extend `src/cli/network_timing.py` as new
campaign locations appear; the upgrade path is a real geocoder if it gets noisy.

## Exit Codes

| Code | Meaning  |
|------|----------|
| 0    | Success  |

## Implementation

- Module: `src/cli/network_timing.py` → `run_timing(args)`
- Pure logic: `location_to_timezone(location)`, `recommend_send_time(location, now)`
- DB read: `contacts.location` (migration 008), `companies`
- Stdlib `zoneinfo` for DST-correct local times; no new dependency
