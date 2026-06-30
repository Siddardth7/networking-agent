---
description: Draft the reply-aware next move for a contact who replied (take the intro, ask about sponsorship, propose a chat, or ask for a referral) — in voice, gated.
---

# /network-nextmove

The hardest moment: **they replied — now what?** Paste their reply and this
drafts the single best next move toward a warm, useful connection, in your
voice, through the same hard_check + critic gates as a cold draft.

## Usage

```
/network-nextmove <contact-id> "<their reply, verbatim>"
/network-nextmove <contact-id> "<reply>" --move SCHEDULE_CALL
/network-nextmove <contact-id> "<reply>" --channel COLD_EMAIL --outcome POC
```

**Example:**
```
/network-nextmove 42 "Thanks for reaching out — happy to chat. What did you want to discuss?"
→ Next move: SCHEDULE_CALL  [OK]
  Subject: ...
  ---
  <a short, in-voice message proposing a 15-minute call>
```

## The next move

Classified from the reply text (and any recorded outcome), in goal-advancing
precedence — override with `--move` when you read the reply differently:

| Move                   | Fires when…                                              |
|------------------------|---------------------------------------------------------|
| `THANK_INTRO`          | they offered an intro / point of contact (or outcome=POC) |
| `SPONSORSHIP_QUESTION` | they raised work authorization / visa / sponsorship      |
| `SCHEDULE_CALL`        | they're open to talking — **also the default warm reply** |
| `REFERRAL_ASK`         | they mentioned hiring / open roles                       |

## Gating

The draft runs the same gates as a cold message: humanizer (AI-tell stripping),
`hard_check` (placeholder leak + length), and the critic (AI-tells, specificity).
The printed `[QUALITY_CODE]` is `OK`, `HARD_FAIL`, or `CRITIC_HOLD` — review
anything that isn't `OK` before sending. The next move makes no new achievement
claims, so the numeric-fabrication check is not applicable.

## Channel

Defaults to email when the contact has an address, else the post-connection
LinkedIn thread. Force it with `--channel`.

## Exit Codes

| Code | Meaning                                        |
|------|------------------------------------------------|
| 0    | Success                                        |
| 1    | Empty reply, invalid `--move`/`--channel`, or unknown contact |

## Implementation

- CLI: `src/cli/network_nextmove.py` → `run_nextmove(args)`
- Core: `src/agents/drafter.py` → `classify_next_move` (pure) + `draft_next_move`
- DB read: `contacts`, `companies`
