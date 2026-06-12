# Rate-Engine Stabilization: Profiles + Objective-Driven Synthesizer

**Status:** active · **Owner:** NBS · **Started:** 2026-06-11

This is the living design + improvement log for moving the rate engine off the
"moving target" (per-union curve-fitting) onto a **stable, profile-driven**
foundation. Append new entries to the *Improvement Log* at the bottom; do not
rewrite history.

---

## 1. The problem we are fixing

The earlier pipeline extracted each PDF **in isolation** and then **mechanically
merged** the results in the publisher with deterministic rules (doc-type
precedence, dedupe, row-level overwrite, name normalization). Every rule was
tuned to a specific input→output pair we had already seen. Consequences:

- A new CBA + rate-sheet shape broke it (the merge rules didn't generalize).
- The LLM never saw the *relationship* between documents, so it could not reason
  about precedence, indenture cohorts, or fund naming.
- Each "fix" was a band-aid → the solution never stabilized.

**Root cause:** we were curve-fitting deterministic post-processing instead of
giving the LLM the right *objective* and a *fixed target schema*.

## 2. The stable architecture

Two pieces, each with a single responsibility:

### 2a. Per-union PROFILE (the frozen oracle) — `profiles/<trade>_<local>.yaml` (+ `.json` twin)

Built by `_TMP_/build_profiles.py` from the client's own ground-truth rate
sheet in `From Customer/Rate Sheets/<Trade>/<Local>/`. Each profile captures
**structure only — never dollar values** (so it is safe to commit; customer
rates stay out of the repo):

- `column_order` — the exact canonical column layout the client expects
- `fund_columns` — exact fund names + `percent` flag (e.g. `Union Dues 281` is %)
- `packages` — exact classification names (e.g. `Apprentice Year 2-A`, not `Class 2`)
- `zones`, `has_cohorts`, `cohorts` (indenture before/after windows)
- `wage.derived_multipliers` — `{Wage Differential: 1.15, Wage 1.5x: 1.5, Wage 2.0x: 2.0}`
- `source_cba_folder` / `oracle_sheet` — provenance back to the matched inputs

The profile is **both** the synthesizer's target schema **and** the test oracle.
Matching `From Customer/CBAs/<Trade>/<Local>` ↔ `Rate Sheets/<Trade>/<Local>` by
local number gives us source↔output pairs for free.

### 2b. SYNTHESIZER (the reasoning pass) — `lambdas/processing/synthesizer/handler.py`

One holistic LLM call per rate period. **All** documents for the period (CBA +
rate notices + wage sheets) go in **together**, each labelled with its role,
plus the frozen profile as the exact target schema, plus the objective:

- CBA defines **structure** (packages, zones, funds, formulas, cohort rules).
- Rate notice / wage sheet carries the **authoritative current $** that
  **supersedes** the CBA for the target period (latest effective ≤ period wins).
- Apprentice sheets carry **cohort** wages; emit one row set per cohort.
- Use the profile's **exact** canonical names; map source labels onto them.
- **Never fabricate**; blank-with-a-gap-note beats a guessed number.

**Division of labor (the key principle):** the LLM does *interpretation* (which
value, which cohort, what supersedes, label mapping); **code** does *arithmetic*
(derived multiplier columns computed in Decimal space, round-half-up). LLMs do
not multiply. Differential columns that are *explicitly stated* in a document
(journeyman shift wage) are extracted as-is; only unstated ones are derived.

## 3. Profile inventory (built 2026-06-11)

17 profiles built from client rate sheets. **Bold** = POC scope.

| Trade | Local | Pkgs | Funds | Rows | Cohorts | Oracle sheet |
|------|------|----|----|----|----|----|
| Pipefitters | **537** | 9 | 22 | 10 | – | 2026.03.01.537 Rate Sheet.xlsx |
| Sprinkler | 120 | 25 | 35 | 25 | – | 2022-2027.120 Rate Sheets.xlsx |
| Sprinkler | 183 | 8 | 13 | 8 | – | 2026.01.01.183 Rate Sheet.csv |
| Sprinkler | 268 | 7 | 10 | 7 | – | 2026.01.01.268 Rate Sheet.csv |
| Sprinkler | **281** | 9 | 9 | 15 | **Y** | 2026.01.01.281 Rate Sheet.csv |
| Sprinkler | 314 | 7 | 12 | 7 | – | 2026.01.01.314 Rate Sheet.csv |
| Sprinkler | 417 | 14 | 15 | 14 | – | 2024-2027.417 Rate Sheet.csv |
| Sprinkler | **483** | 15 | 14 | 21 | – | 2026.01.01.483 Rate Sheet.csv |
| Sprinkler | 542 | 12 | 16 | 24 | – | 2026.01.01.542 Rate Sheet.csv |
| Sprinkler | 550 | 13 | 24 | 36 | – | 2026.01.01.550 Rate Sheet.csv |
| Sprinkler | 669 | 14 | 16 | (messy) | – | 2025.01.01.669 Rate Sheet.xlsx |
| Sprinkler | 692 | 12 | 13 | 12 | – | 2026.01.01.692 Rate Sheet.csv |
| Sprinkler | 696 | 14 | 16 | 34 | **Y** | 2026.01.01.696 Rate Sheet.csv |
| Sprinkler | 699 | 19 | 11 | 23 | – | 2026.01.01.699 Rate Sheet.csv |
| Sprinkler | **704** | 13 | 13 | 13 | – | 2026.01.01.704 Rate Sheet.csv |
| Sprinkler | 709 | 13 | 12 | 19 | – | 2026.01.01.709 Rate Sheet.csv |
| Sprinkler | **821** | 14 | 14 | 51 | **Y** | 2026.01.01.821 Rate Sheet.csv |

Observations:
- **Cohort unions** (indenture splits) = 281, 696, 821 — the hardest cases.
- Fund lists are mostly shared (H&W, RESA, Pension, SIS, UA Intl Training,
  Apprenticeship Training, Industry Promotion National/Local) + local-specific
  funds (e.g. `S.U.B. 704`, `Bay Area IP Fund 483`, `Market Recovery 821`).
- `Wage Differential / 1.5x / 2.0x` = 1.15 / 1.5 / 2.0 across the board (537 uses
  P&G multipliers handled separately).
- Percent-based funds appear as `%` (e.g. `Union Dues 281` 3.00%, `Market
  Recovery 821` 2.00%, `Union Dues 1 483` 6.00%).

## 4. Validation — 281 (hardest union, cohorts)

| Metric | Result |
|---|---|
| Rows | **15 / 15** exact (by cohort+package key) |
| Value cells (all funds, both cohorts, derived cols) | **180 / 180** exact within $0.01 |
| Cohort split from a single apprentice sheet | correct |
| Apprentice Year 2-A vs 2-B Pension split (0.00 vs 7.45) | correct |
| Per-cohort H&W (Year 5: 12.35 vs 13.15) | correct |
| Labels (Apprentice Year, Industry Promotion Local Use) | exact (from profile) |
| Fabricated / duplicate / noise rows | **0** |

Before the profile: 165/165 values correct but labels were the source's
("Apprentice Class N", "Industry Promotion National Use"). After feeding the
profile: literal label match → 180/180, 15/15. **The profile closed the gap with
zero curve-fitting.**

## 5. Upgrade plan (sequenced)

1. **[DONE]** Build profiles for all matched unions (17).
2. **[DONE]** Profile-driven synthesizer; 281 literal exact.
3. **Fix Jobs UI** — batch-process executions show union/period `—` because
   `job-list` parses the old EventBridge S3-event input shape, not the new
   `{batch_id, batch_period, files}` shape. Parse both (and prefer the planner's
   resolved output).
4. **Re-validate 704, 537, 821, 483** against their profiles via the synthesizer
   (offline harness first, like 281). Gate before any pipeline swap.
5. **Wire synthesizer into the Step Function** — `PLAN → SYNTHESIZE → render`,
   replacing per-doc-extract + publisher-merge. Keep the explicit "Process
   batch" button. Bundle `profiles/*.json` into the synthesizer Lambda.
6. **Promote** the synthesizer to default only after 4–5 pass for all POC unions.
7. **CDK sync** — bring the boto3-applied Lambdas/SFN/API/EventBridge changes,
   the guardrail ANONYMIZE change, and the new synthesizer + profiles into CDK
   source so `cdk deploy` doesn't revert live state.
8. **Profile refresh job** — re-run `build_profiles.py` whenever the client
   sends new rate sheets; treat profiles as versioned reference data.

## 6. Open questions / risks

- **537 multipliers**: P&G ladder (1.10/1.15/1.25) + OT didn't infer cleanly from
  the xlsx; confirm 537's derived columns extract correctly in step 4.
- **669**: source folder is messy (v2/v3, 1159-row consolidated sheet); out of
  POC scope, profile is best-effort.
- **Per-period profiles**: profiles are built from the *current* sheet; if column
  layout changes across periods, key profiles by period range.
- **Pipeline swap risk**: 704/537 currently work via the old path; do not switch
  the default until step 4 passes for every POC union.

---

## Improvement Log (append newest at the bottom)

### 2026-06-11 — Foundation laid
- Diagnosed curve-fitting as the root cause of the moving target.
- Built objective-driven synthesizer (`lambdas/processing/synthesizer/`).
- Built 17 per-union profiles (`profiles/`, YAML + JSON), structure-only.
- Wired profiles into the synthesizer as the frozen target schema.
- **281: literal 15/15 rows, 180/180 value cells exact** vs client.
- Moved derived-column math into code (Decimal, round-half-up); LLM extracts
  stated differentials, code derives the rest.
- Next: fix Jobs UI union/period; re-validate 704/537/821/483 offline.
