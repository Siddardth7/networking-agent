# Classify-accuracy scorecard — baseline (v0.5.5, issue #4)

**Date:** 2026-06-26 · **Classifier:** `finder._classify_contact` (Haiku
`claude-haiku-4-5`) · **Labeled set:** `src/eval/classify_labeled_set.json`
(19 contacts, qa-expert-supervised) · **Harness:** `src/eval/classify_scorecard.py`
· **Reproduce:** `python -m src.eval.classify_scorecard` (live, needs the
Anthropic key); metric logic is keyless-tested in `tests/test_classify_scorecard.py`.

Closes the gap the Finder audit named (FINDER_AUDIT **D3**): classify was tested
for *shape*, never *accuracy*.

## Agreed accuracy bar (gates v0.5.5)

**Persona ≥ 100% · Focus-area ≥ 95%.** v0.5.5 does not ship until #5 reaches this
on this set (the DoD accuracy learning loop). Set chosen 2026-06-26.

## Baseline (live Haiku, n=19)

| Dimension | Accuracy | Macro-F1 | vs bar |
|---|---:|---:|---|
| **Persona** | **100%** | 1.00 | ✅ meets |
| **Focus-area** | **68%** | 0.67 | ❌ needs +27pt (→ #5) |

### Persona — 100% (n=19)
All four personas perfect, **including the deliberate hard cases**: Principal/
Senior-Staff individual contributors → `SENIOR_MANAGER` (James Okafor, Mei Lin),
HR Generalist → `RECRUITER` (Greg Olsen). The persona classifier is solid; no
fix needed.

### Focus-area — 68% (n=19), macro-F1 0.67

| class | support | precision | recall | F1 |
|---|---:|---:|---:|---:|
| COMPOSITE_DESIGN | 2 | 40% | 100% | 0.57 |
| STRUCTURAL_ANALYSIS | 2 | 67% | 100% | 0.80 |
| MANUFACTURING | 2 | 67% | 100% | 0.80 |
| MATERIALS | 2 | 100% | 100% | 1.00 |
| ADDITIVE | 2 | 67% | 100% | 0.80 |
| PEER | 5 | 100% | 60% | 0.75 |
| ALUMNI_ACADEMIC | 4 | 0% | 0% | 0.00 |

## Root-cause of the focus gap — 5 of 6 misses are *unspecified convention*, not error

| contact | gold | predicted | category |
|---|---|---|---|
| Anjali Rao (PhD) | ALUMNI_ACADEMIC | COMPOSITE_DESIGN | spec gap |
| Marco Bianchi (GRA) | ALUMNI_ACADEMIC | COMPOSITE_DESIGN | spec gap |
| Priya Nair (postdoc) | ALUMNI_ACADEMIC | ADDITIVE | spec gap (also genuinely ambiguous) |
| Tyler Brooks (undergrad RA) | ALUMNI_ACADEMIC | STRUCTURAL_ANALYSIS | spec gap |
| Dana Whitfield (recruiter) | PEER | MANUFACTURING | spec gap |
| Riya Kapoor (Design Eng) | PEER | COMPOSITE_DESIGN | genuine over-read |

**The classifier's tool schema never tells the model the focus-area convention
for two personas:**
1. **ALUMNI → `ALUMNI_ACADEMIC`** (regardless of research topic). All 4 ALUMNI
   were assigned their *topic* focus instead → `ALUMNI_ACADEMIC` scored 0/0/0.00.
2. **RECRUITER → `PEER`** (focus-area is not meaningful for non-engineers). The
   one recruiter with a domain-saturated snippet got `MANUFACTURING`.

Only **Riya Kapoor** (generalist "Design Engineer" → over-read as
`COMPOSITE_DESIGN`) is a genuine classifier miss.

**Focus-area gold convention (documented for reproducibility):** ALUMNI persona →
`ALUMNI_ACADEMIC`; RECRUITER persona → `PEER`; engineers → their specialty, or
`PEER` if generalist. The labels are correct *intent*; the baseline correctly
shows the live model doesn't yet follow it because it was never told.

## qa-expert supervision (methodology + labels)

- **Methodology:** no bugs. Precision/recall/F1, confusion matrix, accuracy, and
  macro-F1 (averaged only over classes present in gold or predictions) are
  correct; the classifier-agnostic design (inject `classify_fn`; live glue
  `pragma: no cover`) is sound for reproducible measurement.
- **Labels:** the ALUMNI/RECRUITER focus convention is the one called out above —
  validated by the live data. Aerospace-only domain noted as acceptable for a
  Phase-A baseline (generalization is Phase B). Per-class support is thin
  (2/class for engineering focuses) — a single miss swings recall 0.5.

## Routing to #5 (classify fixes to hit the bar)

1. **Add the two spec rules** to the `focus_area` tool description in
   `finder._classify_contact`: ALUMNI → `ALUMNI_ACADEMIC`; RECRUITER → `PEER`.
   Expected to recover 5 of 6 misses (→ ~95% focus).
2. **Fix the generalist over-read** (Riya) — tighten guidance so a generalist
   title without a specialty signal lands on `PEER`.
3. **Refine the set for ≥95% confidence:** add a few more samples per engineering
   focus class (2/class is coarse) + the discriminating cases qa-expert
   suggested (non-canonical seniority like "Lead Engineer", academic titles like
   "Research Scientist", a domain-saturated recruiter). Re-run until
   **persona 100% / focus ≥95%**.
