# Definition of Done — mandatory gates for every issue & version

**Status:** canonical · **Adopted:** 2026-06-25 · **Tracker mirror:** [issue #1](https://github.com/Siddardth7/networking-agent/issues/1) (pinned)

This is the single source of truth for the process every roadmap issue follows.
Issue bodies link here instead of repeating the gates. Start any new issue by
reading **this doc + that issue's body** — nothing else is required for context.

---

## Per-issue gates (run in order — do not skip)

1. **Implement under ponytail** (full mode = the standing senior-dev reflex).
   The laziest solution that actually works: reuse what's already in the repo
   before writing new code; stdlib / native platform features before a new
   dependency; the shortest *correct* diff. Mark every deliberate shortcut with a
   `# ponytail:` comment naming the ceiling and the upgrade path. Non-trivial
   logic (a branch, loop, parser, money/security path) leaves **one runnable
   check** behind.

2. **Dedicated tester.** Spawn the **`test-automator`** agent to write/extend the
   `pytest` suite for the change. For complex or cross-module issues — and **all
   `audit` issues** — **`qa-expert`** supervises the test strategy.

3. **Learning loop (coverage gate) — the hard stop.** Loop
   *write test → run `pytest --cov=src --cov-branch` → fill gaps* until the
   touched modules hit **≥ 95% line AND branch** coverage and the repo-wide gate
   has not regressed. **Do not start the next issue (or close the version) until
   this is met.** Accuracy-bearing issues (classify, ranking) run a parallel
   accuracy loop against their scorecard, not just line/branch coverage.

4. **`/code-review`** on the diff. Resolve every correctness finding.

5. **`/ponytail:ponytail-review`** on the diff — the supervising senior-dev pass
   for over-engineering / bloat. Delete anything speculative.

6. **Green + clean + logged.** Full suite green, `ruff check` clean,
   `CHANGELOG.md` updated.

## Per-version gates (before any tag)

- [ ] Every issue in the version milestone closed.
- [ ] Repo-wide `pytest --cov=src --cov-branch` **≥ 95% line + branch**; ratchet
      `fail_under` in `pyproject.toml` up to the new floor (**never down**).
- [ ] Full **`/code-review`** of the version diff.
- [ ] **`/ponytail:ponytail-audit`** (whole-repo over-engineering scan) +
      **`/ponytail:ponytail-debt`** (review the `ponytail:` shortcuts harvested
      this cycle — pay down or consciously keep).
- [ ] Live validation per [`ROADMAP.md`](ROADMAP.md) where the version calls for it.
- [ ] Tag + push (**Sid** runs the tag/push).

## Sizing legend (used in every issue + as labels)

| Field | Values |
|---|---|
| **Size** | `S` ~<0.5d · `M` ~0.5–1.5d · `L` ~2–4d · `XL` ~1wk+ |
| **Complexity** | `low` (mechanical) · `med` (design / unknowns) · `high` (research / accuracy / cross-module) |
| **Priority** | `P0` (blocker) · `P1` (core to the version) · `P2` (when free) |

## Bootstrap exception

This doc itself is a pure-markdown contract — no logic — so gates 2–5 are N/A for
it. The first issue that the tooling gate (#2, branch coverage) and the coverage
baseline (#21) depend on are where the gates become live.

---

> **Why this exists:** every issue must carry `/code-review`, a coverage-gated
> learning loop, a dedicated tester, and ponytail as the supervising senior dev.
> They live here once; issues reference this doc.
