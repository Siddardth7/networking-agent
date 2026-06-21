# Cowork + Chrome producer — capability assessment & workflow

**Date:** 2026-06-21  **From:** Claude Cowork (the proposed producer)
**Re:** Brief `docs/COWORK_CHROME_BRIEF_2026-06-21.md`
**Validated against:** `src/agents/importer.py` (`validate_contacts_file` /
`import_contacts`), `commands/network-import.md`, contract §2 of
`FLEXIBLE_INPUT_DESIGN_2026-06-21.md`.

**One-line verdict:** I can reliably be a **read-only, human-paced co-pilot** that
extracts name / title / profile-URL / location / headline from LinkedIn search
and Alumni-tool *results lists* and emits valid canonical JSON. I should **not**
be an autonomous high-volume scraper — that risks Sid's job-search account, which
is the one asset we can't replace. The good news from §10–11: at one company/day
with a ~20-invite ceiling we only need ~12–25 contacts, so a conservative
read-only flow is sufficient. **I recommend a semi-manual division of labor.**

---

## A. Capabilities & honest limits

**1. Can Claude-in-Chrome use the authenticated session, and is it safe (ToS)?**
Yes, technically — the Chrome extension drives Sid's real, logged-in browser, so
it operates inside the existing session with no credentials handled by me. But
candidly: LinkedIn's User Agreement prohibits automated/bot access and scraping,
and LinkedIn runs aggressive automation detection (behavioral + checkpoints).
Any automation carries non-zero account risk. Because this is Sid's live
job-search account, I'd treat safety as the top constraint and operate
**read-only and human-paced**, not as a bulk crawler. Safeguards I'd apply:
- **Read, don't act.** I navigate/read results pages and extract text. I never
  click Connect, Message, Follow, or Endorse. Sending stays 100% manual in the
  agent's approval step.
- **Human-in-the-loop navigation option.** Preferred mode: *Sid* opens the
  Alumni tool / search and applies filters; I read what's on screen. This keeps
  the click-pattern human and removes the riskiest signal (automated query
  spamming).
- **Stay in results lists; rarely open profiles.** The single biggest detectable
  signal is opening many individual profiles fast. I read the cards instead.
- **Hard daily caps:** ≤ ~3–5 search/results page loads and ≤ ~10–15 profile
  opens *per day*, with deliberate pacing (seconds between actions, not
  machine-speed). Well under LinkedIn's free commercial-use ceiling.
- **Stop on any checkpoint/CAPTCHA/"unusual activity"** immediately and hand
  back to Sid. Never retry through a block.
- **No storage of anything beyond the public profile fields we emit.**

**2. Alumni tool vs People search — which is more reliable for me?**
Both are drivable; the **Alumni tool is more reliable and higher-value.**
- *Alumni tool* (School page → **Alumni** → "Where they work" = company +
  "What they do"): renders a clean, paginated list of confirmed alumni at a
  company with name, headline, and location visible on each card. Because the
  tool itself guarantees the school link, I can tag `persona: ALUMNI` with
  confidence and skip a classifier call. This is the goal-critical source.
- *People search* (company + location + title/keyword): also readable, but the
  results UI is busier, location filtering is via the left-rail facet (reliable
  when I/you set it), and persona is inferred from the title, not guaranteed.
  I'd use it for the recruiter + a couple of peers after alumni.
- Reliability ranking for me: **Alumni tool > People search (with location
  facet set) > Google/Serper site-search** (which can't filter location well —
  that's the plugin's free fallback, not mine).

**3. Which fields can I reliably extract, and do I need to open each profile?**
From the **results list / alumni cards**, reliably, without opening profiles:
`full_name`, `title` (the headline line), `linkedin_url` (the card's profile
link), and usually `location`. That covers everything the contract recommends.
- **`about`:** I capture the **headline** (one line, present on the card) and use
  it as `about` — that's enough to ground the hook and classifier. The *full*
  "About" section requires opening the profile, which I deliberately avoid at
  volume. So treat `about` as "headline-grade," not full bio.
- **`email`:** **Cannot** reliably get from LinkedIn. I omit it. Email is the
  plugin's job (Hunter pattern inference), per §10–11.

