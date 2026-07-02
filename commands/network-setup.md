---
description: Guided onboarding (#77) — a conversational interview that builds ANY user's profile.yaml, voice.md, and resume_library.yaml on the HOST Claude's tokens, validated and written by the deterministic setup bridge. Re-runnable; existing files are backed up before any rewrite.
---

# /network-setup

The onboarding wizard (ROADMAP B3). YOU (the host model) run the interview in
conversation; the `network_setup_host` bridge validates and writes the files.
Ask questions **one section at a time**, show each composed file to the user,
and only call `write` after they confirm. Do not guess facts about the user —
everything in these files must come from their answers or their pasted resume.

## 0. Where things stand

```
python -m src.cli.network_setup_host status
```

→ JSON per file: `config`, `profile`, `voice`, `resume_library` — each with
`exists`/`valid` and a summary (unfilled keys, focus areas, project counts).

- **Nothing exists** → fresh setup: run every step below in order.
- **Some files exist** → update mode: tell the user what's already configured
  (use the summaries), ask which parts they want to (re)do, and run only those
  steps. Every overwrite is automatically backed up to a timestamped `.bak`,
  so rewriting is safe — say so.
- **A file is invalid** → show the `error` message and offer to rebuild that
  file (its step below).

## 1. Config scaffold (keys stay out of the chat)

```
python -m src.cli.network_setup_host scaffold
```

Then tell the user to fill the `REPLACE_ME` keys by **editing the file
directly** (path is in the output) or via env vars (`ANTHROPIC_API_KEY`,
`SERPER_API_KEY` / `APIFY_API_KEY`, optional `HUNTER_API_KEY` /
`APOLLO_API_KEY`). **Never ask the user to paste an API key into the
conversation.** Note: with the host-token flow (`/network-run`,
`/network-jobs`) no Anthropic key is needed — a discovery key (Apify or
Serper) is the only hard requirement.

## 2. Profile interview → profile.yaml

Read `config/profile.example.yaml` (in this plugin's repo) first — it is the
schema, field docs, and a worked example. Then interview, roughly:

1. **Field + goal** — "What field are you in, and what roles are you hunting?"
   (Their answer drives everything below.)
2. **Identity lines** — compose `fallback_identity` (one line: name, degree/
   credential, school, grad date) and `identity_short` (the shortest honest
   self-tag, e.g. "MS CS at Georgia Tech, distributed systems") from their
   answers; confirm both.
3. **School** — `school_name` plus `school_signals`: every lowercase substring
   that marks a fellow alum in a title or LinkedIn URL (full name, common
   abbreviations, campus name). Shared school is the warmest hook — get the
   variants right. `school_name` is used verbatim in the hook phrase
   "we share a {school_name} background" — pick the short form that reads
   naturally there (e.g. "UIUC" works; for "Emory" the phrase reads "a Emory",
   so prefer a variant like "Emory nursing" if it sounds off — read it aloud
   with the user).
4. **Past employers** — `shared_employers`, lowercase; a shared past employer
   is the second-warmest hook. Include common short forms.
5. **Identity markers** — `identity_markers`: lowercase phrases that mean
   "this is my school/program" (school variants + program names). The
   guardrail flags a draft that repeats any of them twice.
6. **Target roles** — `role_keywords`: 5–10 job titles the Finder should
   search for by default.
7. **Focus taxonomy** — YOU propose 3–6 domain focus areas for their field
   (UPPER_SNAKE `name`, a short `description` the classifier reads, resolver
   `keywords`, and optionally a `hook` phrase + `hook_keywords` for
   title-based hooks), then refine with them. Do not add PEER or
   ALUMNI_ACADEMIC — the loader appends those automatically. These same area
   names must be used in resume_library `focus_areas` (step 4).

Compose the YAML (comments welcome — they survive the write), show it, and on
confirmation:

```
cat > /tmp/profile.yaml <<'YAML' … YAML
python -m src.cli.network_setup_host write profile < /tmp/profile.yaml
```

The bridge validates with the real runtime loader and flags unknown keys
(typos) as warnings — surface any warnings to the user and fix before moving
on.

## 3. Voice interview → voice.md

Read `config/voice.example.md`. The `[CUSTOMIZE]` sections are the user's;
everything else (the 4-part model, specificity gate, channel rules, forbidden
phrases) is **validated mechanics — keep it verbatim** unless the user
explicitly objects to a rule.

Interview for the `[CUSTOMIZE]` parts only:
- **Identity** — 2–3 sentences: who they are, what they specialize in, what
  they're seeking, work authorization if relevant. Build it from the profile
  answers; confirm the wording — the drafter uses it verbatim.
- **Tone** — read them the default tone bullets; adjust only what they push
  back on.
- **Cold email prefs** — word limits / CTA style if they care; defaults
  otherwise.
- **Signature** — name, credential line, LinkedIn URL, email.

Show the full file, then `write voice` (same stdin pattern as above).

## 3.5 Persona templates → templates/ (skip only for aerospace users)

The four persona templates (`recruiter.md`, `senior_manager.md`,
`peer_engineer.md`, `alumni.md`) tell the drafter WHO it writes as and the
per-persona strategy. **The built-ins are voiced for the original aerospace
user** — without this step, a non-aerospace user's draft prompts carry the
wrong identity (found live in the B4 trial: the voice doc said one person,
the persona template another).

For each of the four files in `src/templates/personas/`: keep the strategy
skeleton (the 4-part guidance, the rotate-the-ask rules, the tone bullets)
and adapt ONLY the identity line, the recipient framing ("at an
aerospace/space company" → their industry), and field-specific examples.
Write them to `~/.networking-agent/templates/` and add to profile.yaml:

```yaml
templates_dir: ~/.networking-agent/templates
```

(Include it in the step-2 profile write, or re-run `write profile`.) A file
you don't create falls back to the built-in — so do all four.

## 4. Resume → resume_library.yaml

Ask the user to **paste their resume text or a project list**. Convert it to
the `config/resume_library.example.yaml` shape — this is the drafter's
APPROVED FACTS source, so the rules are strict:

- One `project` per distinct project/job; short `id`, honest `title`.
- **`type` provenance is non-negotiable**: COMPETITION / COURSEWORK /
  RESEARCH / INTERNSHIP / INDUSTRY. When unsure, ask — and default DOWN
  (COURSEWORK), never up. The drafter uses `type` to forbid describing
  academic work as employment; mislabeling here fabricates a work history.
- `focus_areas` — the profile's area names from step 2 (plus PEER /
  ALUMNI_ACADEMIC where apt).
- `bullets` — copy the user's achievement lines near-verbatim. **Never invent
  or round a number**; a metric must appear in their pasted text or be
  confirmed by them. Drop superlatives; keep the concrete result.
- `keywords` per bullet — lowercase substrings of the **job titles of people
  who'd care** (they're matched against a contact's title, not the user's
  stack): "backend", "platform", "quality", "icu", …

Show it, then `write resume`. An empty library is accepted with a warning —
but tell the user drafts will have no grounded achievements until it's
filled.

## 5. Verify + hand off

```
python -m src.cli.network_check
```

Walk through any ✗/⚠ lines with the user (unfilled keys are the usual one).
Then point them at the front doors:

- `/network-run <company-slug>` — Campaign mode: referral candidates at a
  target company.
- `/network-jobs <feed.json>` — Application mode: per-posting referral
  candidates from a scored job feed.

If they're new to networking itself, offer `/network-coach` (the strategy —
why alumni-first, why one ask, what to do with each reply).

## Rules for the interviewer (you)

- One section at a time; short questions; use their words in the files.
- Show every file before writing; write only on confirmation.
- Never fabricate: no invented metrics, employers, or dates. If a section has
  no user-supplied content, leave it out rather than filling it.
- Never handle API keys in-conversation (step 1 pattern only).
- Update mode rewrites ONLY what the user asks; backups make it reversible —
  the `write` output includes the `.bak` path, mention it.
