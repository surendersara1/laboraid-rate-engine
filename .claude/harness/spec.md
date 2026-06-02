# Extraction Spec — generic CBA → ratesheet pipeline (3-union prototype)

Implements the architecture in `DESIGN.md` and is graded by
`.claude/harness/criteria.md`. Build the pipeline (canonical model + per-union
profiles + ingest/extract/compute/pivot/evaluate stages) and **prove it on three
unions**: `sprinkler_fitters_483` (regression), `sprinkler_fitters_704` (close
sibling), `pipe_fitters_537` (structural outlier).

This run targets the **deterministic extraction path only** (pdfplumber tables +
text + documented CBA rules). There is **no Anthropic API key** in this
environment, so any value that requires LLM-assisted prose/image extraction and
cannot be obtained deterministically must be left **blank + flagged**, never
fabricated.

---

## 0. Hard rules (encode in code; graded by criteria 4 & 5)

- **Read-only** on every `data/<union>/cba/` and `data/<union>/ratesheet/`. The
  only writable location is `data/<union>/ai_output/`. Do not change mtimes under
  `cba/`/`ratesheet/`.
- The groundtruth **header** may be read to match column **names/order only**.
  **Never** read groundtruth **values** into output cells. All values derive from
  `cba/` documents (or deterministic compute over them).
- Unsourceable cells → leave **blank** and enumerate them in
  `data/<union>/ai_output/<union>.gaps.md` (row key, column, reason). No guessing.
- Every value column carries documented provenance (source doc + locator), in a
  `COLUMN_SOURCES`-style mapping and/or the canonical model's `source_doc` /
  `source_locator` fields.
- **Half-up rounding** to 2 decimals for every computed dollar cell
  (`Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)` — proven necessary:
  `83.505 → 83.51`). Reuse `r2()` from `extract/build_483.py`.
- Re-runnable from scratch via `uv` (see §5). No secrets committed.

---

## 1. Targets (output paths must match groundtruth base name exactly)

| Union | Groundtruth (read-only) | Output (write here) |
|---|---|---|
| sprinkler_fitters_483 | `data/sprinkler_fitters_483/ratesheet/2026.01.01.483 Rate Sheet.csv` | `data/sprinkler_fitters_483/ai_output/2026.01.01.483 Rate Sheet.csv` |
| sprinkler_fitters_704 | `data/sprinkler_fitters_704/ratesheet/2026.01.01.704 Rate Sheet.csv` | `data/sprinkler_fitters_704/ai_output/2026.01.01.704 Rate Sheet.csv` |
| pipe_fitters_537 | `data/pipe_fitters_537/ratesheet/2026.03.01.537 Rate Sheet.xlsx` (XLSX) | `data/pipe_fitters_537/ai_output/2026.03.01.537 Rate Sheet.csv` |

Notes:
- 537 groundtruth is **XLSX-only**; read its header via `openpyxl` for column
  names/order. Output is still a **CSV** with that exact header (the evaluator
  reads the XLSX groundtruth and the CSV output).
- Each union also writes `data/<union>/ai_output/<union>.gaps.md`.

---

## 2. Column schemas (copy header character-for-character; order matters)

Key columns (used for row alignment) are the same family everywhere:
`Zone`, `Package`, `Start Date`, `End Date`. Everything before them
(`Union Group, Trade, Union Local`) is constant per union; everything after is a
value column.

### 2a. sprinkler_fitters_483 — 25 columns
```
Union Group, Trade, Union Local, Zone, Package, Start Date, End Date,
Wage, Wage Differential, Wage 1.5x, Wage 2.0x,
Health & Welfare, RESA, Health & Welfare Metal, Pension, SIS,
UA International Training, Industry Promotion National Use,
J&A Training 483, NCFPCG 483, Bay Area IP Fund 483, HRA 483,
Vacation 483, Union Dues 1 483, Union Dues 2 483
```
- `Union Dues 1 483` is a **percentage** cell formatted `6.00%`. All other value
  columns are **dollars** formatted `%.2f`. `Wage Differential = Wage × 1.15`.

