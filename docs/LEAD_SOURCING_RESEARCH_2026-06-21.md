# Lead Sourcing Research — getting to 50 leads/run

**Date:** 2026-06-21  **Status:** Research / analysis (no code yet)
**Question:** How do we reliably get ~50 leads each run? What is the best
contact source? What free fallbacks exist, and are any paid sources worth it?

---

## TL;DR / recommendation

1. **Our current source (Serper + Google `site:linkedin.com/in`) is fine for
   precision but structurally cannot guarantee 50 leads per company.** It
   depends on what Google has indexed for one narrow company+role query, and it
   stops early when paging stops yielding new unique profiles. Real-world yield
   for a mid-size aerospace company is often 5–25, not 50.
2. **The single highest-leverage add is Apollo.io's People Search API
   (`mixed_people/api_search`).** It is purpose-built for exactly this query
   ("people at company X with title Y"), returns up to **100 records/page, no
   credit cost for the search itself**, and pages to 50k. It needs a **paid
   plan** for API access (~$49–79/mo) — and that is the one paid source clearly
   worth it for the 50-leads goal.
3. **Best free fallback is a *combination*, not one tool:** keep Serper as the
   free default but broaden it, add **People Data Labs free tier (100
   person-search records/month)** as a second free source, and use **Hunter
   free (50 credits/mo) for email *pattern* inference** rather than per-lookup
   email (that also lands roadmap #3).
4. **Do NOT build on direct LinkedIn scraping as the primary path.** The
   category leader, **Proxycurl, was sued by LinkedIn and shut down on
   2025-07-04.** Apify scraper actors are cheap and useful as an *optional*,
   user-supplied-key escape hatch, but they carry the same ToS/legal risk that
   killed Proxycurl and should not be the default.
5. **Also reconsider what "a run" means.** Today a run = one company. Reaching
   50 *aggregate* leads by fanning the finder across 5–10 target companies
   (5–10 each) is a free, low-risk way to hit the number without forcing 50 out
   of one company that may not have 50 indexed.

---

## 1. What we use today

| Aspect | Current implementation |
|---|---|
| **Discovery source** | **Serper.dev** (Google Search API). Query: `site:linkedin.com/in "<Company>" (quality engineer OR supplier quality OR MRB engineer OR …)` — 9 hard-coded aerospace role keywords (`src/agents/finder.py:_ROLE_KEYWORDS`). |
| **Paging** | Batches of `num=10`, increments `page`, dedups by LinkedIn URL, **breaks as soon as a page adds 0 new uniques** (`serper.py:search_linkedin_profiles`). |
| **What a "lead" contains** | Name, title, LinkedIn URL, search snippet — all parsed out of the Google result. No LinkedIn API. |
| **Email** | **Hunter.io**, **opt-in, default OFF** (`enable_email_enrichment=False`). LinkedIn-only contacts otherwise. |
| **Per-contact processing** | 1 Haiku classify call (persona + focus + hook signal) + deterministic hook. |
| **Scope of a run** | **One company** (`orchestrator` runs a single slug; `finder_limit` default **5**). |
| **Self-imposed caps** | `serper_monthly_limit=100`, `hunter_monthly_limit=25`, `finder_limit=5`. |

### Why this can't reliably hit 50

- **Google result ceiling.** A narrow `site:linkedin.com/in "Company" (roles)`
  query returns a bounded set; Google rarely serves more than a few dozen
  *unique, relevant* organic results, and deep pages (page ≥ 4) degrade into
  dupes/irrelevant hits. The `added_this_page == 0` break then stops the loop.
- **Narrow keyword filter.** The 9-role OR-list is tuned for *precision* (Sid's
  exact target roles), which is the opposite of what you want for *volume*.
- **One-company scope.** Even a perfect query can't invent 50 matching public
  profiles for a 200-person startup. Sid's targets (AST SpaceMobile, Joby,
  Sierra Space, etc.) are mid-size — many simply don't have 50 indexed engineers
  in the target roles.
- **`finder_limit=5`** is the current intent; raising it to 50 is necessary but
  *not sufficient* given the above.

