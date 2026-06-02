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