### 2b. sprinkler_fitters_704 — 21 columns
```
Union Group, Trade, Union Local, Zone, Package, Start Date, End Date,
Wage, Wage Differential, Wage 1.5x, Wage 2.0x,
Health & Welfare, RESA, Pension, SIS, UA International Training,
Apprenticeship Training, S.U.B. 704, Industry Promotion National Use,
Industry Promotion Local Use, S & E 704, Craft 704, Union Dues 704,
Retiree Holiday 704
```
- All value cells are **dollars** (`%.2f`). `Wage Differential = Wage × 1.15`.
- Constants: `Union Group=UA`, `Trade=Sprinkler`, `Union Local=704`.

### 2c. pipe_fitters_537 — 31 columns (outlier; **no** Wage Differential / Wage 2.0x)
```
Union Group, Trade, Union Local, Zone, Package, Start Date, End Date,
Wage, Wage 1.1x, Wage 1.5x,
Temporary Heat, Temporary Heat 1.1x, Temporary Heat 1.5x,
Pension Local, Health & Welfare, Annuity, Industry Improvement, Education,
Labor/Mgt. Trust Fund, Pension National,
Union Dues 537, Organizing Fund 537, C.O.P.E. 537,
Vacation 1 537, Vacation 2 537, Vacation 3 537, Vacation 4 537,
Vacation 5 537, Vacation 6 537, Public Relations, UA PAC
```
- All value cells are **dollars**. Constants: `Union Group=SMART`,
  `Trade=Pipefitter`, `Union Local=537`.
- The 6 vacation columns are flat per-row choices: `0,1,2,3,4,5` (from the CBA's
  "six options $0–$5"). They are **not** computed from wage.

---

## 3. Row taxonomy (every row the output must contain, once each)

