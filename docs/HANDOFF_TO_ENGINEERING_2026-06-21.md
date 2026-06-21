# Handoff → Claude Code (engineering): wire in the Cowork+Chrome producer

Paste this into the engineering Claude Code session.

---

You designed the flexible import path; the Cowork+Chrome "producer" side has now
been assessed. Read these two new files first, then do the wiring below.

**Read:**
- `docs/COWORK_CHROME_PRODUCER_RESPONSE_2026-06-21.md` — the producer's full
  capability assessment + workflow + daily commands (answers §5 of the brief).
- `docs/chrome-capture.example.json` — a sample canonical capture file; it
  validates clean against `validate_contacts_file` (file-level `company` +
  `location`; every contact has `full_name` + `title` + `linkedin_url`).

**Context (already settled by the producer assessment):**
- The producer is **read-only and human-paced** against LinkedIn (account-safety
  on Sid's live job-search account is the binding constraint). It captures
  ~12–25 contacts/company from results lists — alumni-first, one recruiter, a few
  unlabeled peers — and emits canonical JSON. It does **not** send.
- Per-contact it reliably supplies `full_name`, `title`, `linkedin_url`,
  `location`, headline-as-`about`, and `persona` only when certain (`ALUMNI` from
  the Alumni tool, `RECRUITER` from title). It **omits** email and `focus_area`
  (classifier's job). This matches the existing contract — **no schema change is
  required for the producer to work today.**
- Daily cycle: write `runs/<YYYY-MM-DD>-<slug>.json` → `--validate` →
  human confirm → `--draft`. Send stays gated by the marketer approval artifact.

**Tasks (in priority order):**

1. **Confirm/establish the producer I/O paths.** Decide whether to standardize on
   `runs/<YYYY-MM-DD>-<company-slug>.json` for outputs and `runs/targets.csv`
   (`company,location,school,status`) as the daily queue. If yes, add a short
   "Producer I/O" section to `commands/network-import.md` (or a new
   `docs/CHROME_PRODUCER_CONTRACT.md`) documenting these as the canonical
   locations so the Cowork scheduler can rely on them. If you prefer different
   paths, name them and I'll have the producer target those.

2. **Decide the three optional fields (Q13/Q14 in the response).** Today
   `importer.py:_apply_aliases` silently drops unknown keys, so these vanish
   unless you add them:
   - file-level **`school`** — currently `_read_rows` only lifts
     `company/company_slug/location/source` from JSON meta;
   - per-contact **`alumni_confirmed`** (bool) — stronger ALUMNI signal than a
     classifier guess;
   - per-contact **`connection_degree`** (1st/2nd/3rd) — could prioritize the
     LinkedIn channel.
   For each: either extend the alias map + `ContactCandidate` schema + ingest to
   honor it, or explicitly decline (the producer will then omit it to keep files
   clean). Recommend honoring at least `alumni_confirmed` since it improves
   classify accuracy for the highest-value persona.

3. **Confirm the auto-`--draft`-after-confirm flow is acceptable.** The producer
   plans to run `--validate` (no writes) always, then `--draft` after Sid's
   one-line confirm, because the send gate is downstream in the approval
   artifact. If you'd rather it stop at the file + `--validate` and have Sid run
   `--draft`, say so.

4. **Add a contract regression test using the sample.** Wire
   `docs/chrome-capture.example.json` (or a copy under `tests/fixtures/`) into a
   test that asserts `validate_contacts_file(...)["ok"] is True`, count == 6, and
   no errors — so the producer's exact output shape stays a guaranteed-supported
   input as the importer evolves.

5. **Reply with decisions on 1–3** (paths, which optional fields you'll honor,
   draft-flow) so the producer can finalize its emit format and the Cowork
   scheduler can be wired to the agreed paths.

Constraints: don't change the required-field contract (`full_name` only required;
`persona`/`focus_area`/`hook` respected-when-present, generated-when-absent). Keep
the full suite green. Email stays optional/Hunter-only.
