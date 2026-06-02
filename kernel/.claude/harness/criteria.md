# Grading criteria — CBA → ratesheet extraction

Both the **builder** (to self-check) and the **evaluator** (to grade) read this
file. This is the most important file to tune — the wording here steers output
more than anything else.

The task: for one union, extract wage and fringe-benefit rates from the
collective bargaining agreement documents in `data/<union>/cba/` and produce a
CSV ratesheet in `data/<union>/ai_output/` that reproduces the human-made
**groundtruth** ratesheet in `data/<union>/ratesheet/`.

Each criterion has a 1–10 score and a hard threshold. If **any** criterion falls
below its threshold, the work FAILS and goes back to the builder with specific,
cell-addressable feedback.

The groundtruth ratesheet is the source of truth. **Value accuracy** and
**column fidelity** matter most — a ratesheet that looks plausible but has wrong
numbers is worthless.

---

### 1. Column fidelity  — threshold: 9
The generated CSV header **exactly matches** the groundtruth header: same column
names, same order, no missing columns, no extra columns. Header text must match
character-for-character (e.g. `Health & Welfare`, `Wage 1.5x`, `Union Dues 483`).

### 2. Value accuracy  — threshold: 9
After aligning rows by their key columns, **≥98% of data cells match** the
groundtruth. Numeric cells match within ±0.01 tolerance; percentage cells
(e.g. `6.00%`) are compared as percentages with the same formatting; blank cells
must match blank. Score scales with measured cell accuracy — this is the core
metric. Report the exact accuracy % and the worst mismatching cells.

### 3. Row completeness  — threshold: 9
Every groundtruth row is present in the output exactly once and no extras. Rows
are identified by their key columns: `Zone`, `Package` (classification), the
indenture-date columns where present, and `Start/End Date`. No missing
classifications, no missing zones, no duplicate rows, no stray rows.

### 4. Traceability / sourcing  — threshold: 7
Values are genuinely **extracted from the CBA documents**, not copied from the
groundtruth. The pipeline is auditable: a human can see which source document and
location each rate came from (e.g. notes, a provenance column, or a documented
mapping). Fabricated or guessed values are a hard fail for this criterion.

### 5. Code quality  — threshold: 6
The pipeline is readable, deterministic, and **re-runnable** from scratch. It
reads only from `cba/` (and may read the groundtruth header for column
names/order), writes only to `ai_output/`, and never modifies `cba/` or
`ratesheet/`. Sensible structure and naming; no secrets committed.

---

## Hard rules (never violate)
- **Read-only** on every `cba/` and `ratesheet/` folder. The only writable
  location is `data/<union>/ai_output/`.
- **Never edit the groundtruth** to make a comparison pass.
- **Never populate output cells by reading groundtruth values.** The header may
  be read to match column names/order; values must come from the CBA documents.
  If a value cannot be found in the CBA, leave it blank and report it — do not
  fabricate.

## Calibration note (read before scoring)
Scoring is **mechanical**, not vibes — compute the actual header diff, row
alignment, and cell-accuracy % by loading both files, and report the numbers. A
10 means a near-perfect reproduction verified by computation. Assume the
generated output is wrong until the computed comparison proves otherwise. Be
specific: cite the row key, column, expected value, and produced value for every
mismatch you report.
