# Chrome producer contract

**Date:** 2026-06-21  **Status:** Agreed — the wiring the Cowork + Chrome
producer can rely on. Settled from `docs/COWORK_CHROME_PRODUCER_RESPONSE_2026-06-21.md`.

This is the stable interface between the **Cowork + Chrome producer** (read-only,
human-paced LinkedIn capture) and the **plugin**. The producer can build its
scheduler against these paths and fields without them shifting underneath it.

---

## 1. I/O paths (canonical)

| Path | Purpose |
|---|---|
| `runs/targets.csv` | The daily queue. Columns: `company,location,school,status`. A blank `status` = not done; the producer stamps `done` after a successful cycle. |
| `runs/<YYYY-MM-DD>-<company-slug>.json` | One capture file per company per day. `<company-slug>` matches the importer's `_slugify` (`Joby Aviation` → `joby-aviation`). The producer also treats the *presence* of this file as the done-marker. |

`runs/` is **git-ignored** — captures contain real contact data and the queue is
runtime state. The producer creates `runs/` on first use.

`targets.csv` example:
```
company,location,school,status
Joby Aviation,"Dayton, OH",UIUC,
Sierra Space,"Louisville, CO",UIUC,
AST SpaceMobile,"Midland, TX",UIUC,
```

## 2. The capture file (canonical JSON)

Same canonical contract as `docs/FLEXIBLE_INPUT_DESIGN_2026-06-21.md` §2. Only
`full_name` is required; `title` + `linkedin_url` + a company are recommended.
File-level `company`, `location`, `school`, `source` apply to every contact.

```json
{
  "company": "Joby Aviation",
  "location": "Dayton, OH",
  "school": "UIUC",
  "source": "chrome",
  "contacts": [
    {
      "full_name": "Jane Doe",
      "title": "Structures Engineer",
      "linkedin_url": "https://www.linkedin.com/in/janedoe",
      "location": "Dayton, OH",
      "about": "UIUC AE '22 — composite structures",
      "persona": "ALUMNI",
      "alumni_confirmed": true,
      "connection_degree": "2nd"
    }
  ]
}
```

### Fields the plugin honors (incl. the three the producer proposed)

| Field | Honored how |
|---|---|
| `full_name` | **Required.** |
| `title`, `linkedin_url`, `location`, `email` | Stored. `linkedin_url` is the dedup key. `email` optional (usually omitted; Hunter's job). |
| `about` | LinkedIn headline-as-about; grounds the hook + classifier. |
| `persona` | Honored when present (`RECRUITER` / `SENIOR_MANAGER` / `PEER_ENGINEER` / `ALUMNI`); else classified. |
| `focus_area` | Honored when present; else classified. Producer usually omits. |
| **`alumni_confirmed`** (bool) | **Forces the `ALUMNI` persona** (ground truth beats the classifier guess) and is recorded in `shared_signals`. Emit `true` for anyone sourced via the Alumni tool. |
| **`school`** (file-level) | Recorded in `shared_signals` as campaign context. |
| **`connection_degree`** (`1st`/`2nd`/`3rd`) | Recorded in `shared_signals` so the reviewer can prioritize the LinkedIn invite channel. (No automatic channel routing yet — surfaced for the human.) |

Unknown keys are ignored silently, so emitting extra fields never breaks import —
but only the fields above are stored. `persona`/`focus_area`/`hook` are always
**respected when present, generated when absent**; the required-field contract is
unchanged (`full_name` only).

## 3. Daily cycle (agreed)

`validate → human confirm → draft`. Validation writes nothing; `--draft` is local
Python (never touches LinkedIn); **sending stays gated** by the marketer approval
artifact downstream — so the producer auto-running `--draft` after Sid's one-line
confirm bypasses no human gate that matters.

```
# 1. produce the capture file (read-only, paced)
#    runs/2026-06-21-joby-aviation.json

# 2. validate (no writes) — the contract check
python -m src.cli.network_import runs/2026-06-21-joby-aviation.json --validate

# 3. Sid confirms the captured list (30-second eyeball)

# 4. import + draft (local only; company/location are inside the file)
python -m src.cli.network_import runs/2026-06-21-joby-aviation.json --draft

# 5. stamp targets.csv status=done; Sid reviews drafts in the approval artifact
#    and sends (~12-15 LinkedIn invites to alumni + email the rest)
```

## 4. What the producer omits (by design)

`email` (Hunter's job), full "About" text at volume (headline only), and
exact sub-metro location (LinkedIn is metro-level). These are expected gaps, not
errors.

---

**Regression guarantee:** `docs/chrome-capture.example.json` is the producer's
reference output shape and is covered by `tests/test_chrome_capture_contract.py`
(validates clean, 6 contacts) — so this exact shape stays a supported input as
the importer evolves.
