# Design — Multi-PDF Merge for Pattern C Unions

Architectural decision for the **pattern-C** problem identified in the
customer folder: several unions split a single rate sheet across multiple
PDFs for the same effective date.

Examples (see [docs/customer_pdf_extraction_log.md](./customer_pdf_extraction_log.md)):

- **Sprinkler 692** — `2024.01.01.692 Apprentice Rates.pdf` + `2024.01.01.692 Journeymen Rates.pdf` for the same `2024-01-01`.
- **Sprinkler 183** — `Apprentice SIS` + `Apprentice Wages` + `Total Package` per period.
- **Sprinkler 709** — `Apprentice` + `Journeymen` + `Residential` per period.
- **Sprinkler 696** — `Building` + `Residential` split per period.
- **Sprinkler 669** — Addendum letters D, E, F, G, H carry different fund tables.

The customer expects ONE rate sheet per `(union, period)`. Today the
pipeline produces N rate sheets (one per upload) for the same period —
the Business Inbox shows them as duplicate cards. We need a merge step.

---

## Three approaches considered

### A. Concatenate PDFs into one big PDF, then extract once  ❌ Rejected

- Bedrock Claude has a 32 MB hard cap on attached PDFs.
- Output token budget explodes with 5× the content (already hit at 8000 tokens on a single PDF; raising to 16000 just delays it).
- One bad page poisons the whole extraction.
- Re-fixing one source requires re-extracting the entire concat.
- Provenance lost — reviewer can't ask "which file did this Wage value come from?"
- Latency: 3-5 minutes minimum for a 5-PDF concat.

### B. Merge per-PDF CSVs into a master CSV after extraction  ❌ Rejected

- Once data is in CSV (flat text), column-name semantics are lost:
  `H & W` vs `H&W` look like two different fields.
- Row-dedup on free-form classification names is fragile
  (`Apprentice 1st Year` vs `Apprentice Year 1`).
- No place to record per-cell source PDF — best you can do is per-file metadata.
- Hard to undo one file's contribution if it was wrong; re-merge from scratch.

### C. Merge at the Aurora `rate_cells` level  ✅ Chosen

- Each PDF stays small + atomic. Re-extraction is one Lambda for one file.
- Per-cell provenance is preserved
  (`provenance.source_pdf = "2024.01.01.692 Journeymen Rates.pdf"`).
- Column-name normalization happens on structured rows, not free-form text.
- LLM failures isolate per file.
- Aurora's idempotency check already keys on `(union_id, start_date)`;
  changing "skip" to "append cells" is a small surgical change.

---

## Chosen architecture

```
Admin Upload page (UI)
    │  reviewer picks N PDFs via <input type="file" multiple>
    ▼
For each PDF, in parallel:
    POST /v1/uploads → presigned PUT → S3 → EventBridge → SFN
       Classify → ExtractorInvoker → LLM (or kernel for known unions)
                → canonical CSV in S3 → Publisher
    
    Publisher's behavior change:
       Look up rate_periods by (union_id, start_date).
       - If none: INSERT a new rate_periods row (current behavior).
       - If one exists:
           - APPEND new cells (skip any (zone, package, column) triple
             already present at this period — first write wins).
           - Tag each new cell with provenance.source_pdf = <this upload's filename>.
           - Append the new PDF filename to rate_periods.source_files[].

Business UI
    Inbox shows ONE card per (union, period)
    Provenance panel shows source_pdf per cell
    Per-union "column normalization profile" UI (deferred) — reviewer maps
        "H&W" → "Health & Welfare", saves it; future uploads auto-normalize
```

### Why merge belongs at Aurora, not at the CSV layer

The per-PDF CSV in S3 stays as **raw provenance** — "here's exactly what
Claude/kernel saw on this one file." Aurora becomes the **merged truth**.
Both representations have a place:

- S3 CSV: audit-trail, debug, reproduce single-PDF extraction.
- Aurora rate_cells: canonical, queryable, normalized, joined with provenance.

---

## MVP — what to build first (~half a day)

1. **Publisher idempotency change.** Detect existing `(union_id, start_date)`,
   APPEND cells instead of skipping. Skip any `(zone, package, column)`
   triple already present. Stamp every appended cell with
   `provenance.source_pdf`. Append filename to `rate_periods.source_files[]`.
   ~30 lines in `lambdas/processing/publisher/handler.py`.

2. **Multi-file Admin Upload UI.** Change
   `<input type="file">` → `<input type="file" multiple>`. Iterate the
   selected files and submit each through the existing `/v1/uploads`
   endpoint in parallel. ~10 lines in `ui/src/admin/Uploads.tsx`.

3. **Smoke test on 692.** Upload `2024.01.01.692 Journeymen Rates.pdf` +
   `2024.01.01.692 Apprentice Rates.pdf` for the same period. Verify ONE
   `rate_periods` row in Aurora with cells from both files; provenance
   distinguishes which PDF gave us which cell.

---

## Deferred (not in MVP — wait for real volume)

- **Column normalization profile UI.** Per-union mapping table
  (`H&W → Health & Welfare`, `JATC → Apprenticeship Training`). Tech debt
  for now; reviewer hand-normalizes via override.
- **Conflict detection.** Same (zone, package, column) appears in two
  source PDFs with different values? First-write wins for MVP; UI flag
  for the reviewer is a later improvement.
- **Auto-grouping at upload.** Today the reviewer manually picks the set
  of PDFs that go together. Filename-pattern auto-grouping (group by
  `<period>.<local>` prefix) is a polish item.
- **Reviewer-controlled "this set goes together"** — explicit grouping in
  the UI when filenames don't help.

---

## What stays unchanged

- The router in `extractor-invoker` (kernel vs LLM) — each PDF still routes
  independently. Path A for known unions (537/704/483/281/821), Path B for
  everything else. No cross-PDF coordination at extraction time.
- Per-PDF SFN executions — still one execution per upload. Parallel by S3.
- The Publisher's idempotency on (union_id, start_date) — preserved.
  We're only changing what happens on COLLISION (was: skip; now: merge).

---

## Open questions for the customer

1. **Which source wins on conflict?** If the CBA says `Pension = 9.20` and
   a Rate Notice for the same effective date says `Pension = 9.25`, the
   Rate Notice is probably newer/authoritative — but we should confirm.
2. **Column-name canonical list per union?** The customer may have a
   preferred set of names. Easier to normalize once than per-file.
3. **Are there cases where multiple PDFs explicitly SHOULDN'T merge?**
   (E.g., Building vs Residential — same period, different "zones"; we'd
   probably want both zones in the same rate sheet, but flag the question.)
