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
   → `{"profile": "<name>", "postings": [{job_id, company, company_slug,
   role_title, location, target_keywords, target_focus, precaptured_contacts},
   …], "report": {…}}`. The `report` counts any postings the parser rejected
   (missing required fields, duplicate `job_id`) — surface it; never treat a
   thin feed as full coverage. The feed's `profile_ref` selects the active
   profile (#61); `target_focus` is the posting's `function`/`target_keywords`
   resolved against that profile's focus taxonomy (`null` when ambiguous — the
   rank signal is simply skipped).

   **If `profile` in the plan output is anything other than `default`, prefix
   EVERY subsequent command in this flow (discover / ingest / link / draft /
   critic) with `NETWORKING_AGENT_PROFILE=<profile_ref>`** — that env var is
   how the named profile stays active for classification labels, hooks,
   drafting identity, and guardrails; without it those stages fall back to the
   default profile.

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

   c. **Ingest (deterministic — no LLM)** — save the contacts under the
      company; pass the posting's `target_focus` (when non-null) so contacts on
      the role's team score the ranker's team-match signal (#61):
      ```
      echo "<payload>" | python -m src.cli.network_classify_host ingest <company_slug> \
          --target-focus "<target_focus>"
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

4. **Report** the per-posting counts (linked / unresolved) back to the user —
   and coach as you go (#78): for each posting's top candidates, one line on
   WHY (their `rank_reasons` + whether `target_focus` matched the role's
   team). For the strategy conversation, `/network-coach`.

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

## Role-biased AND role-ranked (P4, #61)

P2 biased **discovery** with the posting's free-form `target_keywords`. P4 adds
the **ranker** signal: `plan` resolves `function`/`target_keywords` against the
active profile's focus taxonomy into `target_focus`, and `ingest
--target-focus` scores matching contacts (+10 team-match). The whole pipeline
is profile-driven — the feed's `profile_ref` picks the profile
(`~/.networking-agent/profile.yaml`, or `profiles/<ref>.yaml` for a named ref);
no profile file means the built-in default (the original aerospace user).

## Notes

- `discover` needs a discovery key (`APIFY_API_KEY` or `SERPER_API_KEY`).
- Multiple postings can share a company; each gets its own `job_id` link, so the
  same contact can back more than one req (why the link is a join table, not an FK).
- `--draft` / `--auto-select` are not wired in P2 (drafting is P3); discover →
  ingest → link is the P2 loop.
