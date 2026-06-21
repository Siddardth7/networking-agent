# Flexible-input Drafter — design

**Date:** 2026-06-21  **Status:** Design (pre-build)
**Goal:** Let contacts enter the pipeline from *any* source — Apollo export,
Serper/Apify dumps, a Claude Cowork + Claude-in-Chrome automation, or a manual
file the user compiles — not just the Serper Finder. The Drafter skill becomes a
source-agnostic "leads file in → personalized drafts out" engine, distributable
in the plugin so anyone can draft connection notes for leads from anywhere.

---

## 1. Principle: normalize at the edge, keep one core

Today the **Finder is the only door** into the pipeline, and it couples three
separable jobs: **discover** (Serper) → **enrich/classify/hook** → **save**. The
Drafter then reads `contacts` rows that already have `persona`, `focus_area`, and
`hook` set.

The fix is one architectural move:

> **Split discovery from ingestion.** Every source becomes a *producer* of a
> single **canonical contact record**; all producers flow through one
> source-agnostic **ingest** path (enrich → classify → hook → save), after which
> the existing Drafter runs completely unchanged.

```
                 ┌─ Serper Finder ─┐
 Apollo CSV ─────┤                 │
 Apify JSON ─────┤  adapters →     │   canonical
 Cowork+Chrome ──┤  CanonicalContact ├──►  INGEST  ──► contacts table ──► DRAFTER
 Manual file ────┘                 │  (classify→hook→save)   (unchanged)
```

Benefits: adapters are tiny and isolated; the classify/hook/draft core is written
once; the Cowork/Chrome automation just has to emit the canonical JSON.

---

## 2. The canonical contact record (the contract)

This is the single schema every source maps to — and exactly what the Cowork +
Chrome automation must emit. JSON form:

```json
{
  "company": "Joby Aviation",          // name or slug — drives the Company: prompt
                                        //   line (anti-fabrication) + ask-rotation grouping
  "company_slug": "joby-aviation",     // optional; derived from `company` if absent
  "location": "Dayton, OH",            // optional; campaign/location context
  "source": "apollo",                  // optional provenance tag (apollo|apify|serper|chrome|manual)
  "contacts": [
    {
      "full_name": "Jane Doe",         // REQUIRED — the only hard-required field
      "title": "Structures Engineer",  // recommended — drives focus_area, hook, achievement match
      "linkedin_url": "https://www.linkedin.com/in/janedoe", // recommended — dedup key + channel target
      "email": "jane@joby.aero",       // optional — enables the cold-email channel
      "location": "Dayton, OH",        // optional per-contact override
      "about": "UIUC AE '22, composite structures…",         // optional — LinkedIn About/headline;
                                        //   grounds the classifier + extracts a hook_signal
      "persona": "ALUMNI",             // optional — if present, skip the classifier (saves a call)
      "focus_area": "STRUCTURAL_ANALYSIS", // optional — same
      "hook": "we share a UIUC background" // optional — user-supplied hook overrides generation
    }
  ]
}
```

**CSV form:** one row per contact; `company`/`location` may be columns or passed
via a `--company` / `--location` flag. Headers are matched by **alias** (below),
so Apollo/manual exports work without renaming.

Field rules:
- `full_name` is the only required field.
- `persona` / `focus_area` / `hook` are **respected when present, generated when
  absent** — so a labeled file skips LLM work; a raw file gets auto-classified.
- Dedup key: normalized `linkedin_url`, else `full_name`+`company`.

---

## 3. Adapters (source → canonical)

| Source | Form | Mapping notes |
|---|---|---|
| **Apollo export** | CSV | `First Name`+`Last Name`→`full_name`; `Title`→`title`; `Company`→`company`; `Person Linkedin Url`→`linkedin_url`; `Email`→`email`; `City`/`State`→`location`; `Seniority`→`persona` (heuristic). |
| **Serper** | JSON | Our own `ContactCandidate` dump — already canonical; pass-through. |
| **Apify** | JSON | Actor output: `fullName`/`name`→`full_name`; `headline`/`occupation`→`title`; `profileUrl`/`linkedinUrl`→`linkedin_url`; `location`; `summary`→`about`. |
| **Cowork + Chrome** | JSON | Emits the canonical schema directly — **no adapter**, it targets the contract. We ship a validator so the Chrome side has an exact target. |
| **Manual** | CSV/JSON | Generic: canonical headers + alias matching; lenient. |

