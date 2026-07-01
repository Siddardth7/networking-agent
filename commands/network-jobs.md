---
description: Application mode (#59/#60) — from a scored job feed, find per-posting referral candidates on the HOST Claude's tokens, optionally draft role-aware notes, and read back a per-job_id referral status. Parse feed → per posting: persist the posting → role-biased discover (HTTP) → classify each (host tokens) → ingest → link → (optional) draft naming the role → status rollup.
---

# /network-jobs

The **Application-mode** front door (Phase B). Where `/network-find-here` targets
a *company*, this targets a *job posting*: it reads a scored application feed and,
for each posting, finds the right referral candidates **on that role's team**,
then links them to the posting's `job_id` so the consumer can later ask "do we
have a referral for this req yet?".

Reuses the host-token Finder end to end — discovery is HTTP, classification runs
on **your** (the host model's) tokens via the `networking-classifier` subagent,
and the deterministic Python does parsing, persistence, hooks, ranking, and
linkage. No `ANTHROPIC_API_KEY` topup.

Feed path (git-ignored, mirrors the Chrome producer contract):
`runs/applications/<YYYY-MM-DD>-feed.json` — schema `application-feed/v1` (see
`docs/APPLICATION_FEED_INPUT_DESIGN_2026-06-30.md` §4).

## Flow

1. **Plan (deterministic — parse + persist postings)** — parse the feed, write
   each `applications` row, and get the per-posting work list:
   ```
   python -m src.cli.network_jobs_host plan <feed.json>
   ```
   → `{"postings": [{job_id, company, company_slug, role_title, location,
   target_keywords, precaptured_contacts}, …], "report": {…}}`. The `report`
   counts any postings the parser rejected (missing required fields, duplicate
   `job_id`) — surface it; never treat a thin feed as full coverage.

2. **For each posting** in `postings`:

   a. **Role-biased discover (HTTP — no LLM)** — pass the posting's
      `target_keywords` to bias discovery toward the role's team:
      ```
      python -m src.cli.network_classify_host discover <company_slug> \
          --limit <N> [--location "<location>"] \
          --keywords "<comma-joined target_keywords>"
      ```
      → a JSON list of `{"candidate": {…}, "context": {…}}`. If it's empty, log
      "no candidates for <job_id>" and move on (best-effort-to-N — no silent caps).

   b. **Classify each (host tokens)** — hand every `context` to the
      `networking-classifier` subagent; pair each `candidate` with its returned
      `{persona, focus_area, hook_signal}` into the ingest payload.

   c. **Ingest (deterministic — no LLM)** — save the contacts under the company:
      ```
      echo "<payload>" | python -m src.cli.network_classify_host ingest <company_slug>
      ```

   d. **Link to the posting (deterministic)** — pipe the discovered `candidate`
      objects to link them to this `job_id` (matched to the just-ingested rows by
      canonical URL / name — a contact already present from Campaign mode is
      linked, not duplicated):
      ```
      echo "<candidates JSON>" | python -m src.cli.network_jobs_host link <job_id> <company_slug>
      ```
      → `{"job_id": "…", "linked": <N>, "unresolved": <M>}`.

3. **(Optional) Draft, role-aware (#60)** — for a posting's linked contacts,
   draft on host tokens as usual (`/network-draft-here` flow), but pass the
   posting's `job_id` so the note names the specific role (a named-role ask
   out-converts a generic company ask):
   ```
   python -m src.cli.network_draft_host context <contact_id> <CHANNEL> --job-id <job_id>
   ```
   → the grounding gains a `posting` block (`role_title`, `job_url`); the
   `networking-drafter` subagent names the role. **Ask-rotation groups by
   posting**: pass only *this posting's* contact ids to the drafter run so
   same-req contacts get distinct ask angles (`assign_ask_angles` already takes an
   arbitrary id list — just scope the ids to the posting).

4. **Report** the per-posting counts (linked / unresolved) back to the user.

## Status rollup (`--status`, #60)

Read the per-`job_id` referral state the consumer polls to decide apply vs drop —
a derived **view** over each posting's linked contacts' outcomes (`searching →
reached → conversation → referral_asked → referred`, plus contact count and any
`SPONSORSHIP_YES/NO`); `none` means no candidates yet.

```
# all postings → the canonical status file
python -m src.cli.network_jobs_host status > runs/applications/$(date +%F)-status.json
# one posting
python -m src.cli.network_jobs_host status --job-id <job_id>
```
→ `{"postings": [{job_id, company, role_title, status, contacts, sponsorship}, …]}`.
It's a read-only rollup, not a new state machine — the per-contact outcome (`#15`,
`/network-outcome`) remains the source of truth.

## Why role-biased, not role-ranked (yet)

P2 biases **discovery** with the posting's free-form `target_keywords`. It does
**not** wire the ranker's `target_focus` signal: `target_focus` is a fixed
`FocusArea` enum, and resolving free-form keywords → enum needs the profile
taxonomy (P4). Ranking still runs (it just uses the generic company target), so
candidates are ordered; the role signal sharpens in P4.

## Notes

- `discover` needs a discovery key (`APIFY_API_KEY` or `SERPER_API_KEY`).
- Multiple postings can share a company; each gets its own `job_id` link, so the
  same contact can back more than one req (why the link is a join table, not an FK).
- `--draft` / `--auto-select` are not wired in P2 (drafting is P3); discover →
  ingest → link is the P2 loop.
