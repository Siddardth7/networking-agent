# Anti-AI-detection — the moat thread

**Started:** v0.5.5 (issue #6) · **Status:** cross-cutting thread, extended each release.

## Why this is the moat

Market research behind the roadmap: generic AI outreach is detected and dead —
~**33% of recruiters say they spot AI-written messages within 20 seconds**, and an
AI-shaped note gets ignored (or worse, marks the sender as low-effort). "5 messages
to the right people, written like a human" beats "50 generic AI blasts." So our
**quality is the differentiator**, and "would a recruiter flag this as AI in 20s?"
is a first-class quality gate, not a nice-to-have.

## How it works today (v0.5.5)

Two complementary layers, both in `src/agents/critic.py`, both feeding the same
draft gate (`critique_draft` → `quality_code` → marketer gate):

1. **Holistic judgment — the `tone` rubric dimension (LLM).** Sonnet scores tone
   0–5 with explicit instruction to penalize "AI/recruiter tells." Catches the
   fuzzy, rhythm-and-genericness cases a regex can't.
2. **Deterministic backstop — `scan_ai_tells()` (new in #6).** A curated,
   **high-precision** list of known tells (filler openers, "I came across your
   profile", corporate buzzwords, cover-letter voice, not-only-but-also, etc.).
   Any hit is an **automatic hold** regardless of the LLM scores — a tell a
   recruiter spots in 20s defeats the message. Runs on body **and** subject.

**Why deterministic too?** Same lesson as the classify override (#5): when a rule
is encodable, encode it — don't rely on the LLM to remember it every run. The
scanner can't have an "off day," and it's directly unit-testable.

**Precision over recall (deliberately).** The list only contains phrases that are
almost never in sharp, genuine outreach, so human-grade drafts pass clean (proven
by `TestScanAITells.test_human_grade_drafts_are_clean` against 4-part-voice
samples). A hold is a *review* prompt surfaced to the user, never a silent drop —
so even a rare false positive costs a glance, not a lost message. Recall (catching
subtler tells) is the LLM `tone` dimension's job and grows over future releases.

## Tell categories (v0.5.5)

filler opener · cold-open ("came across / stumbled upon") · reaching-out cliché ·
corporate buzzword (leverage/delve/synergy/spearhead) · "in today's fast-paced" ·
ever-evolving landscape · "testament to" · passion cliché · excitement cliché
("excited to connect") · "resonate with" · "aligns with" · wealth-of-experience ·
closing cliché ("feel free to reach out", "would love the opportunity") ·
cover-letter voice · not-only-but-also · formal transition filler.

## Roadmap for the thread (future releases)

- **Holistic per-channel calibration** — a dedicated LLM `ai_detection` rubric
  dimension (separate from `tone`) with its own floor, calibrated per channel
  (a 30-word LinkedIn note vs a 140-word email read differently).
- **Rhythm / structure analysis** — sentence-length uniformity, rule-of-three
  cadence, em-dash density (channel-aware, since the 4-part voice uses dashes).
- **Scorecard** — an AI-detection analog of the classify scorecard: a labeled set
  of human vs AI drafts, measuring the detector's precision/recall over time.
- **Feed the drafter** — surface specific tells back into the regen prompt so the
  drafter self-corrects before the gate, not just gets held.