---

## 2. The 2026 landscape shift (important context)

- **Proxycurl is dead.** LinkedIn filed suit 2025-01-24 (fake accounts +
  scraping millions of profiles); Proxycurl shut down **2025-07-04** rather than
  fight. It was *the* developer LinkedIn-data API. Anything that resells scraped
  LinkedIn data now carries demonstrated legal risk.
- **"LinkedIn scraping is dead" is the 2026 consensus** for anything you'd build
  a product on. The compliant survivors are (a) **licensed B2B databases**
  (Apollo, PDL, ZoomInfo, Cognism — they own/curate their data) and (b)
  **pay-per-use scraper actors** (Apify/Bright Data) where the ToS risk sits
  with the actor operator and the user supplies their own key/consent.
- Implication for us: **prefer a licensed people-search API (Apollo/PDL) as the
  volume source**, keep Google/Serper as the free default, and treat raw
  scrapers as an opt-in power-user lane — never the shipped default.

---

## 3. Source-by-source analysis

Costs/limits verified 2026-06-21 (see Sources). "Fit" is for *our* job-seeker
use case: find 50 employees at an aerospace company by role, get name + title +
LinkedIn URL (email optional).

| Source | Model | Free tier | Paid entry | Returns | 50/run? | ToS/risk | Fit |
|---|---|---|---|---|---|---|---|
| **Serper** (current) | Pay-as-you-go credits | **2,500 one-time** | $50/50k credits ($1/1k) | Google SERP → parse LinkedIn snippets | Maybe, per company | Low (search API) | **Good default**, weak ceiling |
| **Apollo.io** | Subscription + credits | Free plan **but no API** | ~$49–79/user/mo (API access) | People search by title/seniority/company; **`api_search` = 100/page, no credit**, no email | **Yes, easily** | Low (licensed DB) | ★ **Best volume source** |
| **People Data Labs** | Credits | **100 person lookups/mo** (emails gated) | $98/mo → 350 credits | Person Search by title/company/skills; 1 credit/record | Yes, but 100/mo total on free | Low (licensed) | **Best free-ish dev source / enrichment** |
| **Hunter.io** | Credits | **50 credits/mo** | from ~$34/mo | Domain search + **email pattern** + verify (~87% acc) | n/a (email, not discovery) | Low | **Best free email *pattern* source** → roadmap #3 |
| **Snov.io** | Credits | 50 credits/mo | from ~$39/mo | Email finder + sequences; ~81% acc, staler data | n/a | Low | Hunter alternative; weaker data |
| **Apify LinkedIn actors** | Pay-per-result | platform free $5/mo credits | usage | Profile/People-Search scrape: **$0.10/search page + $0.004/profile** (no email), ~$0.01 w/ email; profiles $2–4/1k | Yes, cheaply (~$0.30/50) | **Higher (scraping LinkedIn)** | Opt-in power lane only |
| **ZoomInfo / Cognism** | Enterprise seat | none | $$$ (≥ $10k/yr typical) | Premium verified B2B | Yes | Low | Overkill for a solo job-seeker tool |
| **Google Programmable Search** | Free API | 100 queries/day free | $5/1k after | Same SERP idea, slower | Weak (same ceiling as Serper) | Low | Free Serper-style fallback |

### Notes that matter

- **Apollo `api_search` is the unlock.** Official docs: the People *API Search*
  endpoint (`/api/v1/mixed_people/api_search`, NOT `…/search` which 403s on
  Basic) **does not consume credits**, returns up to **100/page**, pages to 500
  (50k records), filters on `person_titles`, `person_seniorities`,
  `organization_ids`, location, company size, and even *active job postings*. It
  **does not return emails/phones** — but our default pipeline doesn't use email
  anyway, so the free-of-credit search is a near-perfect fit. *Caveat to spike:*
  confirm the exact response fields include `linkedin_url` (Apollo person
  records carry it; the api_search field set is worth a 10-minute verification).
  **Cost reality:** free plan has *no* API; you need a paid tier for the master
  API key. That's the one subscription worth paying for here.
