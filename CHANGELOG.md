# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

## [Unreleased]

## [0.9.0] - 2026-06-30

### Added
- **Host-token default-flow orchestration — `/network-run` (issue #50).** Makes
  the host-token path the **default** for the full pipeline; the Anthropic-API
  orchestrator (`run_pipeline`) becomes the explicit `--api` headless fallback.
  Added `network_run_host` — a deterministic, read-only run **planner** (`plan
  <slug>` → `{company, state, next, items}` mapping the company's pipeline state to
  the next host action `discover | select | draft | approve | done` and the work
  items it operates on; mirrors the `run_pipeline` state machine but advises the
  host loop instead of executing it) plus a `select <slug> --ids 1,3,5` verb that
  persists the selection (the one state write the host's select step needs, since
  the interactive selection gate has no CLI entrypoint). Rewrote `/network-run` to
  drive the loop on host tokens — `plan` → `/network-find-here` → `select` →
  per-contact×channel `/network-draft-here` + inline `/network-critic-here` →
  `/network-approve` — fully resumable from any state. Planner CLI 100%. The whole
  LLM surface now runs on host tokens by default; no `ANTHROPIC_API_KEY` topup.
- **Host-token critic (issue #50).** Moves the Layer-4 critic *judgment* (the
  six-dimension rubric scoring, previously a Sonnet `tool_use` call inside
  `critique_draft`) onto host tokens. Refactored the post-LLM decision out of
  `critique_draft` into a shared pure `apply_critique(data, body, subject)` (score
  coercion + the recalibrated `evaluate_scores` hold rule + the deterministic
  AI-tell backstop — same verdict for the API and host paths); added
  `build_critique_context(body, contact, channel, source_facts, subject)`
  (grounding: recipient, channel, approved facts, the draft, the rubric, and the
  hold rule — no LLM), a `networking-critic` `model: sonnet` subagent, a
  `network_critic_host` CLI bridge (`context <draft_id>` | `apply <draft_id>`,
  scores on stdin), and the `/network-critic-here` command. `apply` persists the
  trace and downgrades a saved draft OK/SOFT_FLAG → `CRITIC_HOLD` when held (never
  touching a HARD_FAIL), matching the inline drafter precedence. The grounding
  reconstructs the approved facts the drafter saw via `build_draft_context` (no
  duplicate fact assembly). `critic.py` 100%, bridge CLI 100%. API path unchanged
  as the headless fallback.
- **Host-token find — end-to-end discover/ingest wiring (issue #50).** Makes the
  classify seam end-to-end on host tokens. Added two verbs to `network_classify_host`:
  `discover <slug> --limit N [--location L]` runs the Finder's Apify→Serper
  discovery (HTTP, no LLM) and emits each raw candidate paired with its
  `build_classify_context` grounding; `ingest <slug>` reads the host
  classifications on stdin, canonicalizes each via `apply_classification`,
  generates the hook deterministically, enriches emails (Hunter→Apollo), and saves
  the contacts through `ingest_contacts` with **no Anthropic client** (already
  LLM-free once persona+focus+hook are pre-set), then advances the company
  NEW→FOUND. Factored provider-building out of `find_contacts` into shared
  `build_discovery_chain` / `build_email_providers` so both paths build providers
  identically. Added the `/network-find-here` command (discover → classify-per-
  candidate via the `networking-classifier` subagent → ingest). Bridge CLI 100%.
- **Host-token classification seam (issue #50, two-phase flow — option a).** Moves
  the Finder's persona/focus/hook classify judgment off the Anthropic API onto
  host tokens. Refactored the deterministic post-processing out of
  `_classify_contact` into a shared, pure `apply_classification(raw_persona,
  raw_focus, raw_hook_signal)` (enum coercion + the #5 non-engineer focus override
  + hook trim — same labels for both the API and host paths); added
  `build_classify_context(candidate, company_slug)` (grounding, no LLM), a
  `networking-classifier` `model: sonnet` subagent, a `network_classify_host` CLI
  bridge (`context` | `apply`), and the `/network-classify-here` command. Seam
  fully covered (bridge 100%); the `discover`/`ingest` auto-wiring that makes it
  end-to-end is the next slice under #50.
- **Host-token next-move drafting (issue #50).** The reply-aware next move now
  has a host-token path too: `build_next_move_context` (deterministic — classifies
  the move, assembles voice + reply + channel constraints, no LLM), a shared
  `gate_host_text` safety gate (extracted from `save_host_draft` so the
  humanize→`hard_check` gate is one source of truth), the `networking-nextmove`
  `model: sonnet` subagent, a `network_nextmove_host` CLI bridge (`context` |
  `gate`), and the `/network-nextmove-here` command. Writes the reply on host
  tokens; the gate flags placeholders/fabrication/length. Bridge CLI 100%.
- **Host-token drafting — usable vertical slice (issue #50).** A JSON-in/out CLI
  bridge (`src/cli/network_draft_host.py`: `context <id> <CHANNEL>` →
  grounding JSON, `save <id> <CHANNEL>` ← body on stdin → gated-draft JSON) plus
  the `/network-draft-here` command let the host model drive drafting on its own
  tokens: get grounding → write via the `networking-drafter` sonnet subagent →
  deterministic gate persists it. No `ANTHROPIC_API_KEY` needed for this path.
  Bridge CLI 100% line+branch.
- **Host-token drafting seam (issue #50, first slice).** Toward running the
  plugin's LLM work on the *host* Claude's tokens (no separate `ANTHROPIC_API_KEY`
  topup) when it runs inside Claude Code / desktop / app. Two deterministic
  helpers in the drafter — `build_draft_context(contact_id, channel)` assembles
  the full grounding (contact facts, persona template, voice doc, approved facts,
  fact discipline, channel constraints) with **no LLM call**, and
  `save_host_draft(...)` runs the same humanize → `hard_check` safety gate on
  host-produced text before persisting — plus a `model: sonnet`
  `networking-drafter` subagent that does the writing on host tokens. The
  existing API path is unchanged (kept as the headless fallback). Seam fully
  covered; the inversion + command rewiring tracks under #50 (v0.9.0).
- **Phase A exit-gate harness (issue #20, P0 — partial).** New
  `src/eval/exit_gate.py` measures whether the *whole* Phase A loop connects on
  live data: Gate 1 = the Finder quality bar (the #10 scorecard PASS + contacts
  ranked); Gate 2 = loop completeness (reach → follow-up → continue → outcome
  each produced its artifact). Pure `evaluate_exit_gate` aggregator +
  `ExitGateReport.render_markdown` (100% covered) + a `run_exit_gate_trial` live
  entrypoint (isolated DB, real APIs — production state untouched). The
  *documented live run* that finalizes #20's acceptance is pending (blocked on
  Anthropic API credit at build time); **#20 stays open** until it lands.
- **Reply-aware next-move drafting (issue #19, A8).** The hardest moment —
  "they replied, now what?" — now has a drafter. `/network-nextmove <id>
  "<their reply>"` classifies the next move from the reply text + recorded
  outcome (deterministic, goal-advancing precedence: take the intro →
  `THANK_INTRO`, sponsorship mention → `SPONSORSHIP_QUESTION`, open to talk →
  `SCHEDULE_CALL`, hiring/roles → `REFERRAL_ASK`; warm reply defaults to a call)
  and drafts it in voice through the same humanize → `hard_check` → critic gates
  as a cold message. `--move` / `--channel` override the heuristic; the printed
  `[QUALITY_CODE]` flags anything not `OK`. Pure `classify_next_move` +
  `draft_next_move` in the drafter; CLI at 100%, drafter additions fully
  branch-covered.

### Changed
- **Import-layer hardening (issue #24, audit + tech-debt).** Two findings from
  auditing the source-agnostic ingest, both fixed: (1) **cross-source dedup** —
  the importer and the Finder each normalized LinkedIn URLs differently and
  neither stripped scheme/`www.`/query, so the *same* person from two sources
  (`https://www.linkedin.com/in/jane/` vs `http://linkedin.com/in/jane?utm=x`)
  slipped through as two contacts; both now share one `canonical_linkedin_url`
  (in `src.core.slug`, the same "one canonical X" home as the D8 slug fix). (2)
  **malformed JSON** — `_read_rows` now wraps a `JSONDecodeError` in a clean
  `ContactImportError` so the import path fails the way the validator already
  reports, instead of leaking a raw decode error. `importer.py` 93% → **100%**
  line+branch (alias-map null/blank values, bare-object and scalar JSON,
  existing-company reuse, client build, draft-on-import all now covered);
  acceptance "import layer at 95% branch incl. malformed-input paths" met.

### Fixed
- **Drafter/critic contradiction on "came across" (issue #65, found in
  validation).** The voice guide and every persona template deliberately model
  "came across {a specific thing}" as honest context-setting ("came across your
  hiring post for {Role}"), but the critic's `scan_ai_tells` flagged *any* "I came
  across" as a cold-open tell → an automatic `CRITIC_HOLD` on a draft written to
  spec. Narrowed the tell to "stumbled upon" only (the genuine cliché); the
  generic company version ("I came across your company") stays hard-failed by the
  guardrail forbidden-phrase list. The scanner and the voice guide now agree.
- **Host-token run loop stalled at draft→approve (issue #50, found in
  validation).** The `network_run_host plan` state machine keyed the `draft` step
  solely off the company being `SELECTED`, but nothing advances a company
  `SELECTED → DRAFTED` (`save_host_draft` marks the *contact* DRAFTED, not the
  company — and no code path ever wrote the company `DRAFTED` state, a latent dead
  branch even on the API side). So once every selected contact was drafted, `plan`
  returned `next=draft, items=[]` forever and the host `/network-run` loop could
  never reach approval. The planner now derives `approve` when no `SELECTED`
  contacts remain to draft (read-only, and it still plans `draft` for the
  remainder during partial progress). Surfaced by the in-Claude end-to-end
  validation of the host-token pipeline.

### Coverage
- Repo at **98.85%** line+branch (gate `fail_under=98`); **1139 tests** green,
  ruff clean. The whole host-token surface — find→classify→ingest, drafting,
  next-move, critic, and the default `/network-run` orchestration — plus the
  folded-in #24/#20/#19 work all land at ≥98% per the Definition of Done (#1).

### Notes
- **#50 validated in-Claude and closed.** The host-token pipeline was driven
  end-to-end inside a Claude session with no API credit; the two bugs that
  surfaced were fixed here (#64 loop-stall, #65 came-across contradiction).
- **#20's live exit-gate run was waived for this release.** The exit-gate
  *harness* ships here; the documented live run (blocked on Anthropic credit) is
  deferred and does not gate the `v0.9.0` tag.

## [0.8.0] - 2026-06-29

v0.8.0 = sequencing + timing — the outreach pipeline now decides *when* to
follow up and *when* to send. Timed, capped follow-ups keep no-reply contacts
warm without spamming; timezone-aware send windows put each message in the
recipient's Tue-Thu morning.

### Added
- **Timing intelligence (issue #18, A7).** Recommends a per-contact send window —
  the next Tue/Wed/Thu at 09:00 in the *recipient's* local timezone (~+8% over a
  random send). Each contact's location is now persisted (migration 008:
  `contacts.location`; the finder writes the value the providers already
  extract), and a stdlib-`zoneinfo` keyword heuristic maps location → IANA zone
  (city/country names beat 2-letter state codes; unknown → UTC). New
  `/network-timing` verb lists the recommended send time for every contact.
  Pure `location_to_timezone` + `recommend_send_time` at 100% line+branch,
  verified across US and international zones.
- **Timed multi-touch follow-ups (issue #17, A7).** A sent outreach that draws no
  reply now earns a value-add follow-up scheduled `followup_gap_days` (default 5,
  inside the 4-7 day window) after the last touch, capped at
  `followup_max_touches` (default 2) so the cadence stays non-spammy — research
  puts 2-3 touches at a 20-30%+ reply-rate lift over a single send. Scheduling is
  gated by the marketer artifact (company `APPROVED`), enforces the cap at
  schedule time (never past it), and never double-books an already-pending
  follow-up. New `/network-followups` verb schedules every due follow-up into the
  `followups` table; `--list` shows pending and sent touches. Backed by a pure
  `plan_followups` planner (100% line+branch covered).

## [0.7.5] - 2026-06-28

v0.7.5 = outcome tracking — record and report what actually happens to sent
outreach, closing the feedback loop that will later tune the referral ranking.

### Added
- **Per-contact outreach outcomes (issue #15, A6).** Capture the feedback signal —
  whether a contact replied, yielded a point of contact, or gave a sponsorship
  answer — as an `Outcome` enum (`REPLIED` / `POC` / `SPONSORSHIP_YES` /
  `SPONSORSHIP_NO` / `DECLINED`, default `NONE`) stored on the contact (migration
  007: `outcome`, `outcome_notes`, `outcome_at`). New `/network-outcome` verb
  records an outcome (`/network-outcome <id> <OUTCOME> [--notes ...]`) and lists
  all recorded outcomes (`--list`). This is the data that later tunes the
  referral-ranking weights (#12) once real results accumulate.
- **Outcome rollup report (issue #16).** `/network-outcome --report` summarizes
  the response rate overall and per company (responded/total + an outcome
  breakdown), backed by a pure `aggregate_outcomes` aggregator.

## [0.7.0] - 2026-06-28

v0.7.0 = the uncapped cold-email channel. Hunter shifts from per-person lookups
to org-pattern inference so email scales past the ~25/month finder cap, the
end-to-end email path is hermetically validated, and the dormant PDL provider is
removed.

### Added
- **Hunter email-pattern inference — the uncapped cold-email channel (issue #13,
  A5).** Hunter now resolves emails via one quota-gated `domain-search` per
  company, which returns the org's email pattern (e.g. `{first}.{last}`); that
  pattern is cached and applied locally to *every* contact at the company, so the
  channel is effectively uncapped (one credit per company, not one per contact)
  — offsetting LinkedIn's ~100-invite/week ceiling. Inferred addresses are
  best-effort (`verified=False`, `source="hunter_pattern"`); when a company has
  no pattern the chain falls through to Apollo, and the `HUNTER_EXHAUSTED`
  quota/fallback contract is preserved. **Validated end-to-end (issue #14):**
  hermetic tests run the real Hunter-pattern + Apollo providers through
  `_resolve_email` with a real QuotaManager — fallback ordering, quota
  exhaustion, the `HUNTER_EXHAUSTED`/`APOLLO_EXHAUSTED` sentinels, and the
  one-lookup-per-company uncapped behavior — fully branch-covering the path.

### Removed
- **Dormant PDL provider deleted (issue #22).** `pdl.py` was built but never
  wired into the pipeline and is redundant with Apify (live + fresh vs PDL's
  6–18-month-stale data); removed along with its quota entry. Recoverable from
  git history if a structured backstop is ever needed.

### Changed
- **Providers hardened to the coverage bar (issue #22, audit).** apify, apollo,
  and quota_manager brought to 100% line+branch (quota/fallback skips, client
  close, non-dict/unparseable items, missing-row guards, write rollback); retry
  and serper at 95%. Hunter's coverage is owned by its #13 rewrite (coordinated
  per the audit) and is intentionally not back-filled here.

### Coverage
- Coverage gate (`fail_under`) ratcheted 97 → 98; repo at ~98% combined
  line+branch (committed tree), 888 tests.

## [0.6.5] - 2026-06-28

v0.6.5 = referral-likelihood ranking — operationalizes "5 to the right people >
50 generic" so the contacts most likely to actually help surface first, with the
ranking proven by a validation scorecard.

### Added
- **Referral-likelihood ranking model (issue #11, ROADMAP A4).** New
  `src/agents/ranker.py` scores each captured contact by how likely they are to
  help, from deterministic signals already on the contact — alumni (confirmed >
  classified), 1st/2nd-degree connection, recruiter-for-req, posts-about-hiring,
  recent joiner, team-matches-target-role, plus reachability. Returns a total
  plus per-signal contributions; **no LLM**, so the score is reproducible and
  every point is explainable. The Finder/importer score each contact at ingest,
  log the breakdown, and persist `rank_score` + `rank_reasons` (migration 006);
  the selection gate now lists contacts highest-likelihood first and shows the
  score and the reasons.
- **Ranking validation scorecard (issue #12).** New `src/eval/rank_scorecard.py`
  (pure, offline, runs keyless in CI) grades the ranker's ordering against a
  gold-tiered labeled set with pairwise concordance, tier inversions, and top-K
  precision. Documented result (`docs/RANK_SCORECARD_2026-06-28.md`): **PASS —
  100% concordance, 100% top-5 precision, 0 inversions**, tiers cleanly separated.

### Fixed
- **Hook signals trim on a word boundary (issue #32).** An overshooting
  classifier `hook_signal` was hard-sliced at 80 chars mid-word ("…large
  assembly stru" — surfaced by the v0.6.0 Finder trial); it now backs up to the
  last space so the anchor reads as a clean phrase (a single over-long token
  keeps the hard cut).

### Coverage
- Coverage gate (`fail_under`) ratcheted 96 → 97; repo at ~97% combined
  line+branch (committed tree), 870 tests.

## [0.6.0] - 2026-06-28

v0.6.0 = "develop the Finder like the Drafter" (Sid's stated focus). Finder
discovery, hook grounding, and correctness are hardened, and a live trial
declares the Finder at parity with the Drafter's quality bar.

### Added
- **Live Finder trial + scorecard — Finder/Drafter parity (issue #10).** New
  `src/eval/finder_scorecard.py` scores Finder output on the same shape of
  objective, ground-truth-free criteria the AST drafter trial used (discovery
  yield, hook quality, classify spread) with a PASS/REVIEW/FAIL verdict and a
  markdown render; the live entrypoint runs `find_contacts` and reads the rows
  back. Documented live run on AST SpaceMobile (LinkedIn-only, isolated DB):
  13/15 discovered, 0 GENERIC / 0 verbatim-news / 13/13 hooks whitelisted →
  **PASS, Finder declared at parity** (`docs/TRIAL_FINDER_AST_SPACEMOBILE_2026-06-28.md`).

### Changed
- **Finder discovery is now best-effort-to-N across providers (issue #8).**
  `_discover` accumulates deduped results up to the limit across the provider
  chain (Apify → Serper) — asking each lane only for the shortfall — instead of
  returning the first non-empty lane. **No silent caps:** provider failures and
  any final shortfall are logged at WARNING, fixing FINDER_AUDIT **D1** (a bad
  Apify key no longer looks like "no contacts exist").
- **Discovery role keywords are now config-driven (issue #8, D2).** Moved the
  hardcoded aerospace list to `Config.finder_role_keywords` (default unchanged),
  overridable via `pipeline.finder_role_keywords` in `config.yaml` — any user/
  field, no code edit. The Apify `searchQuery` now broadens across the top
  keywords (OR-joined) instead of only the first (D4), so a composites/stress
  engineer isn't ranked below "quality engineer" and truncated.
- **End-to-end location filtering (issue #8).** `location` threads from
  `run_pipeline` → `find_contacts` → `_discover` → every discovery provider,
  which folds it into its query (Apify semantic `searchQuery`, Serper Google
  query). Location is a first-class campaign filter (one company + location/day),
  so contacts are now geo-targeted, not just company-matched. **Completes #8 —
  Finder discovery improvements.**
- **Hooks are specific and grounded across all input shapes (issue #9,
  FINDER_AUDIT D6/D7/D11).** Imported contacts with persona + focus pre-set
  (Apollo/Cowork rows) now still mine a rich `about`/snippet for a Tier-0 hook
  instead of dropping to a title bucket (**D7**, the central fix); the news-shape
  hook gate rejects only true pasted headlines (a `·` separator or two
  co-occurring markers), so real signals like "reports to VP Structures" are no
  longer demoted (**D6**); the shared-employer hook tier also matches the company
  slug, so a current employee with a bare title still trips it (**D11**).

### Fixed
- **Finder correctness pass (issue #27, FINDER_AUDIT D5/D8/D9/D10/D12).**
  **D5:** re-running the Finder/importer no longer inserts duplicate contacts —
  new partial unique index on `(company_id, linkedin_url)` for non-null URLs
  (migration **005**) plus `INSERT OR IGNORE`. **D8:** one shared
  `src.core.slug.slugify` replaces three divergent copies, so `"Joby Aviation,
  Inc."` no longer cross-links to two company rows. **D9:** a NULL company domain
  now logs a WARNING with the inferred value instead of failing every email in
  the batch silently. **D10:** a new `APOLLO_EXHAUSTED` sentinel replaces the
  mislabeled `apollo` source when Apollo is capped without running. **D12:** the
  company-news query uses the current year instead of a hardcoded one.

### Coverage
- Coverage gate (`fail_under`) ratcheted 95 → 96; repo at ~97% combined
  line+branch (831 tests).

## [0.5.5] - 2026-06-27

Theme: **Finder Audit + Classify.** First audit of the Finder, a classify-accuracy
scorecard driven to persona 100% / focus 100%, the start of the anti-AI-detection
moat, and a coverage push that took the repo to ~96.6% (line+branch) — flipping the
gate to 95.

### Added
- **Anti-AI-detection critic dimension (issue #6) — starts the moat thread.**
  New deterministic `critic.scan_ai_tells()` flags known AI-writing tells (filler
  openers, "I came across your profile", corporate buzzwords, cover-letter voice,
  …) in the draft body + subject; any hit is an automatic `CRITIC_HOLD`,
  complementing the LLM `tone` dimension's holistic judgment. High-precision so
  human-grade drafts pass clean. Research: ~33% of recruiters spot AI outreach in
  20s — quality is the moat. See `docs/ANTI_AI_DETECTION.md`.

### Changed
- **Coverage push — drafter family to ≥95% (issue #23).** +100 hermetic tests:
  `marketer` 84→99%, `dispatch` 79→100%, `drafter` 93→99%. Tests only. With #25,
  repo line+branch is now **~96.6% (committed tree) — above the 95% v0.5.5 gate**
  (#7). Remaining sub-95 modules (hunter, finder, importer) are tracked tech-debt
  (#22/#24), not release blockers.
- **Coverage push — core + CLI to ≥95% (issue #25).** +68 hermetic tests bring
  `network_check` 72→95%, `network_import` 80→100%, and `config`/`orchestrator`/
  `migrations`/`search_cache`/`network_status` to 100% (others 97%). No source
  changes — tests only. Repo line+branch 89→94% (toward the 95% v0.5.5 gate, #7).
- **Classify accuracy hits the v0.5.5 bar (issue #5): persona 100% / focus 100%**
  (baseline was 100% / 68%). `finder._classify_contact` now **deterministically**
  forces focus-area for the two non-engineer personas (ALUMNI → `ALUMNI_ACADEMIC`,
  RECRUITER → `PEER`) in code rather than via the prompt, which the model ignored
  on strong-topic cases; engineers keep the model's focus. `PEER` guidance tightened
  for generalist titles. Labeled set expanded 19 → 28 for ≥95% discriminating margin.

### Added
- **Classify-accuracy scorecard (issue #4).** New `src/eval/classify_scorecard.py`
  + a 19-contact labeled set measure the Finder classifier's persona/focus-area
  precision/recall/F1 (closes audit gap D3 — classify was tested for shape, never
  accuracy). Classifier-agnostic (keyless-tested in CI; live run via
  `python -m src.eval.classify_scorecard`). Baseline: **persona 100% / focus 68%**
  — the focus gap is mostly unspecified-convention behavior (ALUMNI→ALUMNI_ACADEMIC,
  RECRUITER→PEER), routed to #5. Agreed bar: persona 100% / focus ≥95%. See
  `docs/CLASSIFY_SCORECARD_2026-06-26.md`.
- **Branch coverage + CI (issue #2).** `pytest` now runs with `--cov-branch`; a
  GitHub Actions workflow (`.github/workflows/ci.yml`) runs `ruff` + the gated
  test suite on every push/PR. The coverage gate (`fail_under`) ratcheted 80 → 88
  during the cycle, then **flipped to 95 at the v0.5.5 close** (#7) once the
  coverage push (#21/#25/#23) brought the repo to ~96.6%. Ratchet upward only.

## [0.5.0] - 2026-06-25

### Added
- **Apify is now the primary LinkedIn discovery source, with Serper as fallback
  (input-stack decision 2026-06-25).** New `ApifyProvider`
  (`harvestapi/linkedin-profile-search`, Full mode, no cookies) becomes the first
  discovery lane in the Finder; Serper is tried only when Apify is exhausted,
  misconfigured, or finds nothing. Single API key, no rotation (the LinkedIn send
  cap bounds demand below one free account's monthly credit). Billed per
  25-profile page; a `QuotaManager("apify")` monthly cap (default 40 pages ≈ ~$8)
  is the coarse budget guard. Configure via `APIFY_API_KEY`.
- **Apollo email fallback after Hunter.** New `ApolloProvider` (people/match)
  fills addresses Hunter misses; Hunter stays primary. Per-batch exhaustion flags
  skip a capped provider for the rest of the batch (`HUNTER_EXHAUSTED` sentinel
  preserved). Opt-in with the existing email toggle + `APOLLO_API_KEY`.
- Finder discovery and email now run through ordered fallback chains
  (`_discover`, `_resolve_email`); both are unit-tested independently of the DB.
- **Manual scraping (Cowork+Chrome producer) is the documented FINAL fallback** —
  already supported today via `/network-import`; dedicated feature development is
  postponed.

### Fixed
- **Apify profile imports no longer drop company / mangle location.** Apify
  LinkedIn actors return nested fields (`location.linkedinText`,
  `currentPosition.companyName`) the flat alias map couldn't see — company was
  dropped and location stringified as a dict. New `_lift_apify_nested()` lifts
  those paths before aliasing; covered by a regression test using the real
  harvestapi shape.
- **LinkedIn connection notes no longer lost to length HARD_FAIL (live-validation
  finding).** The drafter already gave an over-length `LINKEDIN_CONNECTION` note
  one corrective regen, but the model could still return marginally over the
  280-char cap (observed against a real Apollo export: 287 vs 280) — and those
  drafts HARD_FAILed, dead on arrival. The drafter now deterministically
  auto-trims a still-over note to fit (keeping leading whole sentences; word-
  boundary + ellipsis fallback) and marks it `SOFT_FLAG` so the reviewer sees it
  was machine-shortened rather than silently sent. New `_trim_to_char_limit()`
  helper, covered by unit + integration tests.
- **Drafter batch concurrency tuned for the Anthropic Tier-1 limit (live-
  validation finding).** A full batch at the old fixed `_MAX_WORKERS=6` sustained
  throughput above the Tier-1 **input-tokens-per-minute** ceiling (50k ITPM),
  so `max_retries=8` could not recover and large imports partially failed with
  `RateLimitError` (failed contacts correctly rolled back to `SELECTED`, fully
  retryable). Concurrency is now `min(_MAX_WORKERS ceiling, Config.drafter_max_workers)`
  with a new `drafter_max_workers` default of `3` (keeps a batch under the
  Tier-1 ITPM ceiling out of the box; raise it on higher tiers).
- **Resolved 3 pre-existing `ruff` lint issues** (import ordering in
  `drafter.py`, two `E501` over-length lines in `config.py` and
  `test_humanizer.py`). `ruff check` is now clean.

### Changed
- **README rewritten** as a professional landing page (animated header, badges,
  a mermaid pipeline diagram, market-thesis intro, collapsible sections). Fixes
  stale content: `v0.2.0` status → current, the `<your-username>` install
  placeholder → real repo URL, and the wrong `data.db` reference → `state.db`.
  Adds `/network-import`, the v0.3–v0.4 features, and roadmap/docs links. Plus a
  `docs/README.md` index so the docs folder is navigable.

### Added
- **Per-source contribution logging ("no silent caps", ROADMAP A1).** The import
  path now reports exactly what each source contributed and what it dropped, so a
  thin or lossy file is never mistaken for full coverage. New
  `parse_contacts_file_with_report()` tallies `rows_read` / `usable` and a
  per-reason `dropped` breakdown (`no_name` / `no_company` / `duplicate`);
  `import_contacts()` now returns `{"by_company": {...}, "contribution": {...}}`
  and `/network-import` prints a `Source '<src>': N row(s) read → M usable
  (dropped: …)` line before the per-company summary. `parse_contacts_file()` is
  unchanged (still returns the contact list).
- **Source-agnostic contact input (flexible-input design).** Contacts no longer
  have to come from the Serper Finder. The Finder's second half was extracted
  into a shared `ingest_contacts()` (enrich → classify → hook → save), and a new
  `src/agents/importer.py` normalizes any leads file — Apollo export, Apify
  scrape, Serper/Cowork+Chrome JSON, or a hand-compiled CSV/JSON — into the
  canonical contact record and runs it through that same path. Headers are
  matched by alias (`Person Linkedin Url` / `profileUrl` / `linkedin` →
  `linkedin_url`, etc.); `persona`/`focus_area`/`hook` are generated when absent
  and honored when the file supplies them; a supplied `email` skips Hunter. New
  `/network-import <file> [--company] [--source] [--draft] [--validate]` command
  gives a frictionless "leads file in → drafts out" path (reusing ask-rotation,
  the marketer approval loop, and the artifact); wired as a CLI entry point at
  `src/cli/network_import.py` (`python -m src.cli.network_import <file> --draft`),
  matching the other `network_*` commands. `validate_contacts_file()` is
  the producer-contract check for the Cowork + Chrome automation. The Finder's
  Serper path is unchanged (byte-for-byte behavior; 534 prior tests still green).
  Design: `docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md`.
- **Cowork + Chrome producer contract.** Agreed I/O for the read-only LinkedIn
  capture producer: `runs/<YYYY-MM-DD>-<slug>.json` outputs + `runs/targets.csv`
  queue (git-ignored), and three honored producer fields — `alumni_confirmed`
  (forces the ALUMNI persona, ground truth over the classifier), file-level
  `school`, and `connection_degree` (both surfaced in `shared_signals` for the
  reviewer). The published sample `docs/chrome-capture.example.json` is locked in
  by a contract regression test. Contract: `docs/CHROME_PRODUCER_CONTRACT.md`.

## [0.4.0] - 2026-06-20

Phase 3 — ask-rotation: same-company alumni/peers now get *different* questions
instead of the same script. The validation run also caught and fixed a serious
pre-existing bug where the Drafter fabricated the contact's employer.

### Added
- **Ask-rotation across same-company contacts (Phase 3).** When several
  contacts at the *same* company share a rotation-eligible persona — alumni or
  peer engineer — each is assigned a *distinct* ask angle before drafting, and
  that angle is injected into its generation prompt. Five alumni at one company
  now get five different questions (hiring climate / sponsorship / culture /
  transition / who-to-talk-to) instead of the same script. Peers rotate over a
  parallel set (day-to-day work / a specific project / culture / how they broke
  in / advice for someone finishing an MS). Assignment is deterministic and
  computed once up front (no extra LLM calls, race-free) via
  `drafter.assign_ask_angles`; recruiters (role-specific ask) and senior
  managers (no hard ask) are not rotated, and a lone contact gets no
  assignment — the model still picks the single best angle for them, exactly as
  before. New `enable_ask_rotation` config knob (default on). The alumni and
  peer persona templates' Close sections enumerate the rotatable angles to keep
  the prose in sync with the injected instruction.

### Fixed
- **Drafter no longer fabricates the contact's employer.** The generation
  prompt never received the company name — only `company_id` — so when a
  persona template referenced "a fellow alum at {Company}" the model invented a
  plausible-but-wrong employer (Boeing, Lockheed, Skydweller) or leaked a
  literal `[Company]`. `_load_contact` now LEFT JOINs `companies` and the prompt
  carries a `Company:` line, plus a FACT DISCIPLINE rule pinning the model to
  that exact name (and to "your team" when it's Unknown). Surfaced by the Phase
  3 multi-alumni validation run; re-validated to zero fabrication.
- **Placeholder detector now catches mixed-case brackets.** The bracket gate was
  all-caps-only (`[COMPANY]`), so a title-case `[Company]` slipped through as
  OK. Widened to catch `[Company]` / `[Team Name]` / `[your team]` while still
  ignoring lowercase citation markers like `[smith2023]`.
- Corrected `config/default.yaml`'s `linkedin_char_limit` from a stale `200` to
  `280` (a v0.3.0 200→280 correction had missed this template file).

## [0.3.0] - 2026-06-19

Voice overhaul: the Drafter now writes in the user's own outreach voice
(a 4-part model, persona-tuned) instead of generic LLM phrasing. Validated
on an AST SpaceMobile re-run (30 drafts): zero fabrication, zero placeholder
leaks, zero over-length notes, full opener variety, and the "exactly the
kind of" AI tell eliminated (was 4/30).

### Added
- **4-Part Message Model** (Intro → Source → Hook → Close) encoded in
  `voice.md` and all four persona templates. Context-first openers ("how/why
  I found you") are now first-class; each persona specializes the Source,
  Hook, and Close (recruiters direct/role-specific; senior managers
  admire-and-stay-connected with no hard ask; peers ask about their work;
  alumni lead with the shared school and ask one rotating company question).
- **Humanizer** (`src/agents/humanizer.py`): a deterministic, grammatically
  safe post-generation pass that strips filler intensifiers the soft
  blocklist can't shake (the "exactly the kind/type/sort of …" /
  "exactly the direction" family). Wired into the Drafter after each
  generation so the tell never reaches the gate or the wire.
- **Length-regen** for LinkedIn connection notes: an over-cap note gets one
  compression pass before HARD_FAIL (closes the "no auto-trim" gap).

### Changed
- **LinkedIn connection-note cap corrected: 200 → 280 characters.** The 200
  value was a misread ("free-account hard limit"); LinkedIn's note cap is 300
  on all plans (free accounts are limited on note *count*, not length). 280
  is a safe margin (spaces count, emojis count double). Wired through config,
  guardrail, prompt text, and all persona templates.
- **Blocklist specificity-relaxed:** "I admire" and "I came across" are no
  longer blunt hard-bans — specific admiration/sourcing is a valid hook, and
  the critic's specificity dimension judges genericness. Unambiguous tells
  ("your impressive work", "exactly the kind of") remain hard-blocked.
- Anthropic client now uses `max_retries=8`: the parallel Drafter can burst
  past lower-tier input-token rate limits; the larger retry budget lets a run
  self-pace and complete instead of failing mid-batch.
- Refreshed `config/voice.example.md` to the 4-part model (the prior installed
  voice doc was stale: claimed a 300-char cap and instructed `[RESEARCH_NEEDED]`
  placeholders, both since removed).

## [0.2.2] - 2026-06-18

Quality release: closes the one actionable defect surfaced by the
AST SpaceMobile trial run (`docs/TRIAL_AST_SPACEMOBILE_2026-06-13.md`).

### Added
- Seed blocklist now flags the AI tell **"exactly the kind of"**, which
  appeared in 4/30 trial drafts (including an otherwise-sendable one). The
  phrase was already named in the humanizer's fix prompt but was not
  enforced by the guardrail. Detection is unit-tested in
  `tests/test_guardrails.py`.

## [0.2.1] - 2026-06-11

Free-quota release: default runs now spend zero Hunter credits and never
pay twice for the same search.

### Added
- Search-response cache (`search_cache` table, migration 004): Serper
  responses are cached in SQLite keyed by request payload with a
  configurable TTL (`providers.search_cache_ttl_days`, default 14 days;
  0 disables). Cache hits skip both the HTTP call and the quota
  increment, so re-runs, resumed runs, and trial iterations are free.

### Changed
- **Hunter email enrichment is now OPT-IN** (`pipeline.enable_email_enrichment`,
  default `false`). Rationale: the free tier is 25 lookups/month (~1.5 runs)
  while LinkedIn channels convert far better and need no email. With
  enrichment off, the finder requires no Hunter key, spends zero Hunter
  quota, marks contacts `EMAIL_DISABLED`, and the drafter skips cold email
  (existing tested path). An explicitly injected provider always wins over
  the toggle. **Migration note:** users who relied on cold-email drafting
  must set `pipeline.enable_email_enrichment: true` in config.yaml.
- `/network-check` no longer fails preflight on a missing Hunter key when
  enrichment is disabled (reports an informational skip instead).

### Tests
- 485 -> 505 passing; coverage 90.2%.

## [0.2.0] - 2026-06-10

Quality release: closes every open item from DRAFTER_AUDIT_2026-06-06 and the
remaining STRICT_AUDIT_v0.1.0 follow-ups. Full issue ledger with per-item
status: AUDIT_TRACKER_FABLE5.md.

### Added
- **A10-A15** Layers 1-6 from the 2026-06-06 session, previously uncommitted:
  snippet-derived hook signals + shared_signals (Layer 1), provenance-tagged
  APPROVED FACTS + FACT DISCIPLINE (Layer 2), hard guardrail gate (Layer 3),
  Sonnet critic with persisted critic_trace (Layer 4 + migration 003), the
  HARD_FAIL/CRITIC_HOLD marketer gate + batch checkpoint (Layer 5), and the
  shared drafter/dispatch implementation (Layer 6).
- **A6** Cross-contact opener diversification (Layer 1-A): a normalized opener
  may be used by at most `quality.opener_max_repeats` contacts per run
  (default 2); overuse triggers a corrective regen, persistent repeats are
  SOFT_FLAGged.
- **A7** Drafter-level single-ask enforcement: deterministic multi-ask
  detector (two questions / two ask-sentences / hedge-stacked ask) with one
  corrective regen; prompt gains an explicit one-ask rule.
- **A8** Self-intro deduplication: identity markers appearing twice (body +
  signature) trigger a regen; prompt instructs identity-once.
- **A9** Every held draft now carries a populated held reason: hard_check
  reasons persist to `drafts.critic_trace`, and the artifact + marketer render
  a `Held because:` line for HARD_FAIL and CRITIC_HOLD drafts.
- **A20** Typed `EmptyLLMResponseError` (src/core/errors.py) replaces
  IndexError/AttributeError on malformed LLM responses.
- **A25** `SerperProvider.close()` / `HunterProvider.close()` httpx lifecycle.
- **A32** Critic regression fixtures: the 30 real score vectors from the
  2026-06-06 Joby run are encoded in tests/test_critic_calibration.py.

### Changed
- **A3** Critic decision rule recalibrated: hold only on a severe dimension
  (score <= 1, including grounded_facts fabrication evidence) or more than two
  weak dimensions (< 3). Hold rate on the June-6 fixture set drops 93% -> 33%
  (target band 20-40%); Morgan/Marc passing drafts still pass, Yueyang
  multi-ask and Nathan placeholder still fail. grounded_facts rubric
  clarified: absence of APPROVED FACTS is not fabrication.
- **A5** Hook generation: new title-derived tier (`your work as <title>`)
  preferred over the GENERIC sentinel; `is_acceptable_hook` whitelist gates
  classifier signals.
- **A16** FACT DISCIPLINE rule 5: company-level news may never be attributed
  to the contact personally ("your recent posts").
- **A26** voice.md and resume_library.yaml now resolve next to config.yaml,
  honoring `NETWORKING_AGENT_CONFIG`.
- **A28** pyproject gains [build-system] + setuptools package config —
  `pip install -e .` works; version bumped to 0.2.0; dependencies declared.
- **A31** README documents the quality gate and the voice.md trust model.
  DESIGN-drift notes recorded in docs/DESIGN_DRIFT_v0.2.0.md (the original
  DESIGN.md lives outside this repository and is intentionally untouched).

### Fixed
- **A1** Placeholder tokens are now *prevented*, not just caught: a
  placeholder in the first generation triggers one corrective regen with an
  explicit anti-placeholder instruction before the hard gate runs.
- **A2** Placeholder bodies are never serialized: a draft that still contains
  a bracketed token after regen is HARD_FAILed and redacted
  (`(placeholder removed)`) before any DB insert, in both the drafter and the
  dispatch revision path.
- **A4** Raw company-news strings can no longer become hooks: verbatim-news
  detector (datelines, headline separators, press-release phrasing) rejects
  them in every tier; news stays in shared_signals as phrasing material.
- **A17** voice.md is size-capped at 16 KB on load (truncated with a logged
  warning) to bound prompt-injection/token blow-up from pasted templates.
- **A18** config.yaml permission check now fstats the open descriptor
  (TOCTOU window closed).
- **A19** Quota increments run under `BEGIN IMMEDIATE` — the check-then-update
  pair is atomic across processes, not just threads.
- **A21** Hunter lookups use the stored `companies.domain` when present; slug
  inference is only the fallback.
- **A23** Persona templates, voice doc, and resume library are read with
  explicit utf-8 encoding.
- **A27** Repo is ruff-lint and ruff-format clean (254 violations fixed).

### Removed
- Hook Tier 4 "company news as hook string" (replaced by the title-derived
  tier; news is context, never a hook).

### Wontfix (with reasons — see AUDIT_TRACKER_FABLE5.md)
- **A22** dispatch executor-per-call timeout pattern: once-per-REVISE, tested,
  zero user-visible gain from swapping to httpx timeouts.
- **A24** purge.log hardening + state-transition audit log: explicitly
  deferred by STRICT_AUDIT itself; no unattended send in v0.2.0.
- **A30** Cold-email path live validation: blocked on Hunter quota (external);
  recorded in OPEN_QUESTIONS_FABLE5.md. Unit coverage exists.

### Tests
- 409 -> 485 passing; coverage 90.2% (critic 100%, guardrails 99%, drafter 94%).

## [0.1.1] - 2026-05-25

Hotfix release addressing HIGH and MEDIUM findings from the pre-ship audit (STRICT_AUDIT_v0.1.0.md).

### Security
- **P9** Hunter.io API key no longer leaks into stderr tracebacks; httpx exceptions raised from Hunter calls are now caught and re-raised through a sanitized twin with `api_key=***` and a broken `__cause__` chain via `scrubbed_hunter_call()` (commits `a24b27c`, `72d7ab2`).

### Fixed
- **P6** Per-contact draft sequence in `src/agents/drafter.py` is now atomic — delete-v1, N channel inserts, and the DRAFTED state transition run inside a single `with_writer()` block, so a mid-sequence crash can no longer leave a contact marked DRAFTED with missing channel rows (commits `85576cd`, `6ae0ec3`).
- **P7** `draft_for_contacts` now drains all worker futures on failure and raises `DrafterPartialFailure(RuntimeError)` carrying `.partial_results` and `.errors`, so completed contacts are preserved when a peer worker raises (commits `7862442`, `b4843b4`).
- **P8** `network_purge` no longer follows symlinks when removing `~/.networking-agent/drafts/<slug>/`; the new `_safe_rmtree` helper refuses symlinked paths, emits a stderr warning, and records `fs=symlink-skipped` in both the stdout summary and the audit log (commits `fa209be`, `8255e0e`).

### Tests
- 240 → 252 passing; coverage 88.06%.

## [0.1.0] - 2026-05-25

### Added

#### Commands
- `/network-run <slug>` — full pipeline with state-machine resume (NEW→FOUND→SELECTED→DRAFTED→APPROVED)
- `/network-check` — preflight setup checks (SQLite, DB integrity, schema version, config perms, API key pings, voice doc)
- `/network-find <slug>` — run Finder Agent only (Discover→Enrich→Classify→Hook→Save)
- `/network-draft <slug>` — run Drafter Agent only (parallel fan-out, 3 channels per contact)
- `/network-approve <slug>` — run Marketer approval loop only (APPROVE/SKIP/REVISE)
- `/network-status [slug]` — show pipeline state for one or all companies
- `/network-dry-run <slug>` — simulate run without making API calls
- `/network-providers` — display API quota status (Serper, Hunter, Anthropic)
- `/network-purge [slug]` — delete company data from DB (GDPR compliance)

#### Agents
- **Finder Agent** (`src/agents/finder.py`) — 5-phase pipeline: LinkedIn profile discovery via Serper, email enrichment via Hunter, persona + focus-area classification via Claude Haiku, hook generation, SQLite persistence
- **Drafter Agent** (`src/agents/drafter.py`) — parallel fan-out (up to 6 workers), generates LinkedIn connection note + post-connection message + cold email per contact; guardrail regen on quality flag
- **Marketer Agent** (`src/agents/marketer.py`) — interactive approval loop with APPROVE/APPROVE ALL/SKIP/REVISE/SHOW commands; REVISE dispatches draft revision via Claude Haiku
- **Orchestrator** (`src/orchestrator.py`) — state-machine dispatcher, resumes from any interrupted state, preflight short-circuit on errors

#### Infrastructure
- SQLite state DB with WAL mode, single-writer lock, retry-on-busy (`src/core/db.py`)
- Schema migrations system (`src/core/migrations/`)
- Pydantic-validated config (YAML + env var fallback) (`src/core/config.py`)
- Provider abstraction with exponential-backoff retry and quota management (`src/providers/`)
- Artifact writer — Markdown output to `~/.networking-agent/drafts/<slug>/` (`src/agents/artifact_writer.py`)
- Dispatch protocol for REVISE sub-requests (`src/agents/dispatch.py`)
- Achievement matcher against resume library YAML (`src/agents/achievement_matcher.py`)
- Reputation guardrails — blocks over-used phrases from drafts (`src/agents/guardrails.py`)

#### Quality
- 238 tests, 87% line coverage on `src/`
- `pytest --cov-fail-under=80` enforced in `pyproject.toml`
- `claude plugin validate` passing

### Fixed (BLOCKING issues resolved during design review)
- **Word-boundary regex** in Tier 2 guardrail hook generation — prevented substring false positives
- **Tier 3 hook generation** — expanded to match `"structures"` keyword variant
- **Circular import** in Marketer/dispatch — deferred `dispatch_revision` import to avoid module-load cycle
- **Test fixture DB path patching** — corrected monkeypatch target for `src.core.db._DB_PATH`
- **REVISE lazy resolution** — `dispatch_revision` resolved at first call, not at module import
- **Selection gate state update** — company state now transitions to `SELECTED` atomically with contact updates
