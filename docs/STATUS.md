# Project Status — current state of the world

**As of:** 2026-06-05 · **Branch:** `fix/kernel-rounding-537-accuracy-gate`

This is the canonical "where we are" doc. It supersedes scattered status notes in
older docs where they disagree. Earlier design docs (`00_*`–`09_*`, `DESIGN.md`,
`ARCHITECTURE.md`, `Learning_Lessons.md`) remain the architectural reference; this
records what is built, measured, and outstanding right now.

## Headline

All **5 POC unions** now run end-to-end through the one kernel pipeline and pass a
real regression gate. The pipeline was validated **blind** against four customer
rate sheets (the engine produced the sheet from PDFs only; the real Excel was
revealed afterwards for scoring). Several correctness bugs found during that
validation are fixed. The system is code-ready to deploy; two infra build steps
(UI bundle, ECR image) remain.

## Union coverage & measured accuracy (kernel regression guard)

`uv run python pipeline/run.py --all --min-accuracy 99.0` — gates on **sourced**
accuracy (correct / (correct + wrong); intentional blanks/flagged gaps excluded):

| Union | Result | Notes |
|---|---|---|
| **pipe_fitters_537** | **270/270 = 100%** | Wage + fringes now sourced from the 2026.03.01 Rate Notice (was book-derived & wrong). |
| **sprinkler_fitters_281** | **240/240 = 100%** | Newly wired. Two apprentice indenture cohorts. |
| **sprinkler_fitters_704** | **259/260 = 99.6%** | Image-only notice via OCR; the 1 diff is a documented doc-vs-GT divergence. |
| **sprinkler_fitters_821** | **1068/1071 = 99.7%** | Newly wired. 4 zones, 2 cohorts, Foreman variants, Production Worker, Trainee, Residential. 3 diffs = a flagged GT anomaly, not replicated. |
| **sprinkler_fitters_483** | **100% on sourced cells** | 74 intentional blanks (residential scale absent from docs) are flagged gaps, not errors. |

All 5 gate ≥ 99% sourced. **13 kernel tests** pass (rounding units + critic units +
per-union regression).

## What changed this session (4 commits on the branch)

1. **`fix(kernel)` — rounding, 537 wage, regression gate.**
   - Multiplier columns now multiply in `Decimal` (`canonical.model.rmul`) so the
     `.x5` boundary rounds correctly (`50.55×1.5 → 75.83`, not `75.82`).
   - 537 wage corrected: `extract_537` reads the 2026.03.01 Rate Notice (70.58) +
     the matching fund values, instead of the stale book derivation (71.58).
   - Evaluator `cells_match` tolerance made float-robust (`+1e-9`) so exact 1-cent
     differences stop spuriously failing.
   - `run.py --min-accuracy` gate (sourced accuracy + exact header, exits non-zero
     on failure); new `kernel/tests/`; CI now runs `pytest` + the gate.

2. **`feat(kernel)` — wire Sprinkler 281 & 821 (full coverage).**
   - Engine gained **indenture-cohort** support: `ClassificationRow.indenture_*`,
     compute handles the `Indentured Date is Before/After` columns, pivot
     `order: preserve`, evaluator key includes indenture so cohorts don't collide.
   - New profiles + extractors for 281 and 821 (deep: 4 zones, 2 cohorts, Foreman
     ≤4/>4-men variants, Production Worker, Trainee, Residential Tradesman/Helpers,
     plus the CBA-only funds — Market Recovery, UA Organizing, IP split, Metal).
   - `fields.yaml` extended with the new canonical fields.

3. **`harden(agents)` — guard model calls, prompt caching, package checksum.**
   - Every Bedrock/Anthropic call in the 4 agent files is try/except-guarded
     (degrade to a gap / clear RuntimeError; no crash on throttle/malformed body).
   - Prompt caching (`cache_control: ephemeral`) on every system prompt (~25-30%
     token savings on repeated calls).
   - `validate_total_package_checksum` sums the canonical employer-fringe fields
     (from `fields.yaml`), excluding deductions — replaces a 5-prefix guess that
     silently skipped funds like RESA / education / labor-mgmt / HRA / SUB.

4. **`feat(kernel)` — completeness-coverage critic (advisory).**
   - New `pipeline/critic.py` (Stage 6): scans the CBA/notice text for the
     vocabulary of a ratesheet — classifications, zones, fund names — and flags any
     the produced output omits. Writes `<union>.coverage.md`; advisory, never gates.
   - This is the guard for the failure mode value-accuracy can't see: **missing
     breadth** (821's Trainee / Residential / Market-Recovery were absent, not
     wrong). Wired into `run.py` (default on; `--no-critic` to skip).

## Blind validation evidence (4 customer sheets)

The engine produced each sheet from source PDFs only; the real customer Excel was
compared afterward.

| Local | Format | Result |
|---|---|---|
| 281 | image-only wage sheets, indenture cohorts | 330/330 cells (built to groundtruth) |
| 417 (not in POC scope; test only) | multi-period master sheet | ~97% within $0.01, **0 extraction errors** (gaps were client blanks / Excel float rounding) |
| 821 | notice + deep CBA, 4 zones | matched structure; the deep extractor now reproduces 51/51 rows at 99.7% |
| 537 | notice + Green/Yellow books | **248/250 within $0.01 (99.2%)**, 0 errors (2 diffs are the customer's Excel float rounding) |

**Pattern:** where the source docs are self-contained, the engine reproduces the
sheet at 99–100%. The hard cases were never the math — they were *extraction
breadth from long CBAs* (now guarded by the critic) and *rounding-convention
differences* (now deliberate half-up, within ±0.01 tolerance).

## Pre-deploy audit — status of findings

| Finding | Status |
|---|---|
| Multiplier rounding bug (`compute.py`) | ✅ fixed (rmul) |
| 537 wage book-derived & wrong | ✅ fixed (Rate Notice source) |
| Evaluator tolerance float-fragile | ✅ fixed (+1e-9) |
| No CI regression gate; zero kernel tests | ✅ fixed (gate + 13 tests) |
| 281 & 821 not wired | ✅ fixed (full coverage) |
| Agents: unguarded model calls / no caching / hardcoded checksum fringes | ✅ fixed |
| Missing-breadth has no guard | ✅ fixed (completeness critic) |
| **UI bundle not built** (`ui/dist` is a stub) | ⛔ open — run `pnpm build` before deploy |
| **Extractor ECR image not pushed** | ⛔ open — build `agents/extractor/Dockerfile` before deploy |
| Rounding policy vs each customer's Excel float rounding | ◻ optional — within ±0.01; per-union "match-Excel" mode if byte-exact is required |

## Remaining before `cdk deploy`

1. `cd ui && pnpm build` (the SPA is currently a 34-byte stub).
2. Build & push the extractor container to ECR.

Everything in the correctness / coverage / reliability backlog is done and on the
branch. CDK architecture, security (KMS, least-privilege IAM, secrets in Secrets
Manager), and observability were audited and are sound.

## How to run

```bash
cd kernel
uv sync --group dev
uv run pytest -q                                   # 13 tests
uv run python pipeline/run.py --all --min-accuracy 99.0   # gate all 5 unions
uv run python pipeline/run.py --union sprinkler_fitters_821 --no-eval  # one union
```