- **PDL free = 100 records/month total**, emails/phones restricted on free. Great
  as a *second free source* and as enrichment infrastructure, but 100/mo means
  ~2 runs of 50 before it's dry — rotate it with Serper, don't lean on it alone.
- **Hunter's real value for us is the email *pattern*** (e.g.
  `f.last@company.com`) — one domain-search credit yields the format for the
  *whole* company, letting us construct addresses for every LinkedIn-only
  contact at high confidence. That is strictly better than spending scarce
  per-lookup credits (Hunter 50/mo, our cap was 25) and it *is* roadmap item #3.
- **Apify** is genuinely cheap for volume (~$0.30 for 50 profiles) and the
  People-Search actor mirrors our exact need — but it's scraping LinkedIn. Ship
  it only as an **opt-in, bring-your-own-key** provider with a clear ToS warning,
  never the default. (Same risk class as the thing that killed Proxycurl.)

---

## 4. Free vs paid — the verdict

- **Is any paid source worth it? Yes — Apollo, and only Apollo, for the
  50-leads goal.** ~$49–79/mo buys a purpose-built, licensed, credit-free people
  search that returns 50–100 structured leads per company on demand. Nothing
  free matches it for *guaranteed* volume.
- **Can we stay free? Mostly, with a combo and lower guarantees:**
  - Serper (broadened) as primary discovery — free 2,500, then cheap.
  - PDL free (100/mo) as a second discovery source to top up to 50.
  - Hunter free (50/mo) for email *patterns*.
  - Multi-company fan-out so "50/run" is met in aggregate.
  - Honest expectation: free tier gives **"up to ~50, best-effort,"** not a hard
    guarantee, for any single small company.
- **Skip:** Proxycurl (dead), ZoomInfo/Cognism (enterprise overkill), raw
  scraping as default (legal/ToS).

---

## 5. Proposed architecture (fits the existing provider abstraction)

The codebase already has a clean `SearchProvider` base + `register_provider`
pattern (`src/providers/base.py`) and a `QuotaManager`. This makes a
multi-source "best-effort to N" finder a natural extension, not a rewrite.

1. **Provider tiering with fallback.** Generalize the finder from "Serper only"
   to an ordered provider chain driven by config:
   `apollo → serper → pdl` (use whichever have keys; fall through on
   exhaustion). Each provider implements `search_people(company, roles, limit)`.
2. **"Best-effort to N" loop.** Accumulate unique leads (dedup by LinkedIn URL /
   name+company) across providers until `limit` (default raise 5 → 50) or all
   providers are exhausted — mirroring the existing opener/ask-rotation
   "accumulate to target" pattern. `log()` how many each source contributed and
   what was dropped, so a short free run never *looks* like it covered 50.
3. **Broaden the free query for volume.** Add a second, role-less pass
   (`site:linkedin.com/in "Company" (engineer OR scientist OR analyst)`) after
   the precise pass, and widen `_ROLE_KEYWORDS`. Precision-first, volume-second.
4. **Multi-company runs.** Let a run take a target *list* and fan the finder
   across it (the drafter already fans out; same `ThreadPoolExecutor` shape),
   so 50 aggregate is reachable for free.