**Header-alias strategy (CSV):** a small alias map normalizes common variants —
`linkedin_url` ← {`linkedin`, `linkedin url`, `person linkedin url`, `profileUrl`,
`profile_url`}; `full_name` ← {`name`, `full name`, `fullName`} (or `first`+`last`);
`title` ← {`title`, `headline`, `job title`, `occupation`}; etc. This single map
covers Apollo, most exports, and manual files without per-source code.

**Format detection:** by extension (`.csv`/`.json`) + key sniffing, with an
explicit `--source apollo|apify|serper|chrome|manual|auto` override (default
`auto`). Unknown columns are ignored, not fatal.

---

## 4. Enrichment for imported contacts (reuse the Finder's second half)

Imported contacts skip Serper but still need `persona`, `focus_area`, `hook` for
the Drafter. Reuse the Finder's source-agnostic half:
- **`_classify_contact`** (1 Haiku call) → persona + focus_area + hook_signal,
  grounded on `about`/`title`. **Skipped** when the source already supplied
  `persona` *and* `focus_area`.
- **`_generate_hook`** (deterministic tiers) → hook, unless the file supplies one.
- **Email** stays optional/opt-in (Hunter), exactly as today.

Refactor: extract `ingest_contacts(records, company, *, classify=True)` from
`find_contacts`, and have `find_contacts` call it after Serper discovery. Net
effect: **one enrich/save path, two (soon many) front doors.**

---

## 5. Command / skill surface

- **New:** `/network-import <file> [--source auto] [--company <slug>] [--location <loc>] [--draft] [--auto-select]`
  - Parses → normalizes → (classify/hook) → writes `contacts`.
  - `--auto-select --draft` = the frictionless "file in → drafts out" path:
    import, mark SELECTED, run `draft_for_contacts`, write the artifact.
- **Drafter skill** docs extended: "accepts a contacts file from any source via
  `/network-import … --draft`; persona/hook auto-filled when absent."
- The DB-backed core (selection gate, ask-rotation grouping, marketer approval,
  artifact) is reused — `--auto-select` just skips the manual gate for the
  drop-a-file UX.

---

## 6. Cowork + Chrome integration (the external producer)

This lives **outside** the Python plugin — it's a Cowork workflow that drives
Claude-in-Chrome. Division of responsibility:

- **Cowork + Chrome (built in Cowork):** take `company + location (+ school)`,
  drive LinkedIn search / the Alumni tool, paginate, collect profiles, and **emit
  the canonical JSON** (§2) to a file; then invoke `/network-import <file> --draft`.
- **This plugin provides:** the **canonical JSON contract** (§2) + a
  **validator** (`validate_contacts_file`) so the Chrome side has a precise,
  testable target, and the import→classify→draft pipeline that consumes it.

So we don't build Chrome automation here; we make the plugin the perfect *sink*
for it and publish the exact shape it must produce. A `docs/` "Chrome producer
contract" page + the validator are the deliverables for that half.

---

## 7. Phased build plan

- **Phase 1 — canonical core (unblocks everything):** define `CanonicalContact`
  (Pydantic), extract `ingest_contacts()` from the Finder, refactor `find_contacts`
  to call it. No behavior change; full suite stays green.
- **Phase 2 — import + adapters + command:** `importer.py` (JSON + CSV + alias
  map + format detect), `/network-import`, the `--draft`/`--auto-select` path, a
  `validate_contacts_file` helper, tests with sample Apollo/Apify/manual files.
- **Phase 3 — Cowork/Chrome contract:** publish the producer contract doc +
  validator; (Chrome automation itself is built in Cowork, not here).
- **Phase 4 — polish for plugin users:** drafter SKILL/README updates, a sample
  template file, graceful errors for malformed inputs.

---

## 8. Open decisions (confirm before building)

1. **Auto-classify vs trust labels:** default = classify with Haiku when
   `persona`/`focus_area` absent, respect when present. (Cost: 1 Haiku call/contact
   on raw files.) OK?
2. **One-shot vs DB-staged:** keep the DB-backed core (reuse ask-rotation,
   marketer, artifact) with a `--auto-select --draft` frictionless wrapper —
   rather than a separate stateless "file→drafts" path that duplicates persistence.
3. **Company handling:** require a `--company` (or `company` field) so the
   Company: prompt line + ask-rotation grouping work; derive `company_slug` if
   only a name is given.
