# Cost Breakdown — Networking Agent

All AI calls use `claude-haiku-4-5-20251001`. Third-party API costs use free-tier pricing as the baseline.

---

## Monthly Cost Summary

| Companies/month | Contacts/company | Est. Claude cost | Serper cost | Hunter cost | Total |
|---|---|---|---|---|---|
| 10 | 5 | ~$0.10 | ~$0.05 | ~$0.10 | ~$0.25 |
| 25 | 5 | ~$0.25 | ~$0.10 | ~$0.25 | ~$0.60 |
| 50 | 5 | ~$0.50 | ~$0.20 | ~$0.50 | ~$1.20 |

**Per-run estimate: $0.10–0.30 per company** (varies with contact count and revision loops).

---

## Per-Stage Breakdown

### Stage 1: FIND (`/network-find`)

**What happens:** Finder agent issues 1–3 Serper searches per company, scrapes result snippets, then calls Claude to classify and score each candidate contact.

| Item | Cost |
|---|---|
| Serper searches (1–3 per company) | ~$0.001–0.003 (free tier: 100/month) |
| Claude classification calls (1 call per batch of candidates) | ~$0.002–0.005 |
| **Stage total** | **~$0.003–0.008** |

Serper free tier covers ~33 companies/month at 3 searches each. Upgrade to Serper Starter ($50/month, 2 500 searches) if you run more.

---

### Stage 2: SELECT (automatic, no API calls)

Contact ranking and selection is done in-process using scores from Stage 1. No API calls are made. Cost: **$0.00**.

---

### Stage 3: DRAFT (`/network-draft`)

**What happens:** Drafter agent generates one LinkedIn message and one cold email per selected contact. Each draft call includes your resume bullets, the contact's role context, and your voice.md.

| Item | Cost per contact |
|---|---|
| Hunter email verification (1 lookup per contact) | ~$0.004 (free tier: 25/month) |
| Claude draft generation (1 call per contact, ~1 500 tokens in/out) | ~$0.008–0.015 |
| **Stage total per contact** | **~$0.012–0.019** |
| **Stage total for 5 contacts** | **~$0.06–0.10** |

Hunter free tier covers 25 verifications/month. If you skip email and target LinkedIn-only contacts, Hunter calls drop to zero.

---

### Stage 4: APPROVE (`/network-approve`)

**What happens:** You review each draft. If you request a revision, Claude rewrites the message. Each revision is one additional Claude call (~800 tokens in/out).

| Item | Cost |
|---|---|
| Zero revisions | $0.00 |
| 1 revision per contact (5 contacts) | ~$0.02–0.04 |
| 2 revisions per contact (5 contacts) | ~$0.04–0.08 |

Revision loops are the primary source of cost variability. Accepting drafts on first review keeps this stage near zero.

---

### Stage 5: ARTIFACT (automatic)

**What happens:** Artifact Writer assembles the approved messages into a markdown outreach file. No API calls. Cost: **$0.00**.

---

## Total Per-Run Estimate

| Scenario | Cost |
|---|---|
| 5 contacts, zero revisions | ~$0.07–0.11 |
| 5 contacts, 1 revision each | ~$0.09–0.15 |
| 5 contacts, 2 revisions each | ~$0.11–0.19 |
| 3 contacts, zero revisions | ~$0.04–0.07 |

---

## Tips for Keeping Costs Low

1. **Use `/network-dry-run` before your first real run.** It simulates the full pipeline without making any API calls, so you can verify config and flow at zero cost.

2. **Reduce `finder_limit` in config.yaml.** The default is 5 contacts per company. Set it to 3 to cut draft and Hunter costs by 40%.

3. **Accept drafts on first review.** Each revision loop adds ~$0.004–0.008 per contact. Writing a thorough `voice.md` upfront reduces the need for revisions.

4. **Skip email for contacts without a clear email pattern.** If Hunter can't verify an address, the plugin skips the lookup automatically. LinkedIn-only outreach avoids Hunter costs entirely.

5. **Use `/network-find` + `/network-draft` separately.** Running find first lets you manually review and deselect contacts before drafting, so you don't spend draft credits on contacts you'd reject anyway.

6. **Monitor quotas with `/network-providers`.** Check remaining Serper and Hunter credits before a batch run to avoid mid-run failures.

---

## Free Tier Limits

| Provider | Free tier | Resets |
|---|---|---|
| Serper | 100 searches/month | Monthly |
| Hunter | 25 verifications/month | Monthly |
| Anthropic | Pay-per-use, no free tier | N/A |

At 5 contacts and 3 Serper searches per company, Serper free tier covers ~33 companies/month. Hunter free tier covers exactly 25 email verifications — one per contact if you run 5 companies at 5 contacts each.

---

## Model Reference

All Claude calls in this plugin use `claude-haiku-4-5-20251001`:

- Finder agent: contact classification and scoring
- Drafter agent: LinkedIn message and cold email generation
- Approval loop: revision rewrites

Haiku pricing (as of 2026): $0.80 / 1M input tokens, $4.00 / 1M output tokens. Actual costs will vary slightly with Anthropic pricing changes — check [anthropic.com/pricing](https://www.anthropic.com/pricing) for current rates.
