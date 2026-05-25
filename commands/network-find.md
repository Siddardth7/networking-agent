---
description: "Discover and enrich contacts at a target company using Serper + Hunter. Classifies persona and generates hooks. Writes contacts to DB and shows selection gate."
---

# /network-find

Run the 5-phase Finder pipeline for a target company, then present the selection gate to choose which contacts to draft outreach for.

## Usage

```
/network-find <company-slug>
```

**Example:**
```
/network-find lockheed-martin
/network-find spacex
/network-find blue-origin
```

`company-slug` must be lowercase with hyphens (e.g. `lockheed-martin`, not `Lockheed Martin`).

## What Happens

1. **Discover** — Searches LinkedIn profiles via Serper using role keywords (quality, structures, composites, manufacturing, materials, additive).
2. **Enrich** — Looks up email addresses via Hunter for each candidate.
3. **Classify** — Calls Claude to assign persona (`RECRUITER / SENIOR_MANAGER / PEER_ENGINEER / ALUMNI`) and focus area (7-way).
4. **Hook** — Generates a personalized connection hook per contact (UIUC alumni → shared employer → title specialty → GENERIC).
5. **Save** — Writes all contacts to the local SQLite DB; company state transitions `NEW → FOUND`.
6. **Selection Gate** — Displays a numbered list; you enter `1,3,4`, `all`, or `none` to select contacts for drafting.

## Prerequisites

Run `/network-check` first to verify API keys and quota are healthy.

## Quota Behavior

- **Serper exhausted** — run aborts, company stays `NEW`, clear error message printed.
- **Hunter exhausted mid-run** — remaining contacts are marked `email=NULL, source_provider=HUNTER_EXHAUSTED`; pipeline continues to selection gate.

## State Machine

```
NEW  →  FOUND  (after Finder pipeline completes)
FOUND  →  SELECTED  (after selection gate, at least one contact chosen)
```

Re-running `/network-find` on a `FOUND` company goes straight to the selection gate (contacts already discovered).

## Implementation

- Finder pipeline: `src/agents/finder.py` → `find_contacts(company_slug, limit)`
- Selection gate: `src/cli/selection_gate.py` → `run_selection_gate(company_id)`
- Skill reference: `skills/finder-agent/SKILL.md`
