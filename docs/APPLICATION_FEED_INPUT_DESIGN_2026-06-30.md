# Application-feed input — design & context

**Date:** 2026-06-30  **Status:** Design / context (pre-build) — proposed by a consumer project
**Relates to:** `FLEXIBLE_INPUT_DESIGN_2026-06-21.md` (canonical contact record), `CHROME_PRODUCER_CONTRACT.md` (producer I/O pattern), `ROADMAP.md` (Phase B generalization)

**One line:** add a second input *mode* — a **per-application job feed** — so the agent can find referral candidates **for a specific posting**, not just relevant people at a company. Additive, reuses the existing pipeline, and is the more common job-seeker use case.

---

## 1. Why this doc exists (the use case the agent doesn't know about)

A separate project consumes this plugin in a way it wasn't built for. Capturing it here so the agent's design accounts for it.

**The consumer — a "job-search cockpit" (one user's setup today, a general pattern tomorrow):**
- Fetches job postings daily from multiple sources (Apify scrapers, JobRight, LinkedIn).
- **Scores** each posting on its own rubric; only high-scoring postings (e.g. ≥85) advance. There is **no fixed daily count** — some days 2 postings qualify, some days 8.
- Wants, **for each qualifying posting**, a small set of the *right* referral candidates **on that role's team** (the hiring manager for that function, a recruiter on the req, alumni/peers in that group) — then drafts, then the human sends, then tracks "do we have a referral for *this posting* yet?" to decide whether to apply.

This is **referral-first, application-scoped** networking. It is arguably the *primary* way job seekers network — most people apply to specific roles and want a referral for *that* role, not a broad "get to know the company" campaign.

**The strategic premise behind it:** blind applications convert at ~0%; referrals convert ~10×. So the consumer applies **only after** a referral lands. That makes "referral candidates per posting" the core unit of work.

---

## 2. The gap in v0.8.0

The agent today is **company-targeted** ("Campaign mode"):

- Input is a company + location (`/network-find`) or a company-scoped contact capture (`/network-import`, canonical record in `FLEXIBLE_INPUT_DESIGN §2`).
- The unit of work is a **company**; ask-rotation groups by company; contacts link to a company.
- A **job posting is not a first-class entity.** There is no way to say "these contacts are for *this req*," no per-posting targeting of discovery, and no per-posting state to report back.

So the consumer can only approximate the use case by treating each posting's company as a campaign — losing the role-team targeting and the posting↔contact linkage that drive the apply/drop decision.

---

## 3. The fix: two modes, one engine

Frame the agent as **one pipeline with two front doors**, exactly like the source-agnostic ingest in `FLEXIBLE_INPUT_DESIGN`:

| Mode | Unit of work | Input | Targets |
|---|---|---|---|
| **Campaign** (existing) | a company | company+location, or company contact capture | relevant people at the company |
| **Application** (new) | a **posting** | an application feed (§4) | the **role's team** + hiring manager + recruiter + warm personas |

Both modes converge on the **same** `discover → ingest (classify → hook → rank) → draft → critic → approve → follow-up → outcome` core. Application mode adds (a) a posting entity, (b) role-aware targeting, (c) posting↔contact linkage, (d) per-posting reporting. Everything else is reused.

---

## 4. The application-feed format (new input)

A feed is a list of postings. Each posting carries the role context needed to target *its* team. It deliberately **reuses the canonical contact record** (`FLEXIBLE_INPUT_DESIGN §2`) — the consumer usually supplies *no* contacts (the agent finds them), but the field is allowed for pre-captured leads.

```json
{
  "schema": "application-feed/v1",
  "profile_ref": "default",                 // which user profile/voice to draft as (see §8)
  "applications": [
    {
      "job_id": "ja-2026-06-30-001",         // REQUIRED — stable id from the consumer; the linkage key
      "company": "Joby Aviation",            // REQUIRED — drives Company: anti-fabrication line + grouping
      "company_slug": "joby-aviation",       // optional; derived from company if absent
      "location": "Dayton, OH",              // optional
      "role_title": "Quality Engineer",      // REQUIRED — drafts name the role (stronger than generic)
      "job_url": "https://…",                // optional but recommended
      "function": "QUALITY",                 // generic function tag → biases discovery + ranking
      "target_keywords": ["quality","MRB","supplier quality","AS9100"], // narrows to the role's team
      "score": 88,                           // optional provenance (consumer's rubric)
      "deadline": "2026-07-07",              // optional; informs the consumer's grace window, not the agent
      "source": "cockpit",                   // provenance tag
      "contacts": []                         // optional — pre-captured leads (canonical record); usually empty
    }
  ]
}
```

**Field rules**
- Required per posting: `job_id`, `company`, `role_title`. Everything else optional.
- `job_id` is the contract key: it links discovered contacts, drafts, artifacts, and outcomes back to the posting so the consumer can ask "referral for `job_id` yet?".
- `function` + `target_keywords` are **free-form**, not a fixed enum — they map into whatever focus taxonomy the active `profile_ref` defines (keeps the format domain-agnostic; see §8).
- If `contacts[]` is supplied, each follows the canonical record and is linked to this `job_id` (pre-captured-leads path).
- A posting with no discoverable contacts is logged and skipped — **no silent caps** (consistent with the Finder's best-effort-to-N rule).

---

## 5. What changes per-application (reuse first)

The point is how *little* new logic is needed — most of it already exists.

1. **Discovery, role-biased.** Pass `function` + `target_keywords` into the Finder's discovery so the search and especially the **ranker** favor the role's team. The ranker (`src/agents/ranker.py`, v0.6.5) **already has a `team-matches-target-role` signal** — Application mode just feeds it the posting's role context instead of a generic company target. Plus the existing high-value personas (recruiter-for-req, hiring manager, alumni, peer).
2. **Ingest/classify/hook/rank:** unchanged. Same `ingest_contacts()` path.
3. **Drafting:** unchanged engine; the draft context gains the **role title + job_url**, so notes can reference the specific posting (a named-role ask out-converts a generic company ask). Ask-rotation groups **by posting** (or by company-with-posting-context when several reqs share a company).
4. **Follow-ups / timing / outcomes:** unchanged engines; every record is tagged with `job_id`.
5. **Reporting:** new per-posting rollup the consumer polls (§7).

---

## 6. Data model additions (minimal)

- New **`applications`** table: `job_id` (pk), `company`, `company_slug`, `role_title`, `function`, `job_url`, `score`, `deadline`, `status`, `created_at`. (One migration, following the existing `PRAGMA user_version` pattern.)
- **Link contacts to postings.** A contact can be relevant to more than one posting at the same company, so prefer a small **`application_contacts`** join table (`job_id`, `contact_id`) over a single FK. A plain `contacts.job_id` FK is acceptable for v1 if the join table is deferred — call it out in the migration note.
- **Outcomes/follow-ups** already live on the contact; the join lets the consumer roll them up per posting without schema churn there.

Backward-compatible: Campaign-mode rows simply have no `applications`/link entries.

---

## 7. Command & handoff surface (consumer I/O)

Mirror the `CHROME_PRODUCER_CONTRACT` pattern — stable paths the consumer can build against.

- **New verb:** `/network-jobs <feed.json> [--draft] [--auto-select]`
  - Parse → for each posting: discover (role-biased) → ingest → rank → (optional draft) → link to `job_id`.
  - Prefer a distinct verb over overloading `/network-import` because the entity (postings) and grouping/targeting differ; keeps both contracts clean. (Alternative: `/network-import --mode application` — note as an open decision in §10.)
- **Canonical paths:**
  | Path | Purpose |
  |---|---|
  | `runs/applications/<YYYY-MM-DD>-feed.json` | the consumer's daily feed (input) |
  | `runs/applications/<YYYY-MM-DD>-status.json` | per-`job_id` rollup written back by the agent (output) |
- **Status rollup (what the consumer reads to drive apply/drop):** per `job_id` → best referral state across its linked contacts: `searching → reached → conversation → referral_asked → referred → none`, plus contact count and any `SPONSORSHIP_YES/NO` signal. Also exposed as `/network-jobs --status --json`.
- **`runs/` stays git-ignored** (real contact data + runtime state), same as the Chrome contract.

The agent never touches LinkedIn here either: discovery is off-platform; the human sends. Application mode changes *what we target and how we track it*, not the ban-safety line.

---

## 8. Generalizing beyond one user (Phase B alignment)

This use case is a forcing function for the de-hardcoding already on the roadmap (`ROADMAP.md` Phase B, v0.9 — ~8 spots hardcoded to one user: aerospace roles, school signals, FocusArea taxonomy, employer list, identity terms, a fallback that names the user, persona-template school framing, ask-rotation school angle).

Make Application mode **profile-driven, not person-driven:**

- The feed's `profile_ref` selects a **user profile/config** that supplies: identity + voice, school(s), default target functions, and the **focus-area taxonomy**. No person is baked into code.
- `function` / `target_keywords` are free-form in the feed and **resolve against the active profile's taxonomy** — so the format is field-agnostic. A software engineer (`function: "BACKEND"`, keywords `["distributed systems","Go","Kafka"]`), a nurse, or a finance analyst all use the *same* feed schema; only the profile + the postings differ.
- The existing aerospace-specific taxonomy becomes the **default profile**, not a hardcoded constant — zero regression for the current user, full generality for everyone else.

**Why this matters for adoption:** Campaign mode (mass company outreach) is one audience. Application mode (a referral for *this specific role I'm applying to*) is the larger, more universal audience — it matches how most people actually job-hunt. Shipping both, profile-driven, turns the plugin from "one aerospace job seeker's tool" into "any job seeker's referral engine."

---

## 9. Backward compatibility

- Purely additive. Campaign mode (`/network-find`) and company-capture import (`/network-import`) are untouched.
- The canonical contact record is reused verbatim inside each posting.
- New tables/links are empty for existing flows; existing tests stay green.
- No change to discovery providers, drafting, critic, follow-ups, timing, or outcomes — only new context flowing in and new linkage/reporting flowing out.

---

## 10. Phased build plan (small releases, roadmap-style)

- **P1 — data + parser (no behavior change):** `applications` table + `application_contacts` link (migration), `Application` Pydantic model, application-feed parser + `validate_application_feed`. Ships dark.
- **P2 — `/network-jobs` + role-biased discovery:** feed role context into the Finder/ranker (reuse `team-matches-target-role`), link contacts to `job_id`. Per-posting find works end to end.
- **P3 — drafting + reporting:** role-aware draft context (drafts name the role), artifacts + outcomes tagged with `job_id`, the `status.json` rollup + `/network-jobs --status`.
- **P4 — generalize:** introduce `profile_ref` + profile config; move the ~8 hardcoded spots into the default profile (this is the v0.9 de-hardcode work, scoped by this use case).

**Exit:** from a scored job feed, the agent produces role-relevant, ranked, well-hooked referral candidates per posting; drafts that name the role; and a per-`job_id` referral state the consumer polls to decide apply vs drop — for any user via a profile.

---

## 11. Open decisions (confirm before building)

1. **Verb vs flag:** new `/network-jobs` (recommended, cleaner) vs `/network-import --mode application`.
2. **Contact↔posting cardinality:** `application_contacts` join table (recommended) vs single `contacts.job_id` FK for v1.
3. **Cross-mode dedup:** if a contact was already found via Campaign mode, dedup by `linkedin_url` and just add the `job_id` link? (Recommended yes.)
4. **Discovery budget per posting:** how many candidates per req before stop (best-effort-to-N, N configurable per profile) — and whether multiple reqs at one company share a single discovery pass to save provider quota.
5. **Profile config shape:** reuse/extend the existing `voice.md` + `resume_library.yaml`, or a new `profile.yaml` that references them.

---

**Summary for the agent's maintainer:** there is a real consumer that scores postings and needs *per-application* referral candidates with a *posting↔contact* link and a per-posting state to read back. It fits the existing two-front-door architecture: add an **application-feed input mode** that reuses the canonical record and the ranker's role signal, link contacts to a `job_id`, report per posting, and make it **profile-driven** so it serves any field — not one person.
