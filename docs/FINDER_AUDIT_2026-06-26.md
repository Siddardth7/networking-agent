# Finder Audit — defect catalog (v0.5.5, issue #3)

**Date:** 2026-06-26 · **Scope:** `src/agents/finder.py`, `importer.py`,
`shared.py`, `src/providers/*` · **Method:** supervised audit — main reviewer +
independent `qa-expert` pass, every finding traced to a `file:line` and
cross-checked against the test suite to separate *mechanics-tested* from
*accuracy-tested*. This is the Finder's first defect audit (the Drafter has three;
the Finder had none — the gap Phase A closes).

**Headline:** the Finder *works* on the happy path (live-validated on Joby
2026-06-25), but it has **silent-failure paths in discovery**, **zero
accuracy assertions in classify**, and **hook regressions on imported/sparse
data**. 12 defects (2 high, 7 med, 3 low) + 9 untested branches.

## Severity summary & routing

| ID | Stage | Sev | One-line | Routes to |
|----|-------|-----|----------|-----------|
| D1 | discovery | **high** | Apify auth/other error swallowed when fallback runs clean-but-empty → silent empty discovery | #8 |
| D2 | classify/hook | **high** | `_ROLE_KEYWORDS`/`_UIUC_SIGNALS`/`_SHARED_EMPLOYERS` hardcoded → wrong hooks/classify off-aerospace | #8, #5, (B1) |
| D3 | classify | **high** | No test feeds a real title to the classifier and checks the output is *correct* | #4 |
| D4 | discovery | med | Apify `searchQuery` anchored to `role_keywords[0]` only → biases ranking to "quality engineer" | #8 |
| D5 | save | med | No `UNIQUE(company_id,linkedin_url)`; idempotency DELETE only clears `NEW` → duplicate rows on re-run | **new: #27** |
| D6 | hook | med | News-headline regexes false-positive on normal `hook_signal` phrasing ("reports to…", "…May 5") | #9 |
| D7 | hook | med | Imported contact with persona+focus+rich snippet skips classify → loses Tier-0 hook | #9 |
| D8 | discovery | med | Serper slug uses `.replace(" ","-")` vs `re.sub` elsewhere → slug mismatch on punctuated names | **new: #27** |
| D9 | email | med | Domain inferred as `slug.replace("-","")+".com"` → wrong domain kills email for whole batch silently | **new: #27** |
| D10 | email | low | Empty `EmailResult` labeled `source="apollo"` when Apollo was exhausted/never ran | **new: #27** |
| D11 | hook | low | Tier-2 shared-employer matches title only, not employment → current employees miss it | #9 |
| D12 | hook | low | Company-news query hardcodes `"2026"` → rots after year rollover | **new: #27** |

---

## HIGH