5. **Email as a separate, optional layer.** Default discovery stays email-free
   (no Hunter spend). Add Hunter *pattern* inference (roadmap #3) as the free
   email fallback; per-lookup Hunter stays opt-in.
6. **Apify as opt-in BYO-key provider**, behind a ToS acknowledgment flag.

---

## 6. Cost model (per 50-lead company run)

| Path | Discovery cost | Email | Notes |
|---|---|---|---|
| **Serper free** | ~5 credits (of 2,500 one-time) | none | Free until grant runs out; then ~$0.005 |
| **Serper paid** | ~5 credits × $1/1k ≈ **$0.005** | none | Negligible; yield is the limiter, not cost |
| **Apollo** | **$0 credits** (api_search) + sub ~$49–79/mo | +1 credit/email | Flat monthly; search itself free → best $/lead at volume |
| **PDL free** | 50 of 100 monthly records | gated | Free but burns half the monthly grant per run |
| **Apify People-Search** | ~$0.10 + 50×$0.004 ≈ **$0.30** | +~$0.01/profile | Cheapest pay-per-use, but scraping risk |
| **Claude classify** | 50 Haiku calls / run | — | Already in pipeline; unchanged |

**Takeaway:** discovery cost is essentially free across the board — the binding
constraint is *yield and ToS*, not dollars. Apollo's subscription buys
*reliability of volume*, not raw search cost.

---

## 7. Risks / open questions to resolve before building

- **Confirm Apollo `api_search` returns `linkedin_url`** in its field set
  (10-min spike with a trial key). If not, we'd need a follow-on enrich call.
- **Apollo plan that unlocks API** — sources disagree (some say Professional
  ~$79, some say Organization ~$119, 3-seat min). Verify the cheapest tier that
  grants a master API key before committing.
- **Apollo single-user/job-seeker ToS** — it's a freemium sales tool individuals
  use daily; fine, but confirm outreach use is in-bounds.
- **Dedup across providers** — need a stable key (normalized LinkedIn URL, else
  name+company) so Apollo/Serper/PDL overlaps don't double-count toward 50.
- **Free-tier honesty** — must `log()` per-source contribution and any cap hit so
  a 22-lead free run isn't mistaken for "couldn't find 50."

---

## 8. Recommended next steps (phased)

- **Phase A (free, fast, high-value):** raise `finder_limit` toward 50; add the
  broadened role-less Serper pass + wider keywords; add the "best-effort to N"
  accumulation loop; add per-source `log()`. Gets free yield as high as Google
  allows with zero new dependencies.
- **Phase B (free fallback breadth):** add a **PDL free-tier provider** as a
  second source behind Serper; add **Hunter email-*pattern* inference** (roadmap
  #3) as the free email fallback.
- **Phase C (the volume guarantee, paid):** add an **Apollo `api_search`
  provider** as the preferred source when an API key is present. This is what
  turns "up to ~50, best effort" into "50 on demand."
- **Phase D (optional power lane):** Apify BYO-key scraper provider behind a ToS
  ack, for users who want it and accept the risk.

---

---

## 9. Addendum (2026-06-21): paid Serper/Apify worth it? + multi-account rotation

### 9.1 Is paid **Serper** worth it?

**No — not for the 50-leads goal, and probably not at all soon.**

- **Credits are not our bottleneck.** Serper paid is $50 / 50k credits =
  **$1/1k ≈ $0.001/search**. One company run ≈ 5 credits (5 pages of 10), so the
  **free 2,500 one-time grant ≈ ~500 company runs** before we pay a cent, and
  paid is then ~**$0.005/run**. For a personal job-search tool that's effectively
  free forever.
- **More credits ≠ more leads.** The per-company ceiling is *Google's result
  set*, not our credit balance. Buying Serper credits does **nothing** to raise
  yield past the ~dozens of unique profiles Google will surface for one company.
  Paying Serper solves a problem we don't have.
- **Verdict:** stay on the free grant; if it ever dries up, top up — it's pennies.
  Do not treat Serper spend as a path to 50.

### 9.2 Is paid **Apify** worth it?

**The subscription is not needed; the *pay-per-result actor* is the only cost
that matters — and only if we accept the scraping risk.**

- Apify's **Free plan is recurring**: **$5 of platform usage every billing
  cycle** (not one-time), $0.20/CU, full Store access. The LinkedIn People-Search
  actor is ~**$0.10/search page + $0.004/profile ≈ $0.30 per 50 profiles**, so
  **$5/mo ≈ ~16 runs of 50/month, free.**
- Paid plans (Starter $29 → Scale $199) only lower the CU rate and raise
  concurrency/limits. At our volume (a few runs/week) that's **wasted money** —
  the free $5/mo covers it, and you still pay the per-result actor fee regardless
  of plan.
- The real question for Apify isn't "free vs paid," it's **"use it at all?"** —
  it scrapes LinkedIn, the exact activity that got Proxycurl sued and shut down.
- **Verdict:** if we use Apify, use the **free tier as an opt-in BYO-key lane**
  with a ToS warning; the subscription isn't worth it. Cost was never the
  blocker — ToS/legal risk is.

### 9.3 Can we run **4 free accounts and rotate** them for the same output?

**No. It fails on mechanics for Serper, violates ToS on both, is detectable and
bannable, and — critically — doesn't even solve our actual problem.**

- **Serper, mechanically broken for this:** the 2,500 credits are a **one-time
  grant per account, not monthly.** 4 accounts = 10,000 credits **once**, then
  permanently dry. It is not a renewable free-output engine — so rotation buys a
  one-time bump and nothing recurring.
- **Serper ToS — explicit:** the Terms **prohibit registering more than one
  account** and state users **"must not attempt to circumvent any restrictions or
  limits placed on their … key or account."** Rotation is a direct,
  named violation → account termination risk.
- **Apify ToS:** free $5/mo *is* recurring, so 4 accounts = ~$20/mo of value —
  but multi-accounting to bypass free limits is a standard ToS violation too, and
  Apify enforces anti-abuse.
- **Detection is real and cheap for them:** shared payment card, same/over-close
  IPs, browser/device fingerprint + cookies, and "account instantly maxes its
  limit" heuristics all flag multi-accounting. These tools are *built by people
  who fight free-tier abuse for a living.*
- **It targets the wrong constraint.** Even with unlimited credits across N
  accounts, you still can't pull more than the ~dozens of unique profiles Google
  has for one company. Rotation manufactures **credits**, but our shortage is
  **yield-per-company and source breadth** — credits we already have.
- **Reputational downside specific to us:** this tool is tied to Sid's real
  identity and job search. Burning IPs/cards/accounts on a flagged-abuse pattern
  is a bad trade for ~$15/mo of saved credits we don't need.

**The legitimate version of "stack free tiers" — and it's already roadmap #4:**
rotate across **different providers**, each with its own honest free allotment —
**Serper (2,500 one-time) + PDL (100 person-searches/mo) + Hunter (50/mo, email
patterns)**. That gives the *spirit* of rotation (more free capacity, automatic
fallback) with **zero ToS violation, zero ban risk, and no account-juggling
overhead** — and it actually adds source *breadth*, which is the real fix.

**Bottom line for §9:** neither paid Serper nor paid Apify is worth buying for
the 50-leads goal (Serper free is plenty; Apify free covers it). Multi-account
rotation is ToS-violating, detectable, mechanically pointless for Serper, and
aimed at the wrong constraint. The high-leverage spend, if any, remains **Apollo
(§3)**; the high-leverage *free* move is **multi-*provider* rotation (§5)**.

---

---

## 10. Recommended plan of action — the 30-day, one-company-per-day campaign

**Usage model (from Sid):** each day pick one company + one *location* (e.g.
Joby Aviation, Dayton OH), pull 5–50 relevant contacts, draft + send, move to the
next company/location next day; finish the target list in ~30 days.
**Primary goals:** (1) land **one POC / referral** per company, (2) get
**sponsorship intel** (OPT/H-1B). Everything else is bonus.

### The reframe that changes the answer

- **50 leads is not the goal — ~1 reply per company is.** With alumni reply
  rates of 60–80%, you don't need 50; you need a *small, alumni-heavy,
  location-matched* set. Quality + the alumni/sponsorship angle beat raw volume.
- **The real bottleneck is the *send* side, not sourcing.** LinkedIn caps
  connection invites at **~100/week (~20–25/day safe) on all tiers.** Sourcing 50
  doesn't help if you can only safely send ~20/day — and burning that cap risks
  the very account Sid is job-hunting on. So sourcing should feed two channels:
  LinkedIn (capped) + cold email (uncapped).
- **Location is now a first-class filter** ("Joby *Dayton OH*", not Joby
  company-wide). This is decisive for source choice: structured people-search
  APIs (Apollo, PDL) filter on `person_locations` natively; Google/Serper
  site-search **cannot** filter location reliably (LinkedIn snippets often omit
  it). → strong point for Apollo.

### Best source for THIS plan: Apollo (one-month tactical subscription)

- Apollo `api_search` filters on **title + seniority + location + company**
  simultaneously → "Joby engineers in Dayton, OH, seniority entry–director" in one
  call, **credit-free**, 50–100/page. Exactly the query this campaign makes daily.
- **One month of Apollo Basic/Pro (~$49–59) covers the entire 30-company list**,
  then cancel. For a campaign whose payoff is a job, that's the highest-ROI
  ~$50 in the whole plan. (Spike first: confirm `linkedin_url` is in the
  `api_search` response; emails cost 1 credit each, only needed for cold email.)
- **Free fallback if not paying:** Serper precise + broadened pass with the
  location term appended (weaker, since Google can't filter location well), topped
  up by **PDL free** (budget ~3 contacts/company → ~90/mo, under the 100 cap) for
  location-accurate fills, plus **Hunter free** (≤30 domain-searches/mo) for email
  patterns. Works, lower location precision, more manual.

### Per-company daily playbook

1. **Source location-filtered, alumni-first.** Pull up to ~40–50 but weight
   discovery **ALUMNI > RECRUITER > PEER** (alumni = highest reply + tolerate the
   sponsorship ask; recruiters *own* sponsorship answers).
2. **Split across channels so neither wall is hit:**
   - **LinkedIn connection notes → alumni first, ~12–15/company.** At ~7
     companies/week that's ~100 invites — right at the safe weekly cap. Do **not**
     plan 25 LinkedIn invites × 7 companies (=175/week → throttling/restriction).
   - **Cold email → peers / recruiters / overflow** (no weekly cap; Hunter
     pattern builds the addresses). This is where pulling up to 50 pays off.
3. **Point the ask-rotation (Phase 3) at the two goals.** Ensure, per company:
   ≥1–2 alumni get the **sponsorship** angle (goal #2) and ≥1 gets the
   **who-to-talk-to** angle (goal #1 POC). A small "campaign bias" knob that
   weights the rotation toward `sponsorship` + `who-to-talk-to` would make every
   batch serve both goals directly. (The other angles stay as bonus coverage.)
4. **Send, log, move on.** Next day → next company/location.

### Scheduling reality to plan around

- LinkedIn's ~100 invites/week means **LinkedIn outreach realistically supports
  ~5–7 companies/week** at ~14 alumni-invites each. To keep a true *daily* company
  cadence, lean on **email for the non-alumni** at each company so the LinkedIn
  cap doesn't gate the schedule.
- Over 30 days: ~30 companies, ~400–450 LinkedIn invites total (well within
  4–5 weeks of cap) + email for the rest. Comfortable on every free tier except
  Apollo's API gate — which the one-month sub solves.

### What to spend / not spend (for this campaign)

| Decision | Verdict |
|---|---|
| **Apollo Basic/Pro, 1 month** | ✅ **Worth it** — only thing that nails location+title volume; ~$50 for a 30-company campaign |
| Serper free grant | ✅ Keep as free fallback; covers the campaign on the 2,500 one-time grant |
| Serper paid | ❌ Unneeded; credits aren't the constraint |
| Apify (free or paid) | ⚠️ Optional BYO-key lane only; scraping risk; not needed if Apollo or Serper is used |
| PDL free / Hunter free | ✅ Useful free top-ups (location fills + email patterns) |
| Multi-account rotation | ❌ ToS violation, detectable, and aimed at the wrong constraint |

**One-line answer:** run it **alumni-first and location-filtered, ~12–15 LinkedIn
invites + email the rest per company/day**, source with **Apollo for the
campaign month** (Serper free as fallback), and **bias the ask-rotation toward
sponsorship + who-to-talk-to** so each batch directly produces your POC and your
sponsorship intel. The 50 number was never the goal — ~1 good alumni reply is.

---

---

## 11. The $0 plan (no Apollo subscription) — without compromising the campaign

**Constraint:** budget is tight; an Apollo subscription is off the table. Can we
still run the campaign? **Yes — and the key realization is that automating
*sourcing* is the expensive/ToS-fraught part, but at this cadence we barely need
it.**

### Why manual sourcing doesn't actually compromise anything here

- **The send cap already bounds volume.** LinkedIn allows ~20–25 invites/day. So
  per company we only need ~15–25 *relevant* contacts — a set small enough to
  gather by hand in ~10–15 min, no API required.
- **Manual sourcing gives BETTER precision** for the two things that matter most
  (location + alumni) than any cheap API. LinkedIn's own data is the ground truth.
- **The agent's real value isn't sourcing — it's the pipeline:** classify → hook
  → 4-part voice draft → ask-rotation (sponsorship / who-to-talk-to) → quality
  gate. That works identically whether a contact came from Serper or a paste.

So the budget-free move is a **bring-your-own-contacts workflow**: source where
it's free and most accurate, let the agent do the drafting it's uniquely good at.

### Best FREE sources (in priority order for this campaign)

1. **LinkedIn's own search + Alumni tool — free, and the single best source for
   alumni-at-company-in-location.** A free LinkedIn account allows ~250–350
   searches/month and 500 profile views/day — *far* more than one
   company/day needs. The **Alumni tool** (your school → People → filter "where
   they work" = Joby + "what they do") surfaces UIUC alumni at a company directly;
   plain People search filters by company + location + title for peers/recruiters.
   This is goal-critical: alumni are the highest-reply, sponsorship-tolerant
   persona, and this is the most accurate free way to find them. You're already on
   LinkedIn to send — source in the same place.
2. **Apollo *free web UI* (not the API) as a power-filter.** The free plan keeps
   all 65+ filters (title + seniority + **location** + company) and lets you
   *view* matches — including LinkedIn links — for free. The catch is export:
   only **10 export credits/month, 25 records/export**, so don't rely on CSV
   export — just read the filtered list and copy the LinkedIn URLs you want.
   Gives Apollo's killer location/seniority filtering at $0, manually.
3. **Serper (current, automated) as the free first pass.** Keep it doing the
   easy company-wide pull automatically (free 2,500 grant ≈ months of runs); you
   manually add the location-specific alumni LinkedIn surfaces.
4. **PDL free (100/mo)** — Person Search filters by title + location; budget
   ~3/company → ~90/mo across the list for location-accurate fills.
5. **Hunter free (50/mo)** — email *patterns* for the cold-email channel
   (~30 domain lookups/mo, one per company).
6. **One-time freebies to enrich your manual LinkedIn URLs:** Enrich (100 free
   credits, LinkedIn-URL input), ContactOut (free *preview* API to check if an
   email exists before spending). Useful for the email channel.

### The one small thing to build to unlock this: a contact import path

Today the agent only ingests Serper-discovered contacts. To run the $0 workflow
it needs a lightweight **"import contacts" entry point** — paste or a small CSV
(name, title, LinkedIn URL, optional persona/email) → straight into the same
`contacts` table the finder writes, then the existing
selection-gate → drafter → ask-rotation → marketer pipeline runs unchanged.
This is a *small* feature and it's the highest-leverage free unlock: it turns the
agent into "you find them, I draft them perfectly," which is exactly what a
budget-constrained, manual-source campaign needs.

### Daily $0 playbook

1. **Source (~10–15 min, free):** LinkedIn Alumni tool → UIUC alumni at the
   company/location (aim ~12–15); LinkedIn/Apollo-free People search → a few
   peers + 1 recruiter. Copy name + title + LinkedIn URL.
2. **Import** the list into the agent.
3. **Agent drafts:** classify → voice draft → ask-rotation biased to
   **sponsorship** (≥1–2 alumni) + **who-to-talk-to** (≥1 alumni) → quality gate.
4. **Send:** ~12–15 LinkedIn invites to alumni + cold-email the peers/recruiters
   (Hunter pattern for addresses). Next day → next company.

### Honest trade-off

The price of $0 is **~10–15 min/day of manual sourcing** instead of ~$50/mo for
Apollo's API. At one company/day with a ~20-invite ceiling, that's a *good* trade
— and the manual data is **more** accurate for location + alumni than any cheap
API. Nothing about the campaign's outcomes (POC + sponsorship intel) is
compromised; you trade automation-of-sourcing (which the cadence barely needs)
for zero cost.

**Verdict:** skip Apollo paid. Run **manual LinkedIn/Apollo-free sourcing +
Serper automated first pass + PDL/Hunter free top-ups**, add a small **import
path**, and let the agent do the drafting. $0, no ToS issues, campaign intact.

---

## Sources

- [Apollo free plan export limits 2026 (10 export credits/mo, 25/export)](https://scrupp.com/blog/apollo-io-free-plan-export-limit-workaround) · [Apollo free credits explained](https://alexberman.com/apollo-io-free-credits)
- [LinkedIn commercial use limit 2026 (~250–350 searches/mo free)](https://www.linkedin.com/help/linkedin/answer/a564226) · [LinkedIn limits breakdown 2026 (LeadLoft)](https://www.leadloft.com/blog/linkedin-limits)
- [Free people-search APIs / free tiers 2026 (Enrich)](https://www.enrich.so/blog/people-search-api)
- [LinkedIn weekly invitation limit 2026 (~100/wk, all tiers)](https://www.outx.ai/blog/weekly-invitation-limit-linkedin) · [LinkedIn connection limits 2026 (daily/weekly)](https://www.joinvalley.co/blog/linkedin-invitation-limit-in-2025-weekly-limits-more)
- [Serper Terms of Service](https://serper.dev/terms) · [Apify pricing](https://apify.com/pricing) · [Apify free tier 2026](https://use-apify.com/docs/what-is-apify/apify-free-plan) · [Preventing free-tier / multi-account abuse (detection methods)](https://payproglobal.com/how-to/prevent-free-trial-abuse/)
- [Proxycurl shutdown announcement (Nubela)](https://nubela.co/blog/goodbye-proxycurl/) · [StartupHub: Proxycurl shuts down](https://www.startuphub.ai/ai-news/startup-news/2025/the-1-linkedin-scraping-startup-proxycurl-shuts-down) · [ZoomInfo: Proxycurl review/shutdown](https://pipeline.zoominfo.com/sales/proxycurl-review)
- [LinkedIn scraping is dead — ToS-safe alternatives 2026 (DEV)](https://dev.to/zackrag/linkedin-scraping-is-dead-5-legal-tos-safe-alternatives-that-actually-work-in-2026-3f36)
- [Apollo People API Search docs](https://docs.apollo.io/reference/people-api-search) · [Apollo API pricing docs](https://docs.apollo.io/docs/api-pricing) · [Apollo pricing breakdown 2026 (Salesmotion)](https://salesmotion.io/blog/apollo-pricing) · [Apollo API guide 2026 (Built by Joey)](https://builtbyjoey.com/blog/apollo-api-lead-generation-guide/)
- [People Data Labs pricing & credits (Help Center)](https://support.peopledatalabs.com/hc/en-us/articles/25794271805211-Pricing-credits) · [PDL person pricing](https://www.peopledatalabs.com/pricing/person)
- [Serper pricing 2026 (Costbench)](https://costbench.com/software/web-scraping/serper/) · [Serper free plan limits](https://costbench.com/software/web-scraping/serper/free-plan/)
- [Hunter.io vs Snov.io 2026 (Woodpecker)](https://woodpecker.co/blog/snov-io-vs-hunter-io/) · [Hunter pricing 2026 (Growth Hack Suite)](https://growthhacksuite.com/hunter-io-pricing)
- [Best LinkedIn scrapers on Apify 2026](https://use-apify.com/docs/best-apify-actors/best-linkedin-scrapers) · [Apify LinkedIn Profile Search actor](https://apify.com/harvestapi/linkedin-profile-search)
- [Best people search APIs 2026 (Enrich)](https://www.enrich.so/blog/people-search-api) · [People search APIs for recruiting (Pin)](https://pin.com/blog/best-people-search-apis)