**4. How many per session before throttling, and how do I pace?**
LinkedIn free commercial-use limit is ~250–350 searches/month and ~hundreds of
profile views/day, but *throttling/checkpoints trigger on behavior, not just
counts.* For this campaign I don't need volume: ~12–25 contacts/company comes
from **~2–4 results-page reads + 0–3 profile opens.** Pacing rules:
- One company/day. Per day: ≤ ~5 page loads, ≤ ~15 profile opens (usually far
  fewer), human-speed.
- Monthly footprint ≈ 30 companies × ~3 searches ≈ ~90 searches — comfortably
  under the ~250–350 free cap, leaving headroom for Sid's own browsing.
- If a results page won't load or a checkpoint appears: stop, don't retry-loop.

---

## B. Workflow I'd design (§5 Q5–7)

**5. Step-by-step capture flow for `company + location (+ school = UIUC)`:**

1. **Alumni-first.** Open (or have Sid open) the **UIUC school page → Alumni**.
   Set "Where they work" = *company*, "What they do" = engineering keywords.
   Read the cards; extract name / headline / URL / location. Tag every one
   `persona: ALUMNI`. Target ~8–15 here — this is the priority persona.
2. **Location filter.** Keep only cards whose location matches the target metro
   (e.g., "Dayton, OH" / "Greater Dayton"). LinkedIn locations are metro-level,
   so I match on metro, not exact city, and keep near-matches with a note.
3. **One recruiter.** Switch to **People search**: company + location + title
   contains `Recruiter OR "Talent" OR "Talent Acquisition" OR Sourcer`. Take the
   best 1, tag `persona: RECRUITER` (title makes this confident).
4. **A few peers.** People search: company + location + target role keywords
   (composites, structures, stress, manufacturing, GNC…). Take ~2–5. Leave
   `persona`/`focus_area` **blank** so the agent's classifier assigns them.
