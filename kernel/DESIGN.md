# Design: generic CBA → canonical ratesheet pipeline

> **Status: design only — not yet built.** This document specifies the
> architecture and records the feasibility analysis. No pipeline code exists yet
> beyond the single-union proof in `extract/`.

## Context

Single-union extraction is proven: [`extract/build_483.py`](extract/build_483.py)
reproduces UA Local 483's **Building zone at 100%** (294/294 cells), and
[`extract/compare_483.py`](extract/compare_483.py) grades any output against the
groundtruth (header diff, key-based row alignment, cell accuracy ±0.01, per-column
and per-zone breakdown, unsourced-cell list).

The open question: can **one** pipeline ingest **any** union's CBA documents and
emit that union's canonical ratesheet? This document answers it.

## Finding: the groundtruths are a canonical *schema family*, not one schema

Measured across all 5 groundtruths:

- **Same row model** everywhere: one row per `Zone × Classification (× Indenture
  variant)`; classifications follow a ladder (General Foreman → Foreman →
  Journeyman → Apprentice tiers).
- **Shared skeleton:** 9 columns in all 5 — `Union Group, Trade, Union Local,
  Zone, Package, Start Date, End Date, Wage, Wage 1.5x`; ~6 more in 4/5 —
  `Wage Differential, Wage 2.0x, Health & Welfare, Pension, SIS, UA International
  Training`.
- **Union-specific long tail:** ~30 fringe columns appear in only one local,
  number-suffixed — `HRA 483`, `S.U.B. 704`, `Market Recovery 821`, `LMCC 281`,
  `Vacation 1–6 537`, …
- **Structural variants:** indenture-date columns only in 281 & 821; **537 is the
  outlier** (no Wage Differential/2.0x — uses `Wage 1.1x` + `Temporary Heat`
  variants, `Pension National/Local + Annuity`, 6 vacation tiers); 821 adds
  `Pension Metal`, 4 zones, Helper/Tradesman/Production classes; apprentice naming
  differs (`Class N` vs `Year N` vs `Year 2-A/2-B`).
- **Internal inconsistency:** the 281 **CSV** (15 rows, Building only) ≠ 281
  **XLSX** (18 rows, adds an Office zone + special rows). The groundtruth itself
  is not perfectly canonical.

**Verdict:** a generic pipeline is feasible, but only as a **canonical
intermediate model + per-union schema profiles** — never a single hardcoded
parser. The output side generalizes cleanly; the *input* (PDF → values) is the
hard, variable part, and some values are simply absent from the documents.

## Architecture

### A. Canonical intermediate model (tidy/long — one record per rate cell)

```
union_local, zone, classification, class_order, indenture_before, indenture_after,
effective_start, effective_end, canonical_field, value, value_kind ($|%|xN),
source_doc, source_locator, confidence
```

Plus a **canonical field dictionary** mapping union-specific names to shared
concepts (e.g. `J&A Training 483`, `Apprenticeship Training`, `Local 483 Training
Fund` → `apprenticeship_training`) while preserving each union's exact output
label. This long form is the "any → canonical" target every CBA maps into.

### B. Per-union schema profile (declarative config, e.g. `profiles/<union>.yaml`)

Defines the exact output ratesheet: ordered column list, each column's source
(canonical_field, a computed multiplier, a split, or a constant), value formatting
(`$` vs `%`), the zone/classification taxonomy, and the key columns used for
comparison. Adding a new local = author a profile (+ mostly-reused extraction),
not new parser code. The 537/821 variants become **data, not code branches**.

### C. Pipeline stages

1. **Ingest / normalize** — per union, enumerate `cba/*`; extract text + tables
   (`pdfplumber`); **OCR fallback** for image-only pages (render via `pypdfium2` +
   `tesseract`, or `ocrmypdf`) — 483's rate-notice page 1 was textless.
