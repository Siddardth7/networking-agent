# networking-agent — Roadmap & Version Ladder

**Last updated:** 2026-06-21 · **Current:** v0.4.0 shipped; flexible-input +
producer contract on `main` unreleased (→ v0.5.0).

This is the canonical plan — it replaces ad-hoc, feature-by-feature work. Derived
from the planning audit of 2026-06-21 (tasks: current-state, adaptability,
release-readiness, gap analysis).

---

## Strategic direction (decided 2026-06-21)

- **Audience:** *Both, in sequence.* **Phase A** = rock-solid for Sid's live
  campaign (prove it). **Phase B** = generalize + public-ready.
- **Adaptability target:** *fully generic, guided onboarding* (any user, any
  field/school) — the Phase B goal.
- **Distribution:** *free open-source on GitHub* (docs + contribution-friendly).
- **Timeline:** *using it now / imminent* → **harden for Sid first**;
  generalization waits.

**Phase A immediate focus (Sid's words):** complete *all* input sources and bring
the **Finder up to the same quality bar as the Drafter** — audited, validated,
robust — not merely "the import path exists."

---

## Where we are (snapshot)

- 8 releases in ~4 weeks (v0.1.0 → v0.4.0). 3-agent pipeline
  (Finder → Drafter → Marketer) + a new source-agnostic import layer.
- ~7,800 LOC, 562 tests, ~90% coverage.
- **Drafter: mature** — 4-part voice, 4 personas, ask-rotation, humanizer,
  guardrails, critic; 3 audit docs + live trial scorecards (AST, Sierra).
- **Finder: functional but unaudited** — 34 tests, but *no* audit doc, *no*
  classify-accuracy scorecard, *no* live trial. This is the gap Phase A closes.
- **Adaptability: hardcoded to Sid** in ~8 code spots (aerospace roles, UIUC
  signals, FocusArea taxonomy, employer list, identity terms, a fallback that
  literally names Sid, persona-template framing, ask-rotation school angle).
  Infrastructure is generic; domain knowledge is baked in.
- **Public-release: no-go today** — blockers are (1) the hardcoding and (2) no
  proven end-to-end live run of the current path. Neither blocks Sid's own use.

---

## Phase A — Harden for Sid (NOW)  ·  v0.5 → v0.7

Theme: **all inputs + Finder to Drafter parity + proven on a live campaign.**

### A1 · All input sources, live-validated *(v0.5)*
Flexible-input is built + unit-tested; each source needs a real round-trip →
drafts (today only mocked):
- Serper (works), Apollo CSV (real export), Apify JSON, manual CSV/JSON,
  Cowork+Chrome capture. Per-source `log()` of contributions; robust cross-source
  dedup. Add **PDL free (100/mo)** as a second free provider behind Serper.

> **Releases are deliberately small** so quality never drops from cramming. Sid
> can *use* the agent from v0.5; every release after that sharpens effectiveness
> **while he runs his campaign** (his live use is the proving ground). Phase A is
> a continuous improvement arc, not a gate he waits behind.

### A2 · Finder audit + classify quality *(v0.5.5)*
First half of "develop the Finder like the Drafter":
- **Finder audit** (`FINDER_AUDIT` doc) — defects in discovery, classify, hook.
- **Classify-accuracy scorecard** — a labeled set; measure persona + focus
  precision/recall across all 4 personas; iterate to a bar. Today it's tested for
  *mechanics*, not *accuracy*.
- Begin the **anti-AI-detection thread** (cross-cutting) — a critic dimension:
  "would a recruiter flag this as AI in 20s?" (Our quality is the moat — research
  says 33.5% of recruiters spot AI in 20s.)

### A3 · Finder discovery + hook quality *(v0.6.0)* — completes Finder→Drafter parity
- **Discovery improvements** — config-driven/broadened role keywords, location
  handling, best-effort-to-N accumulation (no silent caps).
- **Hook quality on imported data** — hooks must work on headline-grade `about`
  text (Cowork) + Apollo rows, not just Serper snippets.
- **Live Finder trial + scorecard** on real companies (the AST-equivalent).

### A4 · Warm-path / referral-likelihood ranking *(v0.6.5)* — highest-leverage add
Rank captured contacts by who's actually likely to *help*: alumni, 1st/2nd-degree,
recent joiners, posts-about-hiring, team-matches-target-role, recruiter-for-req.
Operationalizes the proven rule **"5 to the right people > 50 generic."** A
targeting-intelligence layer on the Finder. (Market research, Tier-1 #1.)

### A5 · Email channel *(v0.7.0)*
**Hunter email-pattern inference** (free, 50/mo) — fills the *uncapped* cold-email
channel that offsets LinkedIn's ~100-invite/week ceiling. (Roadmap #3.)

### A6 · Reply / outcome tracking *(v0.7.5)*
Mark which contacts replied / yielded a POC / gave a sponsorship answer — the
feedback signal that lets us measure "did it work" and feeds the next features.

### A7 · Follow-up sequencing + timing *(v0.8.0)*
Timed, non-spammy multi-touch (no reply → value-add follow-up at 4–7 days,
capped) + **timing intelligence** (optimal send window in the contact's local
timezone — location is already captured). Research: 2–3 follow-ups = 20–30%+ vs
single-touch; Tue–Thu mornings +8%. (Tier-1 #2 + Tier-2 #5.)

### A8 · Conversation continuation *(v0.8.5)* — Phase A done
"They replied — now what?": draft the next move — the referral ask, the
sponsorship question, scheduling the chat. The hardest moment for the target
audience. (Tier-1 #3.)

**Phase A exit gate (v0.8.5):** from any input, the Finder produces relevant,
correctly-classified, well-hooked, *ranked* contacts; the agent runs the full
loop (reach → follow-up → continue → outcome) and Sid's campaign is proven +
measured on live data.

---

## Phase B — Generalize + open-source (AFTER Phase A)  ·  v0.9 → v1.0

Theme: **anyone can adopt it to their own profile.**

### B1 · De-hardcode → config *(v0.9.0)* ✅ SHIPPED (#61, Application-mode P4)
Move the ~8 hardcoded spots from code to config: role keywords, shared employers,
school signals, identity terms, the Drafter fallback name, persona template
school-framing, ask-rotation school angle. → `src/core/profile.py` +
`~/.networking-agent/profile.yaml`; the built-in default profile reproduces the
original aerospace configuration byte-for-byte.

### B2 · Generic focus-area taxonomy *(v0.9.5)* ✅ SHIPPED (#61, with B1)
The hardest item — `FocusArea` spans the DB, the classifier schema, and the
achievement matcher. Make it config-driven (the user declares their focus areas)
so the classifier + matcher work for any field. → the profile's `focus_areas`
taxonomy drives the classifier schema (API + host), the achievement matcher,
Tier-3 hooks, and the ranker's `target_focus` (resolved from free-form
`function`/`target_keywords` via `resolve_target_focus`).

### B3 · Guided onboarding + coaching *(v0.10.0)* ✅ SHIPPED (epic #75)
- **Setup wizard** that builds *any* user's profile — voice.md, resume_library,
  target roles, school, focus areas, employers. The "fully generic" goal.
  → `/network-setup` (host-driven interview) + the `network_setup_host`
  bridge (validated writes, backups); README quick start leads with it.
- **Coaching layer** — the agent explains the strategy as it works (why
  alumni-first, why one ask, what to say on reply). Tool → coach for people who
  don't know how to network. (Tier-2 #4.) → `/network-coach` playbook +
  one-line whys in `/network-run` and `/network-jobs`.

### B4 · Public polish *(v0.10.5)* ✅ SHIPPED (#26/#83/#84)
- At least one **live-API smoke test** (everything is mocked today).
  → `tests/test_live_smoke.py` (opt-in, doubly gated; Serper + Anthropic
  PASS live).
- README `<your-username>` fix, multi-domain examples, `CONTRIBUTING` guide,
  plugin listing. → CONTRIBUTING.md; README badges/prereqs/examples brought
  current + multi-domain; plugin.json field-agnostic.
- **Validate end-to-end on a non-aerospace, non-UIUC profile.**
  → `docs/TRIAL_B4_NONAEROSPACE_2026-07-02.md` — PASS on live discovery
  (backend-SWE persona vs Cloudflare); found + fixed the wizard's missing
  persona-template step.
- *Candidate:* role/req targeting + early-applicant combo (Tier-2 #7).
  → role/req targeting shipped via Application mode (#57/#61); the
  early-applicant timing signal stays a post-1.0 candidate.

**Phase B exit gate = v1.0 = PUBLIC RELEASE (dev/CLI, open-source):** a stranger
in any field can install (via the plugin/CLI), self-onboard, and run it
successfully.

---

## Distribution track (parallel — to design, NOT on the 0.x ladder)

The dev/CLI path above is the **low-effort, clean line to v1.0**. But a Claude
Code CLI plugin reaches *developers*, not the broad job-seeker audience (who use
ChatGPT because it's a chat box). Reaching non-devs needs a **separate, more
accessible surface** — conversational/no-CLI, a hosted wizard, or a web/Chrome
front end. The Cowork + Chrome work is the first step toward this.

**Status: to be designed together** — a deliberate product decision, kept off the
0.x engineering ladder so it doesn't block or bloat the dev releases. It likely
becomes its own product line (a "v2" surface) layered on the same engine.

---

## Version ladder

| Version | Theme | Key deliverables | Phase |
|---|---|---|---|
| **v0.5.0** | All Inputs | Ship flexible-input; live-validate every source (Apollo/Apify/manual/Cowork); per-source logging; PDL-free fallback | A |
| **v0.5.5** | Finder Audit + Classify | `FINDER_AUDIT` + classify-accuracy scorecard + fixes; start anti-AI critic dimension | A |
| **v0.6.0** | Finder Discovery + Hook | Discovery improvements (config keywords, location, best-effort-to-N) + hook-on-imported + live Finder trial → Finder = Drafter parity | A |
| **v0.6.5** | Warm-path Ranking | Rank contacts by referral-likelihood ("5 to the right people") | A |
| **v0.7.0** | Email Channel | Hunter email-pattern inference (uncapped channel) | A |
| **v0.7.5** | Reply / Outcome Tracking | Mark replied / POC / sponsorship — the feedback signal | A |
| **v0.8.0** | Sequencing + Timing | Timed multi-touch follow-ups + local-timezone send windows | A |
| **v0.8.5** | Conversation Continuation | "They replied — now what" drafting | **A done** |
| **v0.9.0** | De-hardcoded | 8 hardcoded spots → config | B |
| **v0.9.5** | Generic Taxonomy | Config-driven focus-area taxonomy | B |
| **v0.10.0** | Onboarding + Coaching | Setup wizard builds any profile; coaching layer | B |
| **v0.10.5** | Public Polish | Live smoke test; docs/CONTRIBUTING/examples; non-aerospace validation | B |
| **v1.0.0** | Public Release | Open-source launch (dev/CLI) — stranger in any field can self-onboard + run | **PUBLIC GATE** |

*(Separately: the non-dev distribution surface — see "Distribution track" — is a
parallel product decision, not a 0.x version.)*

---

## Cross-cutting threads (woven across releases, not their own version)

- **Anti-AI-detection moat** — humanizer + a critic "would-a-recruiter-flag-this"
  dimension; our quality is the differentiator (starts v0.5.5).
- **Test realism** — move from all-mocked toward at least one live-API smoke path
  (lands by v0.10.5).

## Out of scope / opportunistic (post-1.0)

- Free **provider rotation** across multiple sources (roadmap #4).
- Apify **BYO-key scraper** lane (opt-in, ToS-gated).
- **Multi-path to a human** — mutual-connection intros, alumni email beyond LinkedIn.
- Campaign **analytics / dashboard**.
- The Cowork + Chrome producer is built in Cowork, not this repo; this plugin
  stays its sink (`docs/CHROME_PRODUCER_CONTRACT.md`).

## Definition of done

**Per-issue and per-version gates** (ponytail → dedicated tester → 95% line+branch
learning loop → `/code-review` → ponytail-review): see
[`DEFINITION_OF_DONE.md`](DEFINITION_OF_DONE.md). Work is tracked as GitHub issues
+ milestones; pinned issue #1 mirrors that contract.

### Per-phase exit gates

- **Phase A done (v0.8.5):** from any input, the Finder yields relevant,
  correctly-classified, well-hooked, *ranked* contacts; the full loop (reach →
  follow-up → continue → outcome) runs and Sid's campaign is proven + measured
  on live data.
- **Phase B done (v1.0):** domain-agnostic, self-onboarding, open-source, proven
  on a non-Sid profile (via the dev/CLI surface).

> Market thesis + feature rationale behind these: `docs/MARKET_GAP_AND_FEATURE_IDEAS_2026-06-21.md`.
