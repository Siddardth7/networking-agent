# Guided onboarding + coaching (ROADMAP B3, v0.10.0)

**Goal:** a stranger in ANY field can self-onboard — build their own
`profile.yaml`, `voice.md`, and `resume_library.yaml` through a guided
conversation — and be coached on the networking strategy as the agent works.
This is the consumer of everything #61 made configurable, and the last
functional gap before B4 public polish → v1.0.

## Shape: host-token native

The wizard is NOT a Python `input()` loop. The plugin runs inside Claude
Code/desktop — the **host model is the interview surface** (same inversion as
#50): a `/network-setup` command doc drives a conversational interview, and a
deterministic Python bridge does validation + file writes. LLM work (turning
interview answers into a voice doc, turning a pasted resume into provenanced
library bullets) happens on host tokens; Python never guesses.

What already exists and is REUSED, not rebuilt:
- `config.write_default_config` — config.yaml skeleton + 0o600 (scaffold).
- `network_check` — the doctor (keys, DB, voice doc, quotas). Setup ends by
  running it.
- `src/core/profile.py` loader (#61) — the profile validator IS the loader.
- `achievement_matcher.ResumeLibrary` — the resume validator IS the model.
- `config/*.example.yaml` + `voice.example.md` — the templates the host
  interview fills in.
- Rank `rank_reasons`, hook tiers, ask-angle docs — the coaching layer
  surfaces existing explanations; it does not invent a new engine.

## Phases (each its own issue)

### P1 — deterministic setup bridge (ships dark)
`src/cli/network_setup_host.py`, no LLM, no network:
- `status` → JSON per user file (config.yaml, profile.yaml, voice.md,
  resume_library.yaml): exists / valid / short summary (profile name + focus
  areas; project & bullet counts; unfilled REPLACE_ME keys; byte size for
  voice). Invalid files report the validation error, never a traceback.
- `scaffold` → `write_default_config` (created / already-present in JSON).
- `write profile|voice|resume` (content on stdin) → validate with the REAL
  loader/model (profile: YAML mapping through the #61 parser; resume:
  `ResumeLibrary.model_validate`; voice: non-empty, size-cap warning) →
  timestamped `.bak` of any existing file → write. Refuses invalid content
  with the validator's error as JSON. Never touches config.yaml (keys stay
  scaffold + manual edit; the doctor verifies them).

### P2 — `/network-setup` wizard command
The interview: status → scaffold → profile questions (field, identity lines,
school + signals, past employers, target roles, focus areas with
keywords/hooks — the host proposes a taxonomy from the user's field and
refines it with them) → `write profile` → voice questions (fills the Identity
section of `voice.example.md`, keeps the validated rules) → `write voice` →
"paste your resume / projects" → host builds `resume_library.yaml` with
STRICT type provenance (competition/coursework NEVER inflated to industry)
→ `write resume` → `network_check` → point at `/network-run` /
`/network-jobs`. Re-runnable: `status` shows what exists; only rewrite what
the user asks (backups from P1 make this safe).

### P3 — coaching layer
- `/network-coach` command: the strategy playbook as a conversation — why
  alumni-first (the 40-pt rank signal), why ONE specific ask, why 280 chars,
  follow-up cadence (2 touches, 4–7 days), what each reply type means and the
  next move (#19's classify_next_move vocabulary), sponsorship-question
  timing. Grounded in the repo's own validated mechanics, not generic advice.
- Explain-as-it-works: `/network-run` + `/network-jobs` gain a short "coach
  the user" note — when presenting candidates/drafts, the host explains WHY
  in one line each, using data already present (rank_reasons, hook tier,
  assigned ask angle). No new Python.

## Exit (B3 done)
A new user in a non-aerospace field runs `/network-setup`, answers questions,
pastes a resume, and ends with valid profile.yaml + voice.md +
resume_library.yaml (doctor-verified) — then runs a campaign where the agent
explains its choices. Zero regression: no existing flow changes unless the
wizard is invoked.
