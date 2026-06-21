# Brief for Claude Cowork — design the Chrome-based contact producer

**You are Claude Cowork, with access to this project folder and the Claude-in-
Chrome extension.** This doc gives you the full context and a set of questions.
**We are NOT asking you to build anything yet** — we want you to read the
relevant files, honestly assess what you and Claude-in-Chrome can and can't do,
and then *design your own workflow* and answer the questions at the end. Your
answers get pasted back to the engineering side, which will wire your output into
the plugin. Be candid about limitations — an honest "I can't reliably do X" is
more useful than an optimistic guess.

---

## 1. What this project is

A Claude Code **plugin** called `networking-agent`: a 3-agent outreach pipeline
(**Finder → Drafter → Marketer**) that finds contacts at a target company,
drafts personalized outreach in the user's voice (a validated 4-part model with
per-persona logic and "ask-rotation" so same-company contacts get different
questions), runs quality gates, and produces an approval artifact. The user
(Sid) is an MS Aerospace Engineering candidate at UIUC (Dec 2025) doing a job
search.

Key files to skim:
- `docs/LEAD_SOURCING_RESEARCH_2026-06-21.md` — the sourcing strategy + why
  (esp. §10 the 30-day campaign and §11 the $0 plan). **Read §10–11 first.**
- `docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md` — the input architecture; **§2 is
  the canonical JSON contract you must emit**, §6 describes your role.
- `commands/network-import.md` — the command that consumes your output.
- `src/agents/importer.py` — `parse_contacts_file()`, `validate_contacts_file()`,
  `import_contacts()`. `validate_contacts_file()` is the contract checker.

## 2. How the user plans to use the agent

- **One company + one location per day** (e.g. "Joby Aviation, Dayton OH"),
  ~30 companies over ~30 days, finishing a target list.
- **Primary goals per company:** (1) establish **one POC / referral contact**,
  (2) get **sponsorship intel** (does the company sponsor international students
  on STEM-OPT / H-1B). Everything else is a bonus.
- **Outreach reality:** LinkedIn caps connection invites at ~100/week (~20–25/day,
  all tiers). So we do NOT want 50 random leads — we want a **small,
  alumni-weighted, location-matched** set per company (~12–25), because:
  - **Alumni** (fellow UIUC) reply 60–80% and tolerate the sponsorship ask →
    highest priority persona.
  - The agent already varies the ask across same-company alumni (sponsorship /
    who-to-talk-to / culture / transition / hiring), so a handful of good alumni
    directly produce both goals.
- Persona priority for sourcing: **ALUMNI > RECRUITER > PEER**.

## 3. Where YOU (Cowork + Chrome) fit

Today, sourcing the right contacts is the manual step: searching LinkedIn (the
**Alumni tool** + People search) filtered by company + location + role, and
copying name / title / profile-URL. **Your job is to automate that producer
step.** You gather contacts and emit them as **canonical JSON**, then hand off to
the plugin, which does everything downstream (classify → voice draft →
ask-rotation → quality gate → approval). You do NOT draft or send — you *source*.

The handoff is a file + one command:
```
# you write contacts.json, then:
python -m src.cli.network_import contacts.json --company "Joby Aviation" --draft
# (validate first, writes nothing:)
python -m src.cli.network_import contacts.json --validate
```

## 4. The contract you must emit (canonical JSON)

Only `full_name` is strictly required. `title`, `linkedin_url`, and a company are
strongly recommended (company can be per-record or passed via `--company`).
Everything else is optional and *honored when present, generated when absent*.

```json
{
  "company": "Joby Aviation",
  "location": "Dayton, OH",
  "source": "chrome",
  "contacts": [
    {
      "full_name": "Jane Doe",
      "title": "Structures Engineer",
      "linkedin_url": "https://www.linkedin.com/in/janedoe",
      "location": "Dayton, OH",
      "about": "UIUC AE '22 — composite structures…",   // headline/About text; grounds the hook
      "email": "jane@joby.aero",                          // optional; usually omit (LinkedIn-only)
      "persona": "ALUMNI",                                // optional; ONLY if you're confident
      "focus_area": "STRUCTURAL_ANALYSIS"                 // optional; ONLY if you're confident
    }
  ]
}
```
- Personas the agent uses: `RECRUITER`, `SENIOR_MANAGER`, `PEER_ENGINEER`,
  `ALUMNI`. Focus areas: `COMPOSITE_DESIGN`, `STRUCTURAL_ANALYSIS`,
  `MANUFACTURING`, `MATERIALS`, `ADDITIVE`, `PEER`, `ALUMNI_ACADEMIC`.
  If unsure, **omit** persona/focus_area — the agent classifies them itself.
- Run `--validate` before handing off; it reports missing-company errors and
  "no channel / no title" warnings without writing anything.

---

## 5. Questions for you (answer these)

### A. Capabilities & honest limits
1. Can Claude-in-Chrome operate inside the user's already-authenticated LinkedIn
   session, and are you comfortable doing so (ToS, account-safety)? What
   safeguards would you apply to protect the user's job-search account?
2. Can you drive the **LinkedIn Alumni tool** (a school's People tab, filtered by
   "where they work" + "what they do")? And regular **People search** with
   company + location + title/keyword filters? Which is more reliable for you?
3. Per result, which fields can you reliably extract — full name, title/headline,
   profile URL, location, About text? Do you need to open each profile to get
   About/headline, or can you read it from the results list?
4. Realistically, how many profiles can you collect per company session before
   hitting LinkedIn's search/commercial-use limits or throttling? How would you
   pace to stay safe (the free search cap is ~250–350 searches/month)?

### B. Workflow you'd design
5. Given `company + location (+ school = UIUC)`, write your **step-by-step
   capture flow** — how you'd find alumni first, then a recruiter, then peers,
   apply the location filter, paginate, dedup, and assemble the JSON.
6. How do you handle failures/partial results (a search returns nothing, a page
   won't load, you're rate-limited mid-run)? Can you resume?
7. How would you tag persona when confident (e.g. the Alumni tool guarantees
   ALUMNI; a "Recruiter/Talent" title ⇒ RECRUITER), and when would you leave it
   blank for the agent to classify?

### C. Orchestration & scheduling
8. Can you **schedule one company/day** from a target list, and track which are
   done across days? How should the target list (companies + locations + school)
   be given to you — a file in this folder, a Cowork table, something else?
9. After producing `contacts.json`, should you **call the import command
   yourself** (`python -m src.cli.network_import … --draft`) or just drop the
   file for the user to run? Which do you prefer and why?
10. Where should outputs live (suggest a path/naming, e.g.
    `runs/<date>-<company>.json`) so the engineering side can wire it cleanly?

### D. Fit & fallbacks
11. What parts of this are you **confident** about vs. **risky/unreliable**?
12. Would you recommend a different division of labor (e.g. semi-manual: you
    surface candidates, the user confirms before export)? 
13. Anything you can reliably add that we didn't ask for (mutual connections,
    current-vs-past employee, alumni-confirmed flag, headline) — or anything we
    asked for that you **cannot** reliably get (e.g. email)?
14. Any change to the canonical JSON (§4) that would make your output cleaner or
    more reliable? Propose concrete field additions/renames if so.

---

## 6. What to hand back

Please return: (a) your answers to §5, (b) a concrete **workflow description**
you're confident you can execute, (c) a **sample `contacts.json`** for one real
company+location produced by your flow (even 5–10 contacts), and (d) the exact
**commands/steps** you'd run for a daily cycle. The engineering side will use
(b)–(d) to wire scheduling + the handoff into the plugin.
