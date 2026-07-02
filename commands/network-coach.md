---
description: The networking strategy, explained (#78) — why the agent works the way it does (alumni-first, one ask, short specific notes, capped follow-ups) and what to do at each stage, grounded in this repo's actual mechanics, not generic advice.
---

# /network-coach

Coach the user on the strategy this agent encodes. Answer whatever they asked;
for a general "how does this work / what should I do", walk the arc below.
**Every claim here names the real mechanism in this repo** — when coaching,
cite the mechanism, not folklore. Keep it conversational; this is a coaching
session, not a lecture.

## The one-line strategy

Five messages to the right people beat fifty generic ones. Referrals convert
roughly 10× better than cold applications, and the person most likely to help
is someone with a *reason* to: a shared school, a shared employer, or genuine
overlap with your work. Everything the agent does — ranking, hooks, one-ask
discipline, capped follow-ups — exists to earn a reply from those people
without ever sounding like a bot.

## Why alumni-first

The ranker's weights say it plainly: a confirmed shared-school contact scores
**40 points** (classified-alumni 25) vs recruiter 20 and generic
engineer/leader 5. Shared school is also the warmest deterministic hook
(Tier 1: "we share a {school} background"). Alumni tolerate a direct company
question, so they get the most useful asks — and when several alumni at one
company are drafted together, **ask rotation** assigns each a DIFFERENT
question (hiring climate / sponsorship / culture / their transition / who to
talk to), so a handful of short replies together paint the full picture no
single contact would volunteer.

## Why the notes look the way they do

- **4-part spine** (Intro → Source → Hook → Close), always in that order;
  channel length decides how many parts survive.
- **Specificity gate**: a hook must name one real, verifiable detail from
  THEIR profile or be omitted. Generic admiration reads as filler; the critic
  holds drafts with known AI tells, and the guardrail hard-fails placeholders
  and invented facts. When the agent had no specific signal, the note is
  deliberately shorter — that's the feature, not a failure.
- **Hook ladder**: a specific snippet detail beats shared school beats shared
  employer beats title specialty beats "your work as {title}". The agent never
  pastes company news as a hook.
- **280-character LinkedIn cap** (hard-enforced): the note earns the reply;
  it never asks for a call. The full message and the one ask come
  post-connection.
- **ONE ask, ever.** A second ask measurably kills replies; the guardrail
  detects multi-asks and forces a rewrite.
- **Facts come only from the resume library** (with provenance): coursework
  is never dressed up as employment, and no metric appears that the user
  didn't supply. This is why drafts survive a skeptical reader.

## Cadence and timing

- **Follow-ups**: at most **2**, spaced ~**5 days** (4–7 window), and only
  for approved, sent outreach that got no reply. Past the cap, stop — a third
  nudge converts nearly nothing and burns the bridge.
- **Send windows**: Tue/Wed/Thu ~09:00 in the *contact's* timezone
  (`/network-timing` computes it from their location). Monday triage and
  Friday afternoons eat messages.
- **The human sends.** The agent never touches LinkedIn; you review
  (`/network-approve`) and you press send. A draft marked CRITIC_HOLD or ⚠
  is the agent telling you to read it extra carefully, not a hard no.

## When they reply: the next move

Record every outcome (`/network-outcome <id> <OUTCOME>`) — it feeds
reporting, follow-up gating, and Application-mode status. Then let
`/network-nextmove-here` classify and draft the reply. The precedence it
encodes (issue #19):

| Their reply contains | Next move | Why |
|---|---|---|
| An intro offer / a point of contact | `THANK_INTRO` | A concrete handoff beats everything — take it same-day. |
| Visa / sponsorship mention | `SPONSORSHIP_QUESTION` | They opened the door; ask the precise question (STEM OPT / H-1B) now, not at the offer stage. |
| Mentions hiring / open roles | `REFERRAL_ASK` | They signaled the req exists — this is the moment the whole thread was building toward. |
| Warm but unspecific | `SCHEDULE_CALL` | Default: propose a *brief* call; the call is where referrals actually happen. |

Two rules the drafts already obey — keep obeying them live: thank before
asking, and never stack a second ask onto a reply.

## Which front door

- **Campaign mode** (`/network-run <slug>`) — "I want to work at this
  company": builds a bench of the right people there.
- **Application mode** (`/network-jobs <feed.json>`) — "I applied to this
  specific req": finds the role's team per posting, links them to the
  `job_id`, and answers "do I have a referral for this req yet?"
  (`--status`). If you're applying to specific postings, Application mode is
  the sharper tool; the two share one engine and dedupe against each other.

## Explaining the agent's choices (any time)

When the user asks "why this person / why this draft", the data is already
there: `rank_reasons` on each contact (the per-signal score breakdown), the
hook text (which tier fired), and the assigned ask angle. Read them back in
plain language — that's the whole coaching trick.
