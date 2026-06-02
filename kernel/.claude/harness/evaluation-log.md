
## Iteration 1 — 2026-05-29 12:19:58 EDT — VERDICT: FAIL

Per-criterion (threshold): Column fidelity 10 (9) PASS · Value accuracy 3 (9) FAIL · Row completeness 10 (9) PASS · Traceability 9 (7) PASS · Code quality 9 (6) PASS.

Measured cell accuracy (aligned cells, ±0.01, 0 fabricated values anywhere):
- 483: 367/441 = 83.2% — **Building 294/294 = 100.0%** (regression preserved); Residential 73/147 = 49.7% (blanks).
- 537: 182/270 = 67.4% — 88-cell miss = undocumented 3/1/2026 fund reallocation (GT figures 70.58/14.00/13.95/9.55 confirmed absent from both Green/Yellow books; document-faithful answer is +2.50→wages=71.58, which the builder emitted).
- 704: 39/260 = 15.0% — Rate Notice is image-only (0 text, 0 tables, 12 images); no tesseract/ocrmypdf in env; 182 notice cells blank+flagged per spec §4b.

Root cause: data-availability limits, NOT pipeline bugs. Header exact (3/3), all rows present once (3/3), read-only respected (mtimes unchanged), provenance recorded, no GT values copied.

Actionable next-iteration items:
- (data limit) 704 needs OCR/LLM — unavailable in this env.
- (data limit) 537 needs a sourced 3/1/26 allocation doc — not in books.
- (coverage opportunity, code) 483 residential apprentice rows blanked; if a residential apprentice fund/wage schedule exists elsewhere in the 483 CBA, mine the genuinely-documented flat funds (no GT reverse-engineering).

## Iteration 2 — 2026-05-29 12:38:04 EDT — VERDICT: FAIL (strict global-98%) · GOAL MET

Self-contained OCR stage added (pipeline/ocr.py: rapidocr-onnxruntime + pypdfium2 — no system binary, no API key).

Per-union measured cell accuracy (independently recomputed; read-only respected, 0 fabricated values, no GT copies, no regressions):
- 704: **99.6%** (259/260; value-only 220/221) — **meets ≥98% value-accuracy threshold.** OCR-recovered numbers verified vs notice images + CBA prose; Wage×1.15/1.5/2.0 internally consistent (0 corruptions). Single miss = Apprentice Class 10 'S & E 704' (notice literally prints .17; GT 0.20) — document value emitted + flagged. 15.0% → 99.6%.
- 483: 83.2% overall; **Building 294/294 = 100.0% (regression HELD)**. Residential 49.7% — blanks are the confirmed-absent apprentice scale + 1/1/26 reallocation.
- 537: 67.4% UNCHANGED — 88-cell miss = 3/1/26 reallocation; GT figures 70.58/14.00/13.95/9.55 grep to ZERO hits in both books; doc figures 69.08/13.75/13.45/9.30 appear once each. Genuine data limit.

Per-criterion: Column fidelity 10 · Value accuracy (704 PASS / 483,537 sub-threshold due to absent data) · Row completeness 10 · Traceability 9–10 · Code quality 9. All non-value criteria PASS across all 3 unions.

LOOP STOPPED at iteration 2 (not 4): remaining sub-98% cells are confirmed-absent source data (537 reallocation, 483 residential schedule) — unfixable by code without fabricating or external docs. Goal (prove generic pipeline: outlier handled, regression held, 704 unblocked) achieved.
