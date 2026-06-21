---
name: drafter-agent
description: "Draft generation sub-agent for the networking-agent plugin. For each selected contact, generates 3 channel-specific messages in parallel (LinkedIn connection note ≤300 chars, LinkedIn post-connection message, cold email ≤150 words) using persona templates, voice doc, and matched achievements. Invoke when contacts have been selected and drafts need to be generated or revised."
---

# Drafter Agent — Parallel Draft Generation Skill

This skill runs **parallel fan-out draft generation** for selected contacts.

## Source-agnostic input (flexible-input design)

Contacts do **not** have to come from the Serper Finder. Any source — an Apollo
export, an Apify scrape, a Claude Cowork + Claude-in-Chrome capture, or a
hand-compiled file — can be normalized into the canonical contact record and
drafted. Drop a leads file and get drafts:

```
/network-import <file> --company "Joby Aviation" --draft
```

The importer (`src/agents/importer.py`) maps CSV/JSON headers by alias
(`Person Linkedin Url` / `profileUrl` → `linkedin_url`, etc.), fills
`persona`/`focus_area`/`hook` when the file omits them (honoring them when
present), runs the shared `ingest_contacts()` enrich path, marks contacts
SELECTED, and calls `draft_for_contacts` below — unchanged. Only `full_name` is
required per contact; `title`, `linkedin_url`, and a company are recommended.
See `commands/network-import.md` and `docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md`.

## Entry Point

```python
from src.agents.drafter import draft_for_contacts

results = draft_for_contacts(
    contact_ids=[1, 2, 3],         # must be in SELECTED state
    anthropic_client=None,          # optional DI override
    library_path=None,              # optional path to resume_library.yaml
)
# results: dict[contact_id → list[Draft]]
```

`Draft` dataclass fields: `draft_id`, `contact_id`, `channel`, `body`, `subject`, `version`, `quality_flag`.

## Per-Contact Pipeline

For each selected contact, runs 3 **sequential** LLM calls (one per channel) within its thread:

| Channel | Constraint |
|---|---|
| `LINKEDIN_CONNECTION` | ≤ 300 characters total |
| `LINKEDIN_POST_CONNECTION` | No hard limit; relationship-building tone |
| `COLD_EMAIL` | ≤ 150 words body + subject line |

### Prompt Construction

Each channel prompt stacks:
1. **Persona template** (`src/templates/personas/{persona}.md`)
2. **Voice doc** (`~/.networking-agent/voice.md`, if present — skip if missing)
3. **Matched achievements** (top 3 from `match_achievements(focus_area, title, library)`)
4. **Contact context** (name, title, linkedin, email, hook)
5. **Channel constraints** (hard character/word limits)
6. **Anti-phrase nudge** (on regen only)

### Persona templates

| Persona | File |
|---|---|
| `RECRUITER` | `src/templates/personas/recruiter.md` |
| `SENIOR_MANAGER` | `src/templates/personas/senior_manager.md` |
| `PEER_ENGINEER` | `src/templates/personas/peer_engineer.md` |
| `ALUMNI` | `src/templates/personas/alumni.md` |

## Achievement Matching

```python
from src.agents.achievement_matcher import load_resume_library, match_achievements
from src.core.schemas import FocusArea

library = load_resume_library()  # ~/.networking-agent/resume_library.yaml
bullets = match_achievements(FocusArea.COMPOSITE_DESIGN, "Composites Engineer", library, top_n=3)
```

Algorithm: filter projects where `contact_focus_area in project.focus_areas`, then rank bullets by keyword overlap with contact title (case-insensitive substring), return top 3.

If `resume_library.yaml` is missing, returns empty list — prompts still work with "(no achievements matched)".

## Reputation Guardrails

```python
from src.agents.guardrails import check_draft, BLOCKLIST

phrase = check_draft(text)  # returns matched phrase or None
```

Blocklist: `["I noticed", "I admire", "I came across your company", "your impressive work"]`

- **Pass 1 match** → regen once with `"DO NOT USE: '{phrase}'"` nudge in prompt
- **Pass 2 match** → save draft with `quality_flag=True`; surfaced as ⚠️ in Marketer review

## Parallel Execution

```
ThreadPoolExecutor(max_workers=min(6, len(contact_ids)))
```

Each contact's 3 channels run sequentially within its thread (for ordered guardrail logic).
Contacts run in parallel across threads. Cap of 6 workers respects Anthropic Tier 1 (50 RPM).

## Database Writes

- Each channel: `INSERT INTO drafts (contact_id, channel, body, subject, version=1, quality_flag)`
- Contact: `UPDATE contacts SET state = 'DRAFTED'` after all 3 channels inserted

## REVISE Dispatch (Phase 7)

The Marketer's `REVISE <contact-id> <channel> "<feedback>"` verb routes through
`src/agents/dispatch.py` (Phase 7) — NOT through this entry point directly.

For CLI-level single-draft regen, use `/network-draft --revise <draft-id> --feedback "<text>"`.

## Testing

Tests: `tests/test_drafter.py` (9 cases), `tests/test_achievement_matcher.py` (10 cases), `tests/test_guardrails.py` (9 cases).

All LLM calls use DI (`anthropic_client` param); tests pass a mock that returns responses in order.