Row key = `(Zone, Package, Start Date, End Date)`. Start/End dates are constant
per union (the effective period of that union's rate notice).

### 3a. 483 — 21 rows, Start `1/1/26`, End `7/31/26`
- **Building** (15): General Foreman, Foreman 2, Foreman 1, Journeyman,
  Apprentice Class 10 → Class 1 (descending).
- **Residential** (7): Foreman, Journeyman, Apprentice Class 5 → Class 1.
- Building zone is fully sourceable → **must be 100% cell accuracy** (regression
  guard vs `extract/build_483.py`). Residential 1/1/2026 re-allocation is not in
  the docs → blank+flag the known gaps (see §4a).

### 3b. 704 — 13 rows, Start `1/1/26`, End `7/31/26`, Zone = **Building** only
- General Foreman, Foreman, Journeyman, Apprentice Class 10 → Class 1.
- (The groundtruth CSV has trailing all-blank lines after row 13; emit **only**
  the 13 data rows — no blank rows.)

### 3c. 537 — 10 rows, Start `3/1/26`, End `8/31/26`
- **Power & Gas** (3): General Foreman, Area Foreman, Foreman.
- **Building** (7): Foreman, Journeyman, Apprentice Year 5 → Year 1 (descending).
- Note apprentice naming is **`Apprentice Year N`** (not "Class N").

---

## 4. Source mapping (where each value comes from)

### 4a. sprinkler_fitters_483 (reference — already proven)
Sources: `cba/2026.01.01.483 Rate Notice.pdf` (clean pdfplumber table, eff
1/1/2026) + `cba/2024.08.01-2030.07.31.483 CBA.pdf` (prose rules). Reuse
`extract/build_483.py` verbatim as the 483 profile + deterministic extractor:
- `Wage`: Rate Notice "Rate/HR" per class (`Fitter`=Journeyman); foreman
  differentials from CBA Art.20 (`Foreman 1 = J+10`, `Foreman 2 = F1+3`,
  `GF = F1+5`).
- `Wage Differential = Wage×1.15`; `Wage 1.5x = Wage×1.5`; `Wage 2.0x = Wage×2.0`.
- `Health & Welfare = notice H&W (13.55) − RESA (0.95)`; `RESA = 0.95` (Art.21).
- `Pension, SIS, UA International Training, Industry Promotion National Use,
  J&A Training 483, NCFPCG 483, HRA 483, Vacation 483` from the named notice cols.
- `Bay Area IP Fund 483 = 0.11` (CBA Art.24); `Union Dues 1 483 = 6.00%`;
  `Union Dues 2 483 = 1.05`.
- **Known gaps (blank+flag):** Residential apprentice **wage scale** and the
  Residential `Pension`/`Vacation 483` 1/1/2026 re-allocation are not in the
  provided docs. Match the gap list `extract/build_483.py` already emits.

### 4b. sprinkler_fitters_704 (close sibling)
Sources: `cba/2026.01.01.704 Rate Notice.pdf` and
`cba/2022.08.01-2027.07.31.704 CBA.pdf`.
- **Gotcha — the 704 Rate Notice is image-only (textless, 0 tables).** Deterministic
  pdfplumber cannot read it. The robust deterministic path is **OCR fallback**
  (DESIGN stage 1: render pages via `pypdfium2`, OCR via `tesseract`/`ocrmypdf`)
  to recover the wage/fringe grid; if OCR is unavailable in this environment,
  the wage and notice-sourced fringe cells are **LLM-assisted territory** → leave
  blank + flag, and document that this is the OCR/LLM boundary for this run.
- The 704 CBA PDF **is** text-extractable (use it for prose rules: foreman
  differential, apprentice % scale, fund definitions, and any base wage stated in
  the agreement).
- Column derivations (apply once wages are recovered):
  - `Wage`: Journeyman + Foreman differentials from notice/CBA; apprentice scale
    (Class 1–10) from notice or CBA % ladder.
  - `Wage Differential = Wage×1.15`, `Wage 1.5x = Wage×1.5`, `Wage 2.0x = Wage×2.0`.
  - Flat fringes (constant down the journeyman→top column, from notice/CBA):
    `Health & Welfare, RESA, Pension, SIS, UA International Training,
    Apprenticeship Training, S.U.B. 704, Industry Promotion National Use,
    Industry Promotion Local Use, Union Dues 704`.
  - **Apprentice-tapered fringes (vary by class):** `S & E 704`, `Craft 704`,
    `Retiree Holiday 704`, and the apprentice rows of `Union Dues 704` step down
    by class (see groundtruth pattern); source the per-class values from the
    notice/CBA apprentice fund schedule.
  - **Gotcha — first-year apprentice fringe drop:** Apprentice Class 1 has
    `Pension=0.00` and `SIS=0.00` (fringes drop for the lowest tier). Encode this
    rule from the CBA, do not infer it from groundtruth.

### 4c. pipe_fitters_537 (outlier — fully deterministic from text)
Sources: `cba/26.03.20 2025-2030 Green Book Clean Version.pdf`,
`cba/26.03.20 2025-2030 Yellow Book Clean Version.pdf` (both text-extractable;
the **Green/Yellow "Wage and Fringe Benefits — Boston Area" page 2/3** holds the
full package). `cba/2026.03.01.537 Rate Notice.pdf` is image-only (1 page) —
**not needed**; the books supply everything for the 3/1/2026 period.
- **Wage base & effective date:** Books state base Journeyman `$69.08` for
  9/1/25–2/28/26, then a stepped increase schedule. The output period is
  **3/1/2026–8/31/2026**, which adds the `$2.50` (9/1/25) + `$2.50` (3/1/26)
  wage increments → Building **Journeyman = $70.58**. Derive the 3/1/2026 wage by
  applying the documented increment schedule to the base; do not read it from GT.
- `Wage 1.1x = Wage×1.1`; `Wage 1.5x = Wage×1.5` (no 2.0x, no Wage Differential).
- **Temporary Heat = Wage×0.60** ("60% rate" in the books);
  `Temporary Heat 1.1x = Temporary Heat×1.1`;
  `Temporary Heat 1.5x = Temporary Heat×1.5`.
- **Foreman / zone differentials** (Yellow book §6):
  - Building `Foreman = Journeyman + $2.50`.
  - Power & Gas (over the **Building Foreman** base, i.e. J+2.50):
    `General Foreman = ×1.25`, `Area Foreman = ×1.15`, `Foreman = ×1.10`.
    (Verified: 70.58→Foreman 73.08; P&G GF 73.08×1.25=91.35,
    Area 73.08×1.15=84.042, Foreman 73.08×1.10=80.388.)
- **Apprentice scale = % of Journeyman wage** (Yellow §1 / Green p2):
  Yr1 40%, Yr2 45%, Yr3 60%, Yr4 70%, Yr5 80% (compute Wage, Wage 1.1x, 1.5x,
  Temporary Heat & its multiples from the scaled wage).
- **Flat fringes** (same every row; from books page 2/3):
  `Pension Local=14.00` (LU 537 Pension at 3/1/26 allocation),
  `Health & Welfare=13.95`, `Annuity=9.55`, `Industry Improvement=0.25`,
  `Education=2.17`, `Labor/Mgt. Trust Fund=2.20`, `Pension National=0.30`
  (UA National Pension), `Union Dues 537=0.93`, `Organizing Fund 537=0.15`,
  `C.O.P.E. 537=0.02`, `Public Relations=0.09`, `UA PAC=0.05`.
  > Confirm each value's 3/1/26 amount from the books before emitting — the
  > books also show a 9/1/25 column; use the column matching the output period.
  > If a fringe's 3/1/26 figure is not stated, blank+flag it.
- **Vacation 1–6 = 0,1,2,3,4,5** respectively (the six declared $-options, Green
  p2 footnote). Flat across all rows.
- **Gotcha — first-year apprentice drop:** `Apprentice Year 1` has
  `Pension Local=0` and `Annuity=0` ("1st year — UA National Pension only; all
  other fringe benefits will be paid"). Encode from the book footnote.

---

## 5. Run approach

- **Python via the project's `uv` env** (pyenv python is broken; system python
  lacks libs). Dependencies are declared in `pyproject.toml` / `uv.lock`, so no
  `--with` flags are needed:
  ```
  uv run python pipeline/run.py --union <name>
  uv run python pipeline/run.py --all
  ```
  Self-contained OCR (`pypdfium2` + `rapidocr-onnxruntime`, already in
  `pyproject.toml`) recovers the image-only 704 notice — no `tesseract`/system
  binary or API key required. If a fund's value is genuinely unreadable, blank+flag it.
- **Structure** (per DESIGN §C / "Files to create"):
  - `canonical/model.py`, `canonical/fields.yaml` — tidy model + field dictionary.
  - `profiles/{sprinkler_fitters_483,sprinkler_fitters_704,pipe_fitters_537}.yaml`
    — ordered columns, key columns, zone/class taxonomy, per-column source
    (canonical_field | multiplier | split | constant), `$` vs `%` formatting.
  - `pipeline/{ingest,extract,compute,pivot}.py` — reuse
    `extract/build_483.py:parse_rate_notice` + `r2()`; compute does the
    multipliers/splits/differentials with half-up rounding.
  - `pipeline/evaluate.py` — generalize `extract/compare_483.py`: header diff,
    key-based row alignment, cell accuracy ±0.01 (percent cells compared as
    percents), per-column + per-zone breakdown, mismatch list, gap report. Must
    read the **537 XLSX** groundtruth (openpyxl) as well as CSV groundtruths.
- **Self-check against `criteria.md` thresholds:** header exact (≥9), ≥98% cell
  accuracy on documented cells (≥9), every GT row once (≥9), provenance present
  (≥7), re-runnable read-only pipeline (≥6).
- **Regression gate:** 483 Building zone must stay **100%** and report the same
  residential gaps as `extract/build_483.py`.

---

## Scope summary

- **3 unions:** `sprinkler_fitters_483` (regression, reuse `build_483.py`),
  `sprinkler_fitters_704` (Building-only sibling), `pipe_fitters_537` (outlier).
- **Columns / rows per union:** 483 = 25 cols × 21 rows; 704 = 21 cols × 13 rows
  (Building only); 537 = 31 cols × 10 rows (Power & Gas + Building, `Year N`
  apprentices, no Wage Differential/2.0x, Temporary Heat + 6 vacation tiers).
- **Primary sources:** 483 → Rate Notice PDF (clean table) + CBA; 704 → CBA
  (text) + **image-only Rate Notice (OCR/LLM boundary)**; 537 → Green/Yellow
  books page 2/3 (fully deterministic text), Rate Notice not needed.
- **Deterministic-first:** all 537 + 483 Building cells deterministic; 704
  notice-sourced cells need OCR — blank+flag if OCR/LLM unavailable.
- **Outputs:** `data/<union>/ai_output/<groundtruth-base-name>.csv` +
  `<union>.gaps.md`; read-only on `cba/`/`ratesheet/`; values never copied from
  groundtruth; half-up rounding throughout.