2. **Extract → canonical rows (HYBRID)**
   - *Deterministic* table parsers for clean rate-notice grids (fast, exact;
     proven on 483).
   - *LLM-assisted* (Claude API) for prose rules (foreman differentials,
     apprentice %s, package allocations) and messy/image tables. Every value
     carries `source_doc`, `source_locator`, `confidence`. **Requires an
     Anthropic API key.**
3. **Compute derived columns** — deterministic: multipliers (`×1.15` differential,
   `×1.5`, `×2.0`, `×1.1`), splits (`H&W = combined − RESA`), foreman
   differentials, apprentice scaling. **Half-up rounding** (proven necessary:
   `83.505 → 83.51`, not banker's `83.50`).
4. **Pivot → ratesheet** — apply the union profile to emit the wide CSV to
   `data/<union>/ai_output/<groundtruth-name>.csv`, headers identical to the
   groundtruth. Read-only on `cba/` and `ratesheet/`.
5. **Evaluate** — generalize `extract/compare_483.py` to any union: header diff,
   key-based row alignment, cell accuracy ±0.01, per-column + per-zone breakdown,
   and the unsourced-cell report.

### D. Gap handling (value not present in the documents)

**Blank + flag** — never fabricate. Leave the cell empty and list it in an
`ai_output/<union>.gaps.md` report with row key, column, and reason — exactly what
the 483 run does for the residential 1/1/2026 allocation. (Prior-year inference
and human-worklist merge were considered and deferred.)

## Files to create when built (not yet)

- `canonical/model.py`, `canonical/fields.yaml` — model + field dictionary.
- `profiles/<union>.yaml` — one per local; reuse for new unions.
- `pipeline/{ingest,extract,compute,pivot}.py` — stages B–D, reusing the parse in
  `extract/build_483.py:parse_rate_notice`.
- `pipeline/evaluate.py` — generalized from `extract/compare_483.py`.

## Reuse of existing work

- [`extract/build_483.py`](extract/build_483.py) — proves stages 1/3/4 and the
  rate-notice table parse; becomes the deterministic-extractor reference and the
  483 profile's logic.
- [`extract/compare_483.py`](extract/compare_483.py) — becomes
  `pipeline/evaluate.py` (already does header diff + row alignment + cell accuracy
  + per-column/zone + mismatch listing).
- The harness agents (`.claude/agents/*`, `.claude/harness/criteria.md`) already
  encode the read-only / `ai_output`-only rules and the comparison rubric.

## Verification (when the pipeline is built)

1. **Schema round-trip** — load each groundtruth header; confirm the union profile
   reproduces it exactly (names + order) for all 5 locals.
2. **Re-prove 483** — run the generic pipeline on 483: Building zone still 100%,
   same gaps reported. Regression guard against `extract/build_483.py` output.
3. **Outlier coverage** — run on **537** (no Differential/2.0x, Temporary Heat, 6
   vacation tiers) and **821** (Pension Metal, 4 zones): profiles handle the
   variants without code branches; report per-zone accuracy.
4. **Gap honesty** — unsourceable cells are blank and enumerated; no
   `cba/`/`ratesheet/` file mtime changes (read-only).
5. **Accuracy bar** — per `criteria.md`: header exact, ≥98% cell accuracy on
   documented cells, every groundtruth row present once.

## Honest caveats

- **No pure-deterministic generalization.** Documents vary too much (tables vs
  prose rules vs image-only scans); the hybrid LLM step is what makes "any CBA"
  tractable, and it is not free (API key, tokens, non-determinism mitigated by
  deterministic post-checks).
- **100% is not guaranteed for any CBA.** Some required numbers aren't in the
  documents (the 483 residential allocation). The honest target is high accuracy
  on *documented* cells plus explicit flagging of the rest.
- **Groundtruth is an imperfect oracle.** Same-union CSV/XLSX disagree (281), so
  the evaluator compares against one declared groundtruth file per union and
  surfaces, rather than hides, such discrepancies.