5. **Dedup** by normalized `linkedin_url` (the importer's primary key); fall back
   to `full_name`+company. Drop anyone already captured for this company on a
   prior day (I check the existing `runs/` files).
6. **Assemble** the canonical JSON (file-level `company`, `location`, `source:
   "chrome"`; per-contact fields), **show Sid the list for a 30-second
   confirm**, then write `runs/<date>-<slug>.json`.
7. **Validate** (`--validate`, no writes) and report the count + any warnings.

Order matches sourcing priority **ALUMNI > RECRUITER > PEER**, and naturally
front-loads the two goals: alumni carry the sponsorship + who-to-talk-to asks;
the recruiter *owns* the sponsorship answer.

**6. Failures / partial results / resume:**
- *Search returns nothing:* widen one axis at a time — drop the location facet
  (keep location in the record but note it's company-wide), then broaden role
  keywords. Report "alumni: 0 at this location" honestly rather than padding.
- *Page won't load / transient error:* one calm retry, then stop and hand back.
- *Rate-limited / checkpoint mid-run:* **stop immediately**, write whatever was
  already captured to the `runs/<date>-<slug>.json` file (partial is fine — the
  importer dedups), and tell Sid. Next session resumes by reading that file and
  only adding new uniques. Because output is a dated per-company file, **resume
  is just "append new uniques to today's file."**
- Partial is acceptable: the campaign needs ~1 good alumni reply, not 50 rows.

**7. When I tag persona vs leave it blank:**
- **Tag `ALUMNI`** — when the contact came from the Alumni tool (the tool
  guarantees the school link). High confidence.
- **Tag `RECRUITER`** — when the title clearly contains Recruiter / Talent /
  Talent Acquisition / Sourcer.
- **Tag `SENIOR_MANAGER`** — only when the title is unambiguous (Director / Head
  of / VP / Sr. Manager). If borderline (just "Manager"), leave blank.
- **Leave blank** — for general engineers/peers and anything ambiguous, so the
  agent's 1-Haiku classifier decides. I also **leave `focus_area` blank almost
  always** (only set it when the title literally names it, e.g. "Stress Analysis
  Engineer" → `STRUCTURAL_ANALYSIS`) — the classifier is better at this than I am
  from a headline. Per the contract: *when unsure, omit.*

---

## C. Orchestration & scheduling (§5 Q8–10)

**8. One company/day from a target list + tracking done state.**
Yes. I can run a Cowork **scheduled task** once per weekday morning that pops the
next undone row and runs the capture. **Give me the target list as a file in this
folder**, not a Cowork table (a file persists, diffs cleanly, and engineering can
read it):

`runs/targets.csv` with columns: `company, location, school, status` (status
blank → done). I mark a row done by the **presence of its output file**
`runs/<date>-<slug>.json`, and also stamp `status=done` in the CSV. That gives
both you and me a durable "what's left" view without a database.

**9. Should I call `--import … --draft` myself, or just drop the file?**
**I run `--validate` always (it writes nothing — zero risk), then run `--draft`
myself after Sid's one-line confirm.** Rationale: the import/draft step is pure
local Python that never touches LinkedIn, and *sending is still gated* by the
marketer approval artifact downstream — so auto-drafting doesn't bypass any human
gate that matters. Auto-drafting also makes the daily cycle one motion. The only
human checkpoints that need to exist are (a) confirm the captured list looks
right before draft, and (b) the existing approval gate before send. So:
**validate → confirm → draft, all by me; send stays manual.**

**10. Output path / naming.**
`runs/<YYYY-MM-DD>-<company-slug>.json`, slug via the importer's `_slugify`
(`Joby Aviation` → `joby-aviation`). Example:
`runs/2026-06-21-joby-aviation.json`. This is self-documenting, sorts by date,
dedups per company, and is what the scheduler reads to know what's done. Keep
`runs/targets.csv` alongside as the queue.

---

## D. Fit & fallbacks (§5 Q11–14)

**11. Confident vs risky.**
- **Confident:** reading Alumni-tool + People-search *results lists*; extracting
  name/title/URL/location/headline; tagging ALUMNI and RECRUITER; dedup;
  emitting valid canonical JSON; validating and importing; scheduling one/day.
- **Risky / unreliable:** high *volume* per company; full "About" text without
  opening profiles; perfectly exact-city location (LinkedIn is metro-level);
  fully *autonomous* navigation without occasional checkpoints. And the standing
  risk: **any** automation against LinkedIn carries ToS/account exposure — which
  is why I cap hard and prefer human-paced/read-only.

**12. Better division of labor?** **Yes — semi-manual, and I recommend it.**
Sid (or I, paced) navigates and applies filters; I read the rendered results,
extract, dedup, and assemble JSON; **Sid eyeballs the list for 30 seconds**; then
I validate + draft. This keeps the click-pattern human (lowest account risk),
puts a human judgment gate exactly where it's cheap, and still automates the
tedious part (transcribing 15 cards into clean JSON + running the pipeline).

**13. Things I can reliably *add* (and things I can't get).**
- *Can add, useful:* `connection_degree` (1st/2nd/3rd — visible on cards),
  `alumni_confirmed` (true when sourced via the Alumni tool),
  `current_employee` (the card shows current company), `headline` (already →
  `about`), and a `captured_at` timestamp + `source_detail`
  (`linkedin_alumni_tool` vs `linkedin_people_search`).
  **Caveat:** the importer's alias map only maps a fixed field set and *silently
  ignores unknown keys* (`importer.py` `_apply_aliases`). So these extras are
  **dropped, not stored**, unless engineering adds them to the schema/alias map.
  I'll emit them only if you want them honored — say the word.
- *Cannot reliably get:* **email** (omit — Hunter's job), full About text at
  volume, and exact sub-metro location.

**14. Proposed contract changes (concrete).**
The contract is already a good fit. Small, optional suggestions:
1. **Honor `school` at file level** (currently `_read_rows` only lifts
   `company/company_slug/location/source` from file meta). Useful campaign
   context for UIUC-alumni runs and trivial to add.
2. **Add `alumni_confirmed` (bool) and `connection_degree` (str)** to the alias
   map if you want my Alumni-tool ground-truth and degree to flow through —
   `alumni_confirmed:true` is a stronger ALUMNI signal than a classifier guess,
   and `connection_degree` could prioritize 1st/2nd-degree for the LinkedIn
   channel. Without schema support these are dropped today.
3. **No renames needed** — `about`/`headline`/`summary` already alias to `about`,
   which matches what I produce. Keeping `email` optional is correct.

If you don't want to touch the schema, my output works **as-is today** — I'll
just emit the recommended fields and omit the extras.

---

## Sample `contacts.json` (Joby Aviation, Dayton OH)

A representative file from this flow lives at
`docs/chrome-capture.example.json` and is **valid against
`validate_contacts_file`** (file-level `company` + `location`; every contact has
`full_name`, `title`, `linkedin_url` → no missing-company errors, no
no-channel/no-title warnings). It is **synthetic on purpose**: I have not run a
live LinkedIn capture (the brief said don't build yet, and a live scrape is
exactly the ToS-sensitive action I'd gate behind your go-ahead). Names/URLs are
placeholders showing the *shape and persona mix* my flow produces — alumni-first,
one recruiter, a couple of unlabeled peers. On your go-ahead I'll run the real
read-only capture and replace placeholders with live data.

```json
{
  "company": "Joby Aviation",
  "company_slug": "joby-aviation",
  "location": "Dayton, OH",
  "source": "chrome",
  "contacts": [
    {
      "full_name": "[Alumni 1]",
      "title": "Flight Sciences Engineer",
      "linkedin_url": "https://www.linkedin.com/in/EXAMPLE-alum-1",
      "location": "Dayton, OH",
      "about": "UIUC MS Aerospace '22 — aerodynamics & flight dynamics",
      "persona": "ALUMNI"
    },
    {
      "full_name": "[Recruiter]",
      "title": "Technical Recruiter, Engineering",
      "linkedin_url": "https://www.linkedin.com/in/EXAMPLE-recruiter",
      "location": "Dayton, OH",
      "about": "Hiring across structures, propulsion, manufacturing at Joby",
      "persona": "RECRUITER"
    },
    {
      "full_name": "[Peer 1]",
      "title": "Stress Analysis Engineer",
      "linkedin_url": "https://www.linkedin.com/in/EXAMPLE-peer-1",
      "location": "Dayton, OH",
      "about": "Airframe stress, fatigue & damage tolerance"
    }
  ]
}
```

(Full 6-contact sample in `docs/chrome-capture.example.json`.)

---

## Exact daily commands / steps

**Inputs once:** `runs/targets.csv` →
```
company,location,school,status
Joby Aviation,"Dayton, OH",UIUC,
Sierra Space,"Louisville, CO",UIUC,
AST SpaceMobile,"Midland, TX",UIUC,
```

**Each day (the cycle I run):**
1. Pop the next `status`-blank row from `runs/targets.csv`.
2. Capture (read-only, paced): UIUC Alumni tool → company+location → ~8–15
   alumni; People search → 1 recruiter + ~2–5 peers. Dedup vs prior `runs/`.
3. Write `runs/<YYYY-MM-DD>-<slug>.json` (canonical JSON, `source:"chrome"`).
4. **Validate (no writes):**
   ```
   python -m src.cli.network_import runs/2026-06-21-joby-aviation.json --validate
   ```
5. Show Sid the captured list + validate summary → 1-line confirm.
6. **Import + draft** (local only; send still gated by approval artifact):
   ```
   python -m src.cli.network_import runs/2026-06-21-joby-aviation.json --draft
   ```
   (company/location are inside the file; add `--company "Joby Aviation"` only if
   a file ever omits it.)
7. Mark the row `status=done` in `runs/targets.csv`.
8. Sid reviews the drafts in the approval artifact and sends (~12–15 LinkedIn
   invites to alumni + email the rest). I never send.

**Scheduling:** one Cowork scheduled task on weekday mornings runs steps 1–5 and
pauses at the confirm; on Sid's OK it finishes 6–7. If LinkedIn checkpoints mid-
capture, it writes the partial file and stops.

---

## What I need from engineering to wire this in
1. Confirm the **output dir** (`runs/`) and **queue file** (`runs/targets.csv`)
   names, or give me your preferred paths.
2. Decide on the **optional field additions** (Q13/Q14): `school` (file-level),
   `alumni_confirmed`, `connection_degree`. I'll emit them iff the alias
   map/schema will honor them — otherwise I omit to keep files clean.
3. Confirm **auto-`--draft` after confirm** is acceptable (I believe it is, since
   the send gate is downstream), or tell me to stop at the file + `--validate`
   and let Sid run `--draft`.
