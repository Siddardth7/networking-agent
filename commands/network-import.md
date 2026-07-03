---
description: "Import a leads file from any source (Apollo / Apify / Serper / Cowork+Chrome / manual CSV or JSON), normalize it, and optionally draft outreach for every contact."
---

# /network-import


> **Shell note (Windows):** the commands below use the plugin's Python runner. In bash / WSL / Git-Bash use `"${CLAUDE_PLUGIN_ROOT}/bin/nag" …` exactly as written; in **native PowerShell** substitute the runner with `& "$env:CLAUDE_PLUGIN_ROOT\bin\nag.ps1" …` (same module and args).

Feed contacts into the pipeline from a file instead of the Serper Finder. Any
source works — an Apollo export, an Apify scrape, a Claude Cowork + Claude-in-
Chrome capture, or a list you compiled by hand. The importer normalizes every
row into the canonical contact record, fills `persona` / `focus_area` / `hook`
when the file doesn't supply them, and runs the same drafter the Finder feeds.

## Usage

```
/network-import <file> [--company <name|slug>] [--location <loc>]
                       [--source auto|apollo|apify|serper|chrome|manual]
                       [--draft] [--validate]
```

**Examples:**
```
/network-import apollo-joby-dayton.csv --company "Joby Aviation" --draft
/network-import chrome-capture.json --draft          # company is inside the file
/network-import leads.csv --company joby --validate   # dry-run, no writes
```

## What happens

1. **Parse + normalize** — CSV or JSON; headers are matched by alias
   (`Person Linkedin Url` / `profileUrl` / `linkedin` → `linkedin_url`, etc.),
   so exports work without renaming. Deduplicated by LinkedIn URL.
2. **Ingest** — each contact runs the shared enrich → classify → hook → save
   path. `persona`/`focus_area` are classified when absent (1 Haiku call each)
   and honored when the file already labels them; an `email` in the file is
   trusted (no Hunter spend).
3. **Draft (with `--draft`)** — contacts are marked SELECTED and drafted
   immediately (4-part voice, ask-rotation, quality gate), then the artifact is
   written — the frictionless "file in → drafts out" path.

## The canonical contact record

The only required field is `full_name`. Recommended: `title`, `linkedin_url`,
`company` (or pass `--company`). Optional: `email`, `location`, `about`
(LinkedIn headline/About — grounds the hook), and explicit `persona`,
`focus_area`, `hook` overrides.

JSON form (what a Cowork + Chrome producer should emit):
```json
{
  "company": "Joby Aviation",
  "location": "Dayton, OH",
  "contacts": [
    {"full_name": "Jane Doe", "title": "Structures Engineer",
     "linkedin_url": "https://www.linkedin.com/in/janedoe",
     "email": "jane@joby.aero", "about": "UIUC AE '22, composites"}
  ]
}
```

CSV form: one row per contact; `company`/`location` may be columns or flags.

## Flags

`--company <name|slug>` — default company when the file has no company column.
`--location <loc>` — default location context.
`--source <kind>` — override format detection (default `auto` by extension).
`--draft` — mark imported contacts SELECTED and draft them right away.
`--validate` — dry-run: report parse errors / warnings (missing company, no
channel, no title) and the usable count **without writing anything**. This is
the contract check a Cowork + Chrome producer runs before importing.

## Producer I/O (Cowork + Chrome)

The Cowork + Chrome producer writes captures to
`runs/<YYYY-MM-DD>-<company-slug>.json` and reads its daily queue from
`runs/targets.csv` (`company,location,school,status`). `runs/` is git-ignored
(real contact data). It honors three extra producer fields — `alumni_confirmed`
(forces the ALUMNI persona), file-level `school`, and `connection_degree`
(surfaced for send-prioritization). Full contract: `docs/CHROME_PRODUCER_CONTRACT.md`.

## Implementation

- CLI entry: `src/cli/network_import.py` → `run_import(args)`
  ```
  "${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_import <file> --company "Joby Aviation" --draft
  "${CLAUDE_PLUGIN_ROOT}/bin/nag" src.cli.network_import <file> --validate
  ```
- Importer: `src/agents/importer.py` → `import_contacts(path, company=…, draft=…)`
- Validator: `validate_contacts_file(path)`
- Shared ingest: `src/agents/finder.py` → `ingest_contacts(...)`
- Design: `docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md`
