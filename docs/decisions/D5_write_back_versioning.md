# Decision 5 — Improved result is a **new version (v₍n+1₎)**, human-approved

**Status:** ✅ DONE — decided and built (live).
**Context:** Phase 2 improvement loop; how the AI's output is written back.

## Decision
An Improve run **never mutates** the reviewed sheet. It produces a **new version
v₍n+1₎** — a fresh `rate_period` + copied/updated `rate_cells` — exactly like the existing
rework flow. The new version lands in **`pending_review`**; **a human still approves it**
(dual control). Lineage (`parent_version`) is preserved, so every version is replayable.

## Why
- **Auditable lineage, never destroy evidence.** The original values + the reviewer's
  corrections + the AI's output all coexist; an auditor can diff v1→v2 and see exactly what
  changed, by whom/what, and why. Mutating in place would erase the before-state.
- **Dual control.** The agent proposes; a human disposes. No AI-authored dollar value
  reaches `approved`/`published` without a human gate — required for a legal/financial SoR.
- **Consistency.** Rework already versions this way; Improve reuses the same machinery and
  the same UI affordances (version switcher, approve/reject bar).

## What changed (CDK-deployed)
- `improver` agent `_write_new_version`: copies cells to v+1 (`:val::numeric` /
  `:conf::numeric` casts), sets `parent_version`, writes one `improvement_changes` row per
  changed cell (`source` ∈ override|recompute|resynth|profile-fix, provenance, confidence).
- `improvement_runs` row carries `from_version`→`to_version`, model, status, summary.
- New version defaults to `pending_review`; `ApproveRejectBar` unchanged.
- Change-log panel in `RateSheetReview` reads `improvement_changes` so the human sees
  prior→new + source + provenance before approving (the "what the agent changed" view).

## Verified
Override run produced v2 (Wage 60→90/120/69) with v1 fully intact; comment run produced a
v2 citing "Article 6 Section 14"; both pending_review until a human approved. Version
switcher shows the lineage.
