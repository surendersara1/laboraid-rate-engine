# Labor Aid — CBA → Ratesheet Pipeline

Turn a construction union's **Collective Bargaining Agreement (CBA) documents**
into a structured wage-and-fringe **ratesheet**, and grade the result against the
human-made groundtruth. The pipeline reads only the source PDFs, derives every
value from them (never copies the groundtruth), and flags anything the documents
don't contain rather than fabricating it.

See [`DESIGN.md`](DESIGN.md) for the architecture and rationale.

---

## What it does

For each union, the pipeline:
1. **Ingests** the CBA / rate-notice / wage-sheet PDFs (with OCR for image-only
   pages).
2. **Extracts** wages and fringe contributions into a canonical model, with
   per-cell provenance (which document + location each value came from).
3. **Computes** derived columns deterministically (shift differentials, ×1.5/×2.0
   multipliers, fund splits, foreman/apprentice scales) with half-up rounding.
4. **Pivots** the canonical data into that union's exact ratesheet schema.
5. **Evaluates** the output against the groundtruth (header diff, row alignment,
   cell accuracy ±0.01, per-column / per-zone breakdown).

**Hard guarantees:** the `cba/` and `ratesheet/` folders are **read-only**; all
output goes only to `data/<union>/ai_output/`; no groundtruth *values* are ever
copied into output (only the header is read, to match column names/order);
unsourceable cells are left blank and itemized in a gaps report.

---

## Prerequisites

