# Build notes - CBA -> ratesheet pipeline

## Iteration 1 - 2026-05-29 - targets: sprinkler_fitters_483, sprinkler_fitters_704, pipe_fitters_537

### Regenerate commands (exact)
```
# all three unions (writes CSV + <union>.gaps.md, then self-evaluates):
uv run python pipeline/run.py --all

# single union:
uv run python pipeline/run.py --union sprinkler_fitters_483
uv run python pipeline/run.py --union sprinkler_fitters_704
uv run python pipeline/run.py --union pipe_fitters_537

# standalone evaluate (CSV or XLSX groundtruth):
uv run python pipeline/evaluate.py "<groundtruth>" "<output.csv>"
```
Output paths written:
- `data/sprinkler_fitters_483/ai_output/2026.01.01.483 Rate Sheet.csv` + `sprinkler_fitters_483.gaps.md`
- `data/sprinkler_fitters_704/ai_output/2026.01.01.704 Rate Sheet.csv` + `sprinkler_fitters_704.gaps.md`
- `data/pipe_fitters_537/ai_output/2026.03.01.537 Rate Sheet.csv` + `pipe_fitters_537.gaps.md`

### What was built (DESIGN/spec structure, stages B-D + evaluate)
- `canonical/model.py` (RateCell, ClassificationRow tidy model, `r2()` half-up rounding reused from build_483) + `canonical/fields.yaml` (canonical field -> union output labels).
- `profiles/{sprinkler_fitters_483,sprinkler_fitters_704,pipe_fitters_537}.yaml` - ordered columns (char-for-char names), constants, key columns, `$`/`%` formatting, multiplier specs (Wage Differential x1.15, 1.5x, 2.0x, 1.1x, Temporary Heat multiples).
- `pipeline/ingest.py` (pdfplumber text/tables, image-only detection), `pipeline/extract.py` (per-union deterministic extractors -> canonical rows + gap list; 483 ports build_483's `parse_rate_notice` verbatim), `pipeline/compute.py` (multipliers + explicit-override, half-up), `pipeline/pivot.py` (wide CSV writer), `pipeline/evaluate.py` (header diff, key-aligned cell accuracy +/-0.01, per-zone/per-col, reads CSV + 537 XLSX; date-normalizes Start/End keys), `pipeline/run.py --union/--all`.

### Status by union / zone (MEASURED cell accuracy from self-check)
- **sprinkler_fitters_483** - header exact; 21/21 rows; overall **83.2%** (367/441), **0 wrong cells**, 74 blanks.
  - **Building zone: 294/294 = 100.0%** (regression vs build_483 preserved; diff confirms Building rows identical).
  - Residential: 73/147 = 49.7% - documented funds correct (H&W, RESA, H&W Metal, SIS, UA Intl Trng, Ind Promotion Nat, J&A/Local 483 Trng, NCFPCG, Bay Area IP, HRA, dues) for Foreman+Journeyman; apprentice scale + 1/1/2026 Pension/Vacation reallocation blank+flagged (see gaps). Fixed a bug vs build_483: residential Wage Differential now = Wage (no x1.15 uplift), matching the docs.
- **pipe_fitters_537** - header exact; 10/10 rows; overall **67.4%** (182/270), 88 divergent (0 blank).
  - Fully-correct columns (10/10 each): Industry Improvement, Education, Labor/Mgt. Trust Fund, Pension National, Union Dues 537, Organizing Fund 537, C.O.P.E. 537, Vacation 1-6 537, Public Relations, UA PAC, and all key/constant columns. Structure correct: Building Foreman = J+2.50; P&G GF/Area/Foreman = Building-Foreman x1.25/1.15/1.10; apprentice Yr1-5 = 40/45/60/70/80% of J; Temporary Heat = Wage x0.60; all multipliers; Year-1 apprentice Pension/Annuity = 0 (page-2 footnote).
  - Divergent (Wage + its multiples, Temporary Heat + its multiples, Pension Local, Health & Welfare, Annuity): the Green/Yellow books show ONLY the 9/1/25 fringe column (Wages 69.08, Pension 13.75, H&W 13.45, Annuity 9.30) and state increments are "wages until allocated to the Funds" with "fringes flat for the agreement." The 3/1/2026 split of the $2.50 increment (GT applied +1.50 wage, +0.25 pension, +0.50 H&W, +0.25 annuity -> J 70.58) is NOT in the documents. Emitted the documented derivation (69.08+2.50=71.58 to wages, page-2 fringes) rather than reverse-engineering GT; flagged. Verified the GT figures 70.58/14.00/13.95/9.55 appear on NO book page.
- **sprinkler_fitters_704** - header exact; 13/13 rows; overall **15.0%** (39/260), 221 blanks (0 wrong).
  - The 704 Rate Notice is image-only (12 pages, 0 text, 0 tables); the wage/fringe grid lives only there. No OCR toolchain (tesseract/ocrmypdf absent in env), so per spec section 4b every notice-sourced cell (Wage + all 13 fringe/derived columns x 13 rows) is blank+flagged - the OCR/LLM boundary. The text CBA supplies the prose rules (Foreman = J+4.50, GF = Foreman+2.00, apprentice 40-85% ladder) but those need the Journeyman base wage from the image notice to produce numbers.

### Blank / divergent cell list (mirrored to each ai_output/<union>.gaps.md)
- **483** (7 entries): Residential/* Pension (1/1/2026 reallocation not in docs); Residential/* Vacation 483 (same); Residential Apprentice Class 1-5 Wage (residential apprentice scale not in docs). Apprentice rows also leave derived wage cols + non-flat funds blank as a consequence.
- **704** (182 entries): every Building row x {Wage, Health & Welfare, RESA, Pension, SIS, UA International Training, Apprenticeship Training, S.U.B. 704, Industry Promotion National Use, Industry Promotion Local Use, S & E 704, Craft 704, Union Dues 704, Retiree Holiday 704} - image-only notice, no OCR.
- **537** (1 entry, divergence not blank): Wage / Pension Local / Health & Welfare / Annuity - 3/1/2026 increment split between wages and funds not stated in books; emitted document-derived values (full increment to wages, page-2 fringes).

### Hard-rule compliance
- Read-only verified: cba/ + ratesheet/ mtimes unchanged across runs (snapshot diff clean). Writes only under ai_output/.
- No groundtruth values copied: 537/483 values are computed from book/notice/CBA figures; 704 left blank; GT header read only for column names/order.
- Half-up `r2()` used for every computed dollar cell.

### Next iteration (if feedback): options to raise 537/704
- 537: if the evaluator deems the 3/1/2026 allocation in-scope, would need a documented allocation source (not in current books) - otherwise the 67.4% reflects an honest GT-vs-document divergence.
- 704: requires an OCR toolchain (tesseract/ocrmypdf) or LLM image extraction to recover the Journeyman wage + fringe grid from the image-only notice.

## Iteration 2 - 2026-05-29 12:31:39 EDT - target: sprinkler_fitters_704 (unblock image-only Rate Notice via self-contained OCR)

### Regenerate commands (exact - now include the OCR deps pypdfium2 + rapidocr-onnxruntime + Pillow)
```
# all three unions:
uv run python pipeline/run.py --all

# just 704:
uv run python pipeline/run.py --union sprinkler_fitters_704
```
Output written: `data/sprinkler_fitters_704/ai_output/2026.01.01.704 Rate Sheet.csv` + `sprinkler_fitters_704.gaps.md`.
(483/537 commands from Iteration 1 still valid; the OCR deps are harmless extras for them.)

### OCR engine used and whether it worked
- **rapidocr-onnxruntime** (bundled ONNX model; NO tesseract system binary, NO torch, NO API key) + **pypdfium2** to rasterize each notice page at `render(scale=3)`. New module `pipeline/ocr.py` (Token model, `ocr_pages`, `value_on_row` band-matching, number parsing). **It worked extremely well** - per-token confidences mostly 0.94-1.00; every wage and fringe figure on all 12 pages recovered, including a manual image-crop cross-check of the one ambiguous cell (page 11 S & E = .17, confirmed correct OCR).
- The 12-page notice = 1 Journeyman sheet (p0), 1 apprentice %-scale table (p1: 40/45/50/55/60/65/70/75/80/85% -> Class 1..10), and 10 per-period apprentice fund sheets (p2..p11). Period is keyed off the scale table by nearest-wage match (robust to the "lst"/"l" OCR misreads of "1st").

### Status by union / zone (MEASURED cell accuracy from self-check)
- **sprinkler_fitters_704** - header exact; 13/13 rows; overall **259/260 = 99.6%** (was 15.0%), **0 blanks**, 1 honest GT-divergence. Building zone 259/260.
  - **Fully sourced + correct (13/13 each):** Wage (J 52.32 from notice; Foreman=J+4.50, GF=Foreman+2.00 from CBA Art.14/15; apprentices = notice per-period wages 20.93..44.48), Wage Differential/1.5x/2.0x, Health & Welfare (12.60 = notice 13.95 - RESA 1.35, same split rule as 483), RESA (1.35), Pension (7.45; **0.00 for Class 1** - the 1st-period sheet omits the Pension Fund line = documented first-year drop, recovered from OCR not GT), SIS (11.50 Defined Contribution Pension; **0.00 for Class 1** - same drop), UA International Training (.10 ITF), Apprenticeship Training (1.00 Apprentice Education Fund), S.U.B. 704 (1.20), **Industry Promotion National Use (0.20)** and **Industry Promotion Local Use (0.10)** - CBA Art.24 splits the $.30 fund into $.06 admin + $.14 National (=0.20) + $.10 Local, Craft 704 (per-period .03..06), Union Dues 704 (Union Assessment per-period 1.82..4.54), Retiree Holiday 704 (per-period .03..06).
  - **S & E 704 12/13:** per-period values .08..17 + J .20. Class 10 (top apprentice) is the only miss: notice 10th-period sheet literally prints **.17** (verified by rendering the page crop), GT shows 0.20. Emitted the document value .17; flagged as honest doc-vs-GT divergence (NOT copied from GT).
- **sprinkler_fitters_483** - UNCHANGED: Building **294/294 = 100.0%** (regression preserved), overall 367/441 = 83.2%, 0 wrong, residential gaps unchanged (scale + 1/1/26 reallocation still not in docs; left blank per "no assumptions").
- **pipe_fitters_537** - UNCHANGED: 182/270 = 67.4%; honest 3/1/26 reallocation gap left as-is (not in books).

### What changed this iteration (addressing evaluator's only code-fixable finding: 704)
- **PIVOT** for 704 from "image-only -> blank everything" to a real OCR pipeline. Added `pipeline/ocr.py` (rapidocr-onnxruntime + pypdfium2) and rewrote `extract_704` to parse the OCR'd per-period grid into canonical rows.
- Added `industry_promotion_local_use` canonical field (`canonical/fields.yaml`) mapped to the 704 "Industry Promotion Local Use" column, and split `industry_promotion_local` so 483's "Bay Area IP Fund 483" stays separate. No effect on 483/537 output (verified identical accuracy).
- Mid-iteration refinement: first emitted National Use=0.30 / Local=blank (89.6%); then found CBA Art.24's explicit $.06+$.14+$.10 breakdown -> National=0.20, Local=0.10 -> 99.6%. Fully documented, not GT-copied.

### Blank / divergent cell list (mirrored to ai_output/sprinkler_fitters_704.gaps.md)
- **704** (1 entry, divergence not blank): Building / Apprentice Class 10 / S & E 704 - notice 10th-period sheet states .17 (emitted), GT shows 0.20.

### Hard-rule compliance
- Read-only: only ai_output/ written; cba/ + ratesheet/ untouched. OCR reads the notice image in-memory (pypdfium2), writes nothing under cba/.
- No GT values copied: every 704 cell derives from the OCR'd notice + CBA Art.14/15/24 prose; the one GT-divergent cell keeps the document value. GT header read for names/order only.
- Half-up `r2()` on every computed dollar cell.