### D1 · Silent empty discovery on a bad Apify key — `finder.py:393–416`
**Symptom:** Apify raises `AuthError` (revoked/typo'd key); Serper runs and returns
`[]`; `find_contacts` returns `[]`, marks the company `FOUND`, logs nothing. A
misconfigured key is indistinguishable from "no contacts exist."
**Root cause:** `ran_clean` is set when *any* provider executes without raising
(line 407). `if ran_clean: return []` (410–411) fires **before**
`if other_exc is not None: raise other_exc` (414), so the stored `other_exc` is
dropped.
**Fix:** log `other_exc` at WARNING in the loop before `continue`; and when
`ran_clean` but every lane returned empty *and* an `other_exc` exists, surface it
(or at least a "discovery degraded — Apify errored" line) instead of a clean `[]`.
**Routes to #8** (the "best-effort-to-N, no silent caps" deliverable).

### D2 · Hardcoded biographical constants degrade classify + hook — `finder.py:29–51, 269–278, 666`
**Symptom:** Any non-Sid user gets "we share a UIUC background" hooks and
shared-employer hooks for employers they never worked at; even Sid gets wrong
Tier-1/2 hooks on a non-aerospace campaign. `_ROLE_KEYWORDS` (line 666) also
shapes Apify discovery toward aerospace titles regardless of the user.
**Root cause:** `_ROLE_KEYWORDS`, `_SHARED_EMPLOYERS`, `_UIUC_SIGNALS` are
module constants with no config path.
**Fix:** lift to `Config`/a `UserProfile`; `find_contacts`/`_generate_hook` accept
overrides. Full de-hardcoding is roadmap B1 (#future), but the **role-keyword**
override belongs in #8 now (it directly skews discovery), and the hook/persona
impact is part of the #5 classify story.
**Routes to #8 + #5; tracked for B1.**

### D3 · Classify is tested for shape, never for correctness — `tests/test_finder*.py`
**Symptom:** No test asserts that a real title → the right persona/focus. Fixtures
mock the client to emit a fixed `(persona, focus)`; the "classified correctly"
test only checks pass-through. The `SENIOR_MANAGER` enum deliberately conflates
managers and senior ICs (`finder.py:83–88`) with **no** test that the model
applies it as intended.
**Fix:** this *is* issue #4 — a labeled set + precision/recall. Add golden-file
regression (saved API responses) so accuracy is checked without live cost.
**Routes to #4** (and the fixes loop #5).

---

## MEDIUM

### D4 · Apify semantic query uses only the first keyword — `apify.py:152`
`search_query = f"{company} {role_keywords[0]}"` → the Actor ranks for "quality
engineer"; with 25 returned and `limit=5`, a composites/stress engineer can be
truncated out even though `currentJobTitles` (line 159) lists all keywords.
**Fix:** broaden `searchQuery` (e.g. join top-N keywords / `OR`). **→ #8.**

### D5 · Duplicate contact rows on re-run — `finder.py:655–659` + `migrations/001_initial_schema.sql`
Idempotency DELETE clears only `state='NEW'`; `contacts` has no
`UNIQUE(company_id, linkedin_url)` (the only `UNIQUE` is on the quota table,
line 68). Re-running after a contact is `SELECTED`/`DRAFTED` inserts a dup.
**Fix:** partial unique index on non-null `linkedin_url` + pre-insert skip in
`ingest_contacts`. **→ new issue #27.**

### D6 · News regex false-positives block real hooks — `finder.py:186–193, 266`
`_NEWS_MARKER_RE` (`\breports?\b`, `\bquarterly\b`, `\bcommitment to\b`,
`\bannounc\w+\b`) and `_NEWS_DATE_RE` ("May 5") were built for verbatim Serper
snippets but run on the 80-char LLM `hook_signal` via `is_acceptable_hook`. So
"reports to VP Structures" and "led 787 stress team since May 5" are silently
demoted to a weaker tier.
**Fix:** apply the full news check only to raw `company_news`; for `hook_signal`
keep just the `·` separator check (or require two co-occurring markers). **→ #9.**

### D7 · Imported contacts lose Tier-0 hooks — `finder.py:525–526, 535`
When a candidate has both `persona` and `focus_area` pre-set (every
`alumni_confirmed` row that also carries a focus, plus labeled imports), classify
is skipped → `hook_signal=None` → no Tier-0 even with a rich `snippet`. This is
the central failure mode for #9 (hook-on-imported).
**Fix:** when `snippet` is present and no `hook` supplied, run `_classify_contact`
for its `hook_signal` only (ignore its persona/focus). **→ #9.**

### D8 · Slug computed two different ways — `serper.py:156` vs `importer.py:144` / `apify.py:32`
Serper: `company.lower().replace(" ","-")`; the others: `re.sub(r"[^a-z0-9]+","-")`.
`"Joby Aviation, Inc."` → `joby-aviation,-inc.` vs `joby-aviation-inc` → contacts
cross-link to the wrong `companies` row.
**Fix:** extract one `slugify` to `src/core` and call it everywhere. **→ #27.**

### D9 · Wrong inferred email domain fails the whole batch silently — `finder.py:354`
`f"{company_slug.replace('-','')}.com"` → `general-electric` → `generalelectric.com`
(real: `ge.com`). When `companies.domain` is NULL, every email in the batch fails.
**Fix:** warn/raise when `domain` is NULL before enrichment; add a `--domain`
flag. **→ #27** (pairs with the #13 Hunter work).

---

## LOW

- **D10 · Mislabeled empty email sentinel — `finder.py:464–471`.** With
  `hunter_provider=None` and Apollo exhausted, returns `source="apollo"` as if
  Apollo ran. Add an `APOLLO_EXHAUSTED` branch. **→ #27.**
- **D11 · Tier-2 employer match is title-only — `finder.py:275–276`.** A current
  Boeing employee titled "Structures Engineer" never trips it. Also check
  `company_slug`. **→ #9.**
- **D12 · Hardcoded year in news query — `finder.py:324`.** `"… news 2026"`; use
  `datetime.now().year`. **→ #27.**

---

## Untested branches (the new 95% branch-coverage gate, #2, will expose these)

1. `_discover`: primary raises non-quota error, fallback clean-but-empty (D1 path).
2. `_resolve_email`: `hunter=None`, Apollo present but exhausted (D10 path).
3. `ingest_contacts`: explicit `persona` pre-set but `focus_area` None (forced-persona + classify-for-focus path).
4. `_generate_hook` Tier-0 rejection-then-fallthrough (an acceptable-length but news-blocked `hook_signal`).
5. `find_contacts` with Apify only, no Serper → `company_news=None` branch.
6. `_company_domain` "stored domain wins" branch (line 352).
7. `apify.py` Short-mode `currentPositions` array path (all tests use Full-mode shapes).
8. `import_contacts(draft=True, auto_select=True)` → drafter handoff.
9. `pdl.py` `_parse_person` work-email extraction (dormant module, see #22).

---

## Conclusion

- **Classify (#4/#5):** the biggest *quality* gap is D3 — there is no ground
  truth. The scorecard is the prerequisite for trusting any classify change.
- **Discovery (#8):** D1 (silent error) is the most dangerous defect for Sid's
  live campaign — a fat-fingered key looks like "no one works here." D4 + D2
  (keyword breadth/config) shape *who* gets found.
- **Hook (#9):** D6 + D7 make hooks *worse on exactly the imported data the
  flexible-input path was built for* — high-leverage, user-visible.
- **Correctness (#27):** D5/D8/D9/D10/D12 are small, well-scoped bugs; group them
  into one fix issue and clear them while we're in the Finder.

> Cannot verify without a live run: real LLM classification accuracy on diverse
> titles (that's #4), Apify `currentJobTitles` OR-semantics, and PDL free-tier
> email returns.