- **[`uv`](https://docs.astral.sh/uv/)** — the only requirement. Dependencies are
  declared in [`pyproject.toml`](pyproject.toml) and pinned in `uv.lock`; `uv`
  creates and manages the project environment automatically. (The repo
  deliberately does *not* rely on the system/pyenv Python.)
- No Anthropic API key needed. No `tesseract`/system binaries needed — OCR runs
  through a self-contained ONNX engine declared as a dependency.

One-time setup (optional — `uv run` does it for you on first use):
```bash
uv --version   # verify uv is installed
uv sync        # create .venv and install pinned dependencies
```

---

## Quick start

Run the whole prototype (all five unions) and print the evaluation + gate:

```bash
uv run python pipeline/run.py --all --min-accuracy 99.0
```

Run a single union:

```bash
uv run python pipeline/run.py --union sprinkler_fitters_704
```

> `uv run` resolves the project's `pyproject.toml`/`uv.lock` and runs inside the
> managed environment — no `--with` flags needed. The OCR libraries
> (`pypdfium2`, `rapidocr-onnxruntime`, `Pillow`) are included for unions whose
> rate notice is image-only (currently **704**).

### CLI flags (`pipeline/run.py`)

| Flag | Effect |
|---|---|
| `--union <name>` | Run one union. Valid: `sprinkler_fitters_281`, `sprinkler_fitters_483`, `sprinkler_fitters_704`, `sprinkler_fitters_821`, `pipe_fitters_537`. |
| `--all` | Run every configured union (all 5). |
| `--no-eval` | Generate the ratesheet but skip the groundtruth comparison. |
| `--no-critic` | Skip the advisory completeness-coverage critic (Stage 6). |
| `--min-accuracy <pct>` | Gate: exit non-zero if any union's **sourced** accuracy (excl. flagged-gap blanks) is below `<pct>`, or its header doesn't match. CI runs `--all --min-accuracy 99.0`. |

### Outputs (written per union under `data/<union>/ai_output/`)

- `<groundtruth base name>.csv` — the generated ratesheet (always CSV, with the
  groundtruth's exact header).
- `<union>.gaps.md` — every blank/divergent cell with its row key, column, and
  reason.
- `<union>.coverage.md` — the completeness critic's advisory list of CBA-mentioned
  classifications/zones/funds not found in the output (unless `--no-critic`).

Evaluation (header diff, per-column and per-zone accuracy, mismatch list) prints
to the console unless `--no-eval` is passed; with `--min-accuracy` it also gates.

---

## How the pipeline works (educational walkthrough)

`pipeline/run.py` is the only file you actually invoke from the CLI. Everything
else in `pipeline/` is a **library** that `run.py` imports and orchestrates. When
you ran `uv run python pipeline/run.py --union sprinkler_fitters_704`, every
module in `pipeline/` executed behind the scenes — you just didn't see them
called by name.

### The conductor

`run.py` is ~95 lines and does exactly four things:

```python
profile = load_profile(union)                                  # read profiles/<union>.yaml
rows, gaps = extract.EXTRACTORS[union](union_dir)              # PDF -> canonical rows
n          = pivot.write_csv(profile, rows, out_csv)           # canonical -> wide CSV
              evaluate.evaluate(gt, out_csv)                   # compare vs groundtruth
```

That's the whole pipeline. The interesting work happens inside the functions it
calls.

### What each module does and when it fires

| File | Role | Where it fires during your run |
|---|---|---|
| **`run.py`** | CLI + orchestrator | You called it directly |
| **`extract.py`** | Per-union extractors (`extract_704`, `extract_483`, `extract_537`). Reads PDFs and returns canonical `ClassificationRow` objects + a `gaps` list for anything it couldn't read. | Called once per union via `extract.EXTRACTORS[union](union_dir)` |
| **`ingest.py`** | Lower-level PDF I/O. Opens a PDF, detects whether each page has a text layer or is image-only, returns pages as text or image objects. | Called *inside* `extract_704()` (and the other extractors) whenever they open a PDF |
| **`ocr.py`** | Self-contained OCR using `rapidocr-onnxruntime` + `pypdfium2`. No system binaries, no API key. | Called by `ingest.py` when a page has no text layer (e.g., the 704 rate notice is image-only) |
| **`compute.py`** | Applies the profile's **derived-column rules** — e.g., `Wage 1.5x = Wage × 1.5`, P&G multipliers, apprentice scales — using `Decimal.ROUND_HALF_UP` via the kernel's `r2()` helper. | Called by the extractors after raw values are read, before returning rows |
| **`pivot.py`** | Takes the tidy canonical rows + the profile's `output_schema` and writes a **wide CSV** matching the groundtruth's exact column order and names. | Called once per union: `pivot.write_csv(profile, rows, out_csv)` |
| **`evaluate.py`** | Post-hoc comparison: header diff, key-based row alignment, per-cell accuracy (±$0.01), per-column + per-zone breakdown, mismatch list. **Never used at production runtime** — only for development against a groundtruth. | Called by `run.py` unless you pass `--no-eval` |
| **critic.py** | Stage 6 — advisory completeness check. Scans the union's CBA/notice text for the *vocabulary* of a ratesheet (classifications, zones, fund names) and flags any missing from the output. Writes `<union>.coverage.md`. Never gates; catches *missing breadth* (the failure mode value-accuracy can't see). | Called by `run.py` unless you pass `--no-critic` |
| **`__init__.py`** | Marks `pipeline/` as a Python package. | Implicit on every import |

### The data flow (one chart)

```
                profiles/<union>.yaml
                    │ (column list, derivation rules, source map)
                    ▼
data/<union>/cba/*.pdf ──► ingest.py ──► ocr.py (if image-only)
                                 │
                                 ▼
                          extract.py (extract_704, _483, _537)
                                 │
                                 ▼  rows: List[ClassificationRow]
                            compute.py (multipliers, splits, rounding)
                                 │
                                 ▼  rows: List[ClassificationRow] (with derived fields)
                              pivot.py
                                 │
                                 ▼  CSV header + 13 rows
                       data/<union>/ai_output/*.csv
                                 │
                                 ▼
                            evaluate.py (vs data/<union>/ratesheet/*.csv)
                                 │
                                 ▼
                       console output (per-column, per-zone, mismatches)
                       data/<union>/ai_output/<union>.gaps.md
```

### Worked example: trace one cell

Take the **Wage 1.5x** column for the **Building / Journeyman** row in the 704
output (the value you'll see is `$82.05`). Here is exactly where each step
happens in code:

1. **`ingest.py`** opens `data/sprinkler_fitters_704/cba/2026.01.01.704 Rate Notice.pdf`,
   detects it's image-only, and routes pages through `ocr.py`.
2. **`ocr.py`** extracts text from each page — including the "Wage: $54.70"
   table cell for Journeyman.
3. **`extract_704()` in `extract.py`** locates that wage value, builds a
   `RateCell(value=54.70, source_doc="2026.01.01.704 Rate Notice.pdf",
   source_locator="page 2 / table 1 / row 3", confidence=0.95)` and attaches it
   to a `ClassificationRow(zone="Building", package="Journeyman")`.
4. **`compute.py`** reads `profiles/sprinkler_fitters_704.yaml` and finds
   `Wage 1.5x: { multiplier_of: Wage, factor: 1.5 }`. It computes
   `r2(54.70 * 1.5) = r2(82.05) = 82.05` (half-up rounding kicks in when there
   are sub-cent values).
5. **`pivot.py`** reads the profile's `output_schema` column order and writes
   `82.05` into the `Wage 1.5x` column of the output CSV.
6. **`evaluate.py`** reads `data/sprinkler_fitters_704/ratesheet/...csv`, locates
   the same `Building/Journeyman/Wage 1.5x` cell, sees `82.05` matches `82.05`,
   counts it as ✓, and reports `Wage 1.5x: 13/13` in the per-column block.

Any cell that fails this trace ends up either (a) reported as a mismatch by
`evaluate.py`, or (b) blanked + listed in `gaps.md` because the rule was
"document doesn't say, refuse to fabricate". The 99.6% accuracy line you see
means 259 of 260 cells trace cleanly through these six steps.

### Recommended read order

If you want to learn the kernel by reading code, go in dependency order — each
file builds on the previous:

```
1.  canonical/model.py                # RateCell + ClassificationRow + r2()
2.  canonical/fields.yaml             # the shared field dictionary
3.  profiles/sprinkler_fitters_704.yaml  # one declarative output schema
4.  pipeline/ingest.py                # PDF reading
5.  pipeline/ocr.py                   # OCR fallback (only if you care about scanned PDFs)
6.  pipeline/extract.py               # focus on extract_704() only
7.  pipeline/compute.py               # derived columns
8.  pipeline/pivot.py                 # canonical -> wide CSV
9.  pipeline/evaluate.py              # scoring
10. pipeline/run.py                   # the conductor (you've already seen this one)
```

### How to "run" individual modules

The library modules don't have CLIs — only `run.py` has an `if __name__ == "__main__":`
block. To see the others in action, use the Python REPL:

```bash
cd kernel
uv run python
```

Then in the REPL:

```python
>>> from pipeline import extract
>>> rows, gaps = extract.extract_704("data/sprinkler_fitters_704")
>>> len(rows)
13
>>> rows[0].zone, rows[0].package         # ('Building', 'Foreman'), most likely
>>> rows[0].cells["wage"]                 # inspect one RateCell — value + provenance
>>> gaps                                  # what couldn't it read (and why)?
```

That's the most concrete way to see the kernel's internals — call its functions
yourself and inspect the data structures. The same trick works for `ingest`,
`compute`, `pivot`, and `evaluate`.

### The "never fabricate" rule, made concrete

Every value in the output CSV must trace back to a specific source document at a
specific location. If a cell can't be sourced, the kernel writes nothing in
that cell and adds an entry to `<union>.gaps.md` explaining why. **Never** does
the kernel:

- Read a value out of the groundtruth ratesheet and write it to the output (the
  groundtruth is read-only and only consulted for column names + scoring)
- Interpolate or guess a value because "it's probably reasonable"
- Copy a value from another union or another period

When a value genuinely isn't in any provided document (e.g. 483's residential
apprentice scale), the kernel blanks it and lists it in the gaps report rather
than guessing — a human SME fills it from external knowledge via the AWS-side
**Business UI review queue**. (537 used to sit at 67.4% under this rule because the
wage was derived from the Green/Yellow books; it now reproduces at 100% by reading
the period's authoritative Rate Notice instead — the right fix was a better source,
not a fabricated value.)

---

## Repository layout

```
data/<union>/
  cba/         # source CBA + rate-notice + wage-sheet PDFs   (READ-ONLY)
  ratesheet/   # human-made groundtruth ratesheet (CSV/XLSX)  (READ-ONLY)
  ai_output/   # generated ratesheet + gaps report            (written here)

canonical/
  model.py       # tidy intermediate model + half-up r2() rounding
  fields.yaml    # canonical field dictionary (union names -> shared concepts)

profiles/
  <union>.yaml   # declarative output schema: ordered columns, key columns,
                 # zone/class taxonomy, per-column source + $/% formatting

pipeline/
  ingest.py    # locate + read source PDFs
  ocr.py       # self-contained OCR (rapidocr-onnxruntime + pypdfium2)
  extract.py   # per-union extraction -> canonical rows (+ provenance); 5 unions
  compute.py   # deterministic derived columns (rmul Decimal multiply, splits)
  pivot.py     # canonical rows -> union ratesheet CSV (order: preserve for cohorts)
  evaluate.py  # compare output vs groundtruth (cell accuracy ±0.01, indenture-aware key)
  critic.py    # Stage 6 advisory completeness/coverage critic
  run.py       # entrypoint (--union / --all / --no-eval / --no-critic / --min-accuracy)

extract/
  build_483.py    # original proven 483 extractor (reference)
  compare_483.py  # original standalone comparator (reference)

.claude/           # the build harness (planner / builder / evaluator) — see below
DESIGN.md          # architecture
```

---

## Adding a new union

1. Drop its documents in `data/<new_union>/cba/` and its groundtruth in
   `data/<new_union>/ratesheet/`.
2. Author `profiles/<new_union>.yaml` — copy an existing profile and edit the
   ordered column list (verbatim from the groundtruth header), key columns, the
   zone/classification taxonomy, and each column's source mapping.
3. Register it in `pipeline/run.py` (`TARGETS` + `GT_EXT`) and add an extractor in
   `pipeline/extract.py` (`EXTRACTORS[<new_union>]`).
4. Run `--union <new_union>` and iterate against the printed evaluation.

If the local has **apprentice indenture cohorts** (281, 821) — the same
classification repeated for different indenture dates — set the row's
`indenture_before` / `indenture_after`, list the `Indentured Date is Before/After`
columns in the profile, and add `order: preserve` so the cohorts stay grouped
(see `profiles/sprinkler_fitters_281.yaml` and `extract_281`). Derived multiplier
columns use `canonical.model.rmul` (Decimal multiply, half-up) — never
`r2(base * factor)`, which rounds the `.x5` boundary the wrong way.

---

## Current results (5-union coverage)

Measured by `--all --min-accuracy 99.0` on **sourced** cells (intentional
flagged-gap blanks excluded):

| Union | Sourced accuracy | Notes |
|---|---|---|
| `pipe_fitters_537` | **100%** (270/270) | Wage + fringes now sourced from the 2026.03.01 Rate Notice (was a wrong book derivation). |
| `sprinkler_fitters_281` | **100%** (240/240) | Two apprentice indenture cohorts. |
| `sprinkler_fitters_704` | **99.6%** (259/260) | Image-only notice via OCR; 1 documented doc-vs-GT divergence. |
| `sprinkler_fitters_821` | **99.7%** (1068/1071) | 4 zones, 2 cohorts, Foreman variants, Production/Trainee/Residential. 3 diffs = a flagged GT anomaly (Industrial pre-2017 pension), not replicated. |
| `sprinkler_fitters_483` | **100% on sourced cells** | 74 intentional blanks (residential scale absent from docs). |

Sub-100% cells are **confirmed-absent source data or documented GT divergences**
(listed in each `gaps.md`), not pipeline errors — every produced value is
document-sourced and verified. The earlier "537 = 67.4%" figure is obsolete: it
came from deriving the wage from the Green/Yellow books; reading the Rate Notice
(which states the period's actual allocation) makes 537 reproduce exactly.

### Known data limitations (need a document, not a code change)
- **483 residential** — the residential apprentice wage/fund schedule and the
  1/1/2026 reallocation aren't in the provided docs (left blank, flagged).
- **821** — only the Industrial pre-2017 apprentice cohort shows pension in years
  1-3 in the groundtruth, contrary to the CBA and to the other zones; emitted
  CBA-correct 0.00 and flagged as a GT anomaly rather than replicated.

---

## The build harness (optional)

This repo also ships a generator/evaluator harness used to *build and improve*
the pipeline itself: a `planner` writes a spec, a `builder` writes code, and a
skeptical `evaluator` grades the output against
[`.claude/harness/criteria.md`](.claude/harness/criteria.md). In Claude Code:

```
/harness generate the ratesheet for <union>
```

It plans, pauses for spec approval, then loops build → evaluate until it passes
or hits the iteration cap. Run artifacts land in `.claude/harness/`
(`spec.md`, `build-notes.md`, `evaluation-log.md`). The harness is for
*development*; day-to-day ratesheet generation just uses `pipeline/run.py` above.

---

## Troubleshooting

- **`ModuleNotFoundError` / library load errors** — you're not using `uv`. Always
  invoke through `uv run python …` (or run `uv sync` first); don't call the
  system Python directly.
- **First OCR run is slow** — `rapidocr-onnxruntime` downloads its model once,
  then caches it. Subsequent runs are fast.
- **A union shows new blanks** — check its `ai_output/<union>.gaps.md`; the
  pipeline blanks (never guesses) when a value isn't in the documents.
