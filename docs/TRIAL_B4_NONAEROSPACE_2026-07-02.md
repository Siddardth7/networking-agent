# B4 live trial — non-aerospace, non-UIUC profile (issue #84)

**Date:** 2026-07-02 · **Verdict: PASS** (1 defect found → fixed in this PR)

The Phase-B exit-gate proof: the agent, end to end, for someone who is NOT
the original user. Synthetic persona **"Alex Rivera"** — MS CS at Georgia
Tech, distributed systems, ex-Stripe intern — chosen for the field (backend
SWE), everything else realistic. Live discovery (real Serper credits),
host-token classify/draft/critic (the operator acted as the subagents),
isolated `HOME` (temp config + DB; real state never touched). Zero Anthropic
spend.

## Setup (the `/network-setup` flow, via the #76 bridge)

`write profile` / `write voice` / `write resume` all validated clean:
profile taxonomy `BACKEND / INFRA (+ PEER, ALUMNI_ACADEMIC)`, Georgia Tech
school signals, `[stripe, datadog]` employers; voice doc voiced as Alex;
2-project resume library (INTERNSHIP + COURSEWORK provenance). `status`
reflected all files.

## Live pipeline (target: cloudflare)

| Stage | Result |
|---|---|
| `discover cloudflare --limit 3 --keywords "backend engineer,distributed systems"` (live Serper) | 3/3 real distributed-systems/backend engineers; classify contexts carried the CUSTOM taxonomy (no aerospace areas). |
| Classify (host judgment) → `ingest --target-focus BACKEND` | 3/3 ingested as PEER_ENGINEER/BACKEND; Tier-0 hooks from real snippets ("prior Senior Backend Engineer at Agora", "8+ years specializing in distributed systems"). |
| Rank | All 17 pts incl. **"focus matches target role" (+10)** — the #61 resolver→ranker wiring firing on LIVE data for the first time. |
| Draft (host, LINKEDIN_CONNECTION) | 264 chars, 4-part, grounded in the hook only, one ask → gate **OK** first pass. |
| Critic (host scores on the real rubric) | **OK**, trace persisted, contact → DRAFTED. |

## Defect found (the reason live trials exist)

**Aerospace persona-template leakage.** The first draft context contained
"Siddardth (Sid) Pathipaka, MS Aerospace Engineering candidate at UIUC" in
`persona_template` while `voice_doc` said Alex Rivera — conflicting
identities in one prompt. Root cause: the built-in persona templates are the
DEFAULT profile's voice (#61, by design), but the `/network-setup` wizard
(#77) never created custom templates or set `templates_dir`, so every
non-aerospace wizard user would hit this.

**Fix (this PR):** wizard step **3.5** — adapt the four persona templates
(keep the strategy skeleton, swap identity/industry framing), write to
`~/.networking-agent/templates/`, set `templates_dir` in profile.yaml.
Applied live in the trial: leakage gone (`aerospace/UIUC/Sid anywhere:
False`), draft proceeded voiced correctly as Alex.

## Trial notes

- The `network_check` D9 domain-inference warning fired correctly
  ("inferring cloudflare.com").
- Critic `apply` with wrong rubric keys silently passed on defaults
  (missing dims → 3). Not a live-path risk (the critic subagent gets the
  rubric in its context, and the hold rule is deliberately
  default-forgiving) — noted, not filed.
- Costs: 1–2 Serper credits total (discovery ran twice: once for the
  payload, once re-reading output).

## Exit criteria (issue #84)

✅ Custom taxonomy in classify options · ✅ custom hooks (Tier-0 live; Tier-1/2
verified in the #77 E2E) · ✅ identity/voice correct in drafts (after fix) ·
✅ no aerospace leakage anywhere in the final draft context · ✅ live evidence
throughout · ✅ defect filed-and-fixed in the same PR.
