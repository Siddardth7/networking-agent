# Market gap & feature ideas (research)

**Date:** 2026-06-21 · **Status:** Research input feeding `docs/ROADMAP.md`.
Grounds the product thesis and the post-roadmap feature ideas in 2026 market data.

---

## 1. The market reality (and a sharpened thesis)

**The honest version of the pitch is stronger than "ATS robots reject you."**
- The "75% of resumes auto-rejected by ATS" figure is largely myth — a 2025 study
  found **92% of ATS don't auto-reject on resume content**; knockout questions
  (work auth, min experience) are employer-set, not the algorithm.
- The real enemy is **volume + an AI-application arms race**: applications surged
  **45.5%** while postings **dropped 10.6%**; popular roles draw **200–400
  applications in 48h**; and **33.5% of recruiters spot AI applications in ~20
  seconds, 19.6% reject them.** SHRM's 2026 read: "recruitment is broken,"
  mutually-assured-destruction between auto-appliers and auto-screeners.
- So the durable thesis: **the funnel is flooded with AI noise; a warm referral
  is the only reliable way to get a human to actually look.**

**The referral math (the whole reason this product exists):**
- **28.5% hire rate for referrals vs 2.7% for non-referrals (~10×).** Referrals
  are ~4× more likely to get an offer; cold applications convert at 0.1–2%.
- Referred hires also retain better (45% >4yr vs 25% from job boards).

**Why generic AI outreach fails (and what works):**
- The market is "saturated with AI-generated noise"; buyers have a "trust
  filter." Mass automation and generic text — the 2024 playbook — is now
  obsolete and actively counterproductive.
- **Personalized notes: 9.36% reply rate, 72% higher than generic;** 20–30%
  connect rate vs blank. **"5 personalized messages to the right people beat 50
  generic ones"** — targeting precision > volume.
- **Follow-ups matter:** 2–3 follow-ups spaced 4–7 days hit **20–30%+** vs
  single-touch. **Timing:** Tue–Thu mornings in the recipient's local time =
  **+8%.** Relevance to the recipient's role = +15%.

## 2. Competitive gap (where we win)

Existing tools cluster into two **failing** camps:
- **Auto-appliers** (Loopcv, Sonara, JobWizard autofill) — the backlash tools
  flooding the funnel; detected and penalized.
- **Shallow "one-click referral note" generators** (JobWizard, JobRefer.ai) —
  produce exactly the generic spam the trust filter now rejects.
- **Sales-outreach machines** (Overloop, Heyreach, Sendr) — volume + multi-account
  rotation; built for sales, not job-seekers, and on the wrong side of the trend.

**Nobody does deep-personalized, end-to-end, anti-spam *networking* well.** Our
voice engine + ask-rotation + quality gate is precisely counter-cyclical to where
the market is failing. The opportunity is the *quality / human-feel / right-person*
lane, not the volume lane.

## 3. Feature ideas (ranked by leverage) → mapped to the roadmap

**Tier 1 — serve the proven thesis directly (Phase A):**
1. **Warm-path / referral-likelihood ranking** — rank captured contacts by who's
   actually likely to help (alumni, 1st/2nd-degree, recent joiners, posts-about-
   hiring, team-matches-target-role, recruiter-for-req). Operationalizes "5 to the
   right people." → **v0.6.5** (targeting intelligence on the Finder).
2. **Follow-up sequencing** — timed, non-spammy multi-touch (no reply →
   value-add follow-up at 4–7 days, capped). → **v0.8.0** (+ timing intelligence).
3. **Conversation continuation ("they replied — now what?")** — draft the next
   move: the referral ask, the sponsorship question, scheduling the chat. The
   hardest moment for the target audience. → **v0.8.5**.

**Tier 2 — differentiators / audience fit:**
4. **Coaching layer** — explain the strategy as it works (why alumni-first, why
   one ask, what to say on reply). Tool → coach for people who don't know how to
   network. → **Phase B (v0.9.5, with onboarding).**
5. **Timing intelligence** — optimal send window per contact's local timezone
   (location is already captured). +8%. → folded into **v0.8.0**.
6. **Anti-AI-detection moat** — extend the humanizer + a critic dimension ("would
   a recruiter flag this as AI in 20s?"). Make "the un-generic networking tool"
   an explicit strength. → **cross-cutting** quality thread (woven into Finder/
   Drafter quality releases).
7. **Role/req targeting + early-applicant combo** — network *toward* a specific
   opening, time it to the early-applicant window ("warm intro AND apply early").
   → **Phase B candidate** (touches job-data integration).

**Tier 3 — strategic:**
8. **Non-dev distribution system** — a Claude Code CLI plugin reaches developers,
   not the broad job-seeker audience (who use ChatGPT *because it's a chat box*).
   Reaching them needs a more accessible surface. **Kept as a separate track to
   design (see ROADMAP "Distribution track") — NOT on the 0.x dev ladder.** The
   dev/CLI path stays the low-effort, clean v1.0 line.
9. **Multi-path to a human** — mutual-connection intro asks, alumni email, beyond
   LinkedIn invites — diversify past the weekly invite cap. → opportunistic.

## Sources
- [Employee referral statistics 2026 (Zippia)](https://www.zippia.com/advice/employee-referral-statistics/) · [Job application success rates 2026](https://www.careerhelp.top/blog/job-application-success-rate-statistics-2026)
- [ATS "75%" is largely myth (ResumeAdapter)](https://www.resumeadapter.com/ats-statistics)
- [AI is breaking the job search — 45.5% app surge (Greenville Business Mag/Stacker)](https://www.greenvillebusinessmag.com/premium/stacker/stories/the-bot-applicant-how-ai-is-breaking-the-modern-job-search,34950) · [AI job-application tools hurt your search (jobstrack)](https://jobstrack.io/blog/ai-job-application-tools)
- [LinkedIn cold-messaging data-backed rules 2026](https://bestjobsearchapps.com/articles/en/linkedin-cold-messaging-etiquette-7-databacked-rules-to-boost-job-search-responses-in-2026)
- [Best AI LinkedIn outreach tools 2026 (Overloop)](https://overloop.com/blog/8-best-ai-linkedin-outreach-tools) · [AI tools for LinkedIn job search 2026 (Jobright)](https://jobright.ai/blog/ai-tools-for-linkedin-job-search/)
