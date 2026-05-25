---
name: finder-agent
description: "Contact discovery sub-agent. Runs 5 phases: search LinkedIn via Serper, enrich emails via Hunter, classify persona and focus area via Claude, generate personalized hook. Invoke before drafting outreach for a target company."
---

# Finder Agent — Contact Discovery Skill

This skill runs the **5-phase contact discovery pipeline** for one target company and
persists results to the local SQLite state database.

## Entry Point

```python
from src.agents.finder import find_contacts

contacts = find_contacts(
    company_slug="lockheed-martin",  # lowercase-hyphenated
    limit=5,                          # max contacts to discover
    # Optional DI overrides for testing:
    serper_provider=None,
    hunter_provider=None,
    anthropic_client=None,
)
```

Returns `list[ContactCandidate]` with enriched `persona`, `focus_area`, and `email` fields.

## Phases

### Phase 1 — Discover
`SerperProvider.search_linkedin_profiles(company, role_keywords, limit)` issues a
`site:linkedin.com/in "<company>" (role OR keywords)` query against the Serper API.
Returns `list[ContactCandidate]` with `full_name`, `title`, `linkedin_url`.

**On `QuotaExhausted`:** propagates to caller — company stays `NEW`.

### Phase 2 — Enrich
`HunterProvider.find_email(full_name, company_domain)` looks up an email per candidate.

**On `QuotaExhausted` mid-loop:**
- Remaining contacts get `email=None, source_provider='HUNTER_EXHAUSTED'`.
- Pipeline continues — partial emails are better than no contacts.

### Phase 3 + 4 — Classify (single Claude call)
`_classify_contact(candidate, company_slug, anthropic_client)` calls
`claude-haiku-4-5-20251001` with `tool_choice: {type: "tool", name: "classify_contact"}`.

Extracts two fields from the `tool_use` block in `response.content`:
- `persona`: `RECRUITER | SENIOR_MANAGER | PEER_ENGINEER | ALUMNI`
- `focus_area`: `COMPOSITE_DESIGN | STRUCTURAL_ANALYSIS | MANUFACTURING | MATERIALS | ADDITIVE | PEER | ALUMNI_ACADEMIC`

Falls back to `(PEER_ENGINEER, PEER)` if no `tool_use` block is returned.

### Phase 5 — Hook Generation
`_generate_hook(candidate)` applies 5-tier priority (deterministic, no LLM call):

| Tier | Signal | Hook text |
|------|--------|-----------|
| 1 | "uiuc" / "university of illinois" / "urbana-champaign" in title or URL | "we share a UIUC background" |
| 2 | Shared employer word in title (tata, ge, boeing, lockheed, airbus, honeywell) | "you also spent time at {Employer}" |
| 3a | "composite" / "carbon fiber" in title | "your composites work" |
| 3b | "structural" / "structures" / "stress" / "loads" / "fea" / "airframe" | "your structures work" |
| 3c | "quality" / "mrb" / "supplier" / "manufacturing engineer" / "production" | "your manufacturing and quality background" |
| 3d | "materials" / "metallurgy" / "alloy" / "coating" | "your materials science background" |
| 3e | "additive" / "3d print" | "your additive manufacturing work" |
| 4 | Company news signals — *deferred to v0.2* | — |
| 5 | (fallback) | "GENERIC" |

## Database Writes

- Each contact: `INSERT INTO contacts (company_id, full_name, title, persona, focus_area, linkedin_url, email, email_verified, source_provider, hook)`.
- Company: `UPDATE companies SET state = 'FOUND'` after all contacts saved.
- Idempotency: `DELETE FROM contacts WHERE company_id = ? AND state = 'NEW'` at the start of each run clears any partial contacts from a previous failed attempt.

## Selection Gate

After `find_contacts()` returns, the command layer calls:

```python
from src.cli.selection_gate import run_selection_gate

selected_ids = run_selection_gate(company_id)
```

Presents a numbered list. Accepts `"1,3,4"`, `"all"`, or `"none"`. Invalid input reprompts.
Returns `list[int]` of selected contact DB ids. Marks chosen contacts `selected=1, state='SELECTED'`
and company `state='SELECTED'`.

## State Machine

```
NEW  →  FOUND     after find_contacts() completes successfully
FOUND  →  SELECTED  after run_selection_gate() with ≥1 contact chosen
```

## Quota Awareness

Check quota before running:

```python
from src.providers.quota_manager import QuotaManager
qm = QuotaManager()
print(qm.remaining("serper"))   # Serper queries left this month
print(qm.remaining("hunter"))   # Hunter lookups left this month
```

Or use `/network-check` which surfaces these values.

## Testing

Tests: `tests/test_finder.py` (16 cases), `tests/test_selection_gate.py` (20 cases).

All providers accept optional DI params (`serper_provider`, `hunter_provider`, `anthropic_client`)
for hermetic unit tests without live API calls.
