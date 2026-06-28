# Ranking validation scorecard (2026-06-28, v0.6.5)

Validates the referral-likelihood ranker (#11, `src/agents/ranker.py`) against a
gold-tiered labeled set (`src/eval/rank_labeled_set.json`, n=14). Acceptance
(#12): **top-ranked = highest help-likelihood, documented.**

The ranker is deterministic and offline, so this is a pure, fully-covered eval
(`src/eval/rank_scorecard.py`, 100% line+branch) — no API key, runs in CI.
Regenerate with `python -m src.eval.rank_scorecard`.

## Method

Ranking quality is about *order*, not classification accuracy, so the metrics are:

- **Pairwise concordance** — over every pair whose gold tier differs, the
  fraction the ranker orders the same way (higher tier → higher score). The core
  "does the order agree with human judgment" number.
- **Tier inversions** — a strictly worse target (lower gold tier) scoring *above*
  a better one. The real failures.
- **Top-K precision** — of the top K ranked (K = number of HIGH-tier contacts),
  how many are actually HIGH. This is the literal acceptance bar.

Gold tiers encode Sid's referral priority (alumni / 1st-2nd degree / recruiters
posting reqs / hiring posts > generic). The set is built with clear signal
separation (HIGH = multiple strong signals, LOW = none) so the validation tests
the model's *intent*, not knife-edge weight tuning.

## Result

```
## Ranking validation scorecard (n=14)

- Verdict: PASS
- Pairwise concordance: 100% (65/65 tier-differing pairs ordered correctly)
- Top-5 precision: 100% (5/5 top-ranked are HIGH tier)
- Tier inversions (worse target ranked above better): 0
```

| tier | score range |
|---|---|
| HIGH | 37–80 |
| MED  | 22–27 |
| LOW  | 2–10 |

Ranked order (best first): Priya Alumna-Connected (80, confirmed alumna +
1st-degree + email) → Owen Alum-2nd (62) → Dana Recent-Joiner (50) → Sofia
Alumna (47) → Marcus Hiring-Recruiter (37) | then MED: Raj (27), Nina (25),
Elena (25), Tom (22) | then LOW: Iris (10), Leo/Hana/Sam (7), Pat No-Signal (2).

## Verdict

**PASS — top-ranked = highest help-likelihood.** Every HIGH-tier contact ranks
above every MED, every MED above every LOW; the five strongest-signal contacts
occupy the top five slots; zero inversions; 100% pairwise concordance. The score
bands are cleanly separated (HIGH ≥ 37, MED 22–27, LOW ≤ 10), so the ordering is
robust to small weight changes, not balanced on a knife-edge.

## Notes / tuning candidates (none blocking)

1. **Weights are a v1 heuristic** (`ranker._WEIGHTS`). The scorecard confirms the
   *ordering* is correct on cleanly-separated cases; it does not claim the exact
   point values are optimal. When real outcome data exists (#15 outcome model),
   re-fit weights against who actually replied/referred and re-run this card.
2. **`target_focus` (team-match) signal is unscored here** — it needs a campaign
   target focus, still unwired in the live path (#11 deferral). Add labeled cases
   + the signal weight to this set when that config lands.
3. **Within-tier order is not asserted** (e.g. MED's Raj 27 vs Nina 25) — only
   cross-tier order is graded, which is what "right people on top" requires.
