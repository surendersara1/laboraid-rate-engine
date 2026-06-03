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

Run the whole prototype (all three unions) and print the evaluation:

```bash
uv run python pipeline/run.py --all
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
| `--union <name>` | Run one union. Valid: `sprinkler_fitters_483`, `sprinkler_fitters_704`, `pipe_fitters_537`. |
| `--all` | Run every configured union. |
| `--no-eval` | Generate the ratesheet but skip the groundtruth comparison. |

### Outputs (written per union under `data/<union>/ai_output/`)

- `<groundtruth base name>.csv` — the generated ratesheet (always CSV, with the
  groundtruth's exact header).
- `<union>.gaps.md` — every blank/divergent cell with its row key, column, and
  reason.

Evaluation (header diff, per-column and per-zone accuracy, mismatch list) prints
to the console unless `--no-eval` is passed.

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

That's why 537 sits at 67.4% rather than 100% — the 3/1/2026 fund reallocation
isn't stated in the books the kernel has access to. A human SME could put a
value there from external knowledge; the kernel refuses to, and surfaces the
absence in the gaps report instead. The AWS-side **Business UI's review queue**
is where humans see those gaps and fill them.

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
  extract.py   # per-union extraction -> canonical rows (+ provenance)
  compute.py   # deterministic derived columns (multipliers, splits, rounding)
  pivot.py     # canonical rows -> union ratesheet CSV
  evaluate.py  # compare output vs groundtruth (cell accuracy ±0.01)
  run.py       # entrypoint (--union / --all / --no-eval)

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

---

## Current results (3-union prototype)

| Union | Cell accuracy | Notes |
|---|---|---|
| `sprinkler_fitters_704` | **99.6%** | Image-only notice recovered via OCR. |
| `sprinkler_fitters_483` | Building **100%**, overall 83.2% | Residential apprentice scale + 1/1/26 reallocation absent from docs. |
| `pipe_fitters_537` | 67.4% | Structure fully handled; the 3/1/26 fund reallocation is not stated in the books. |

Sub-100% cells are **confirmed-absent source data** (listed in each `gaps.md`),
not pipeline errors — every produced value is document-sourced and verified.

### Known data limitations (need a document, not a code change)
- **537** — the 3/1/2026 fund allocation isn't in the Green/Yellow books (they
  defer increases to "wages until allocated"). Supply the 3/1/2026 allocation
  notice to close it.
- **483 residential** — the residential apprentice wage/fund schedule and the
  1/1/2026 reallocation aren't in the provided docs.

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
