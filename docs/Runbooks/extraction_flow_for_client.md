# Extraction Flow — Step-by-Step (For Client Walkthrough)

How a Rate Notice PDF + a CBA become one structured rate sheet in our
system, end-to-end. The customer's standard process is to **upload the
year-period Rate Notice and the multi-year CBA together** because the
Rate Notice carries only the Building/Commercial scale and the
Residential package lives in the CBA. The pipeline merges both into one
rate_period in Aurora.

**Updated 2026-06-10** to reflect batched-CBA merge support (commit
`e971a18`). The earlier "one PDF → one rate sheet" framing is now a
special case of the general "one batch → one rate sheet".

---

## The 11 steps

### 1. Reviewer picks the documents to upload
Customer drags both PDFs into Admin → Uploads in a single multi-select:
- `2026.01.01.483 Rate Notice.pdf` — the Rate Notice with the year's
  effective date in the filename. **Required as the batch anchor.**
- `2024.08.01-2030.07.31.483 CBA.pdf` — the multi-year CBA. Filename
  carries a date range, not an anchor.
- (Optional) `Residential Apprentice Scale.pdf` — a third doc when the
  Rate Notice + CBA don't carry the Residential apprentice scale.

### 2. Browser computes the batch anchor period + content hashes
For every selected PDF the browser:
- Computes SHA-256 of the bytes (for duplicate detection).
- Mints a single `batch_id` (UUID) for the whole multi-select.
- Scans filenames for a clean `YYYY.MM.DD.<local>` pattern with a
  Rate Notice / Rate Sheet / Wage Sheet keyword — that file's date
  becomes the batch's **anchor period** (e.g. `2026-01-01`).
- A CBA's filename date range is **not** a valid anchor — CBAs inherit
  from a Rate Notice in the same batch.

### 3. Browser requests presigned PUT URLs
For each file the browser calls `POST /v1/uploads` with
`{filename, batch_id, batch_period, content_hash}`. The Lambda:
- **If the content_hash already maps to a published period**: returns
  `{status: "duplicate", existing_period_id}`. The browser shows
  "already processed" and skips the upload. No SFN run, no Bedrock spend.
- **Otherwise**: writes a `file_hashes` row keyed by content_hash (to be
  back-filled with the period_id after publish), generates a presigned
  PUT URL with the S3 key:
  `laboraid/uploads/<batch_id>/<batch_period>/<filename>`
  — and returns it. The browser PUTs the bytes directly to S3.

### 4. S3 fires an EventBridge event
The S3 inputs bucket has `event_bridge_enabled=True`. The
`Object Created` event routes to Step Functions
`laboraid-dev-l3-sfn-main`. Latency ~1-2 seconds. One execution per file,
so a 2-PDF batch fires two parallel SFN runs.

### 5. Step Functions starts
A new execution begins with the EventBridge event as input. Each run is
independent — the two PDFs in a batch don't directly know about each
other; they coordinate via the **shared rate_period** in Aurora.

### 6. Classify (Lambda)
Reads the S3 key + filename and extracts:
- `local` — union local number (483, 704, 537, 281, 821, …).
- `period` — for Rate Notice / Rate Sheet filenames, the date from the
  filename. For CBAs (range-date filenames) or anything else, the
  `batch_period` segment of the S3 key wins. CBAs always inherit.
- `doc_type` — `rate_notice` / `rate_sheet` / `cba` / `apprentice_scale`
  / `unknown`. Decided by longest-keyword-wins over the filename.
- `union` — the kernel union key (`sprinkler_fitters_483`) if local
  matches a hand-coded extractor, else `local_<NNN>` for unknown unions.

### 7. ExtractorInvoker (Lambda) — the routing decision

| Doc type | Union | Path |
|---|---|---|
| `rate_notice` / `rate_sheet` | One of {537, 704, 483, 281, 821} | **Path A — Kernel** |
| `rate_notice` / `rate_sheet` | Any other local | **Path B-RN — LLM (Rate Notice prompt)** |
| `cba` | Any (kernel or not) | **Path B-CBA — LLM (CBA prompt)** |
| `apprentice_scale` | Any | **Path B-AS — LLM (Apprentice Scale prompt)** |

Key change from the earlier doc: **the routing is by `doc_type`, not just
by union**. The kernel handles tabular Rate Notices; the LLM handles
prose CBAs and apprentice scale variants — even for kernel unions.

### 8a. Path A — Deterministic kernel (Rate Notice on a kernel union)
Bedrock AgentCore Runtime invoked in "direct mode":
- Hand-written Python per union (`sprinkler_fitters_483.py`, …) that
  knows the EXACT table layout of that union's Rate Notice.
- Uses pdfplumber + RapidOCR.
- Emits canonical rows (ClassificationRow + RateCell objects), a CSV,
  and a `gaps` list of `[zone, package, column, reason]` tuples for
  every cell the kernel knew it couldn't fill (e.g., "residential
  apprentice scale not in provided docs").
- **Accuracy: 99%+ on the 5 unions.** Same input = same output.
- Time: ~30-60 s.

### 8b. Path B-RN — LLM extractor with Rate Notice prompt
For Rate Notices on unknown unions. Bedrock Claude Sonnet 4.6 multimodal,
prompt asks for full classification × column table as JSON. Lambda
converts JSON to canonical CSV. Time: 15-90 s. Cost: $0.10-$0.30/PDF.

### 8c. Path B-CBA — LLM extractor with CBA prompt
For CBAs (prose contracts). Bedrock Claude Sonnet 4.6, prompt focuses on
the Residential Sprinkler section and refuses to apply the CBA's
Building Article 15 apprentice percentages to Residential (those
percentages are Commercial-only). Outputs Residential Foreman + Journeyman
rows in the same canonical CSV shape — same column names as the kernel.
Time: 60-180 s on a 35-page CBA. Cost: $0.30-$0.60/PDF.

### 8d. Path B-AS — LLM extractor with Apprentice Scale prompt
For separate Apprentice/Trainee Scale documents. Same canonical CSV
output. *Handling in progress — Friday's work.*

### 9. Canonical CSV in S3 (per source PDF)
Each extractor writes a CSV to a per-source-PDF key:
`s3://laboraid-dev-l3-bucket-outputs/laboraid/uploads/<batch_id>/<batch_period>/<source_pdf_stem>.csv`
so multiple PDFs in the same batch don't collide on a shared output.csv.
Each CSV uses the same layout:
```
Union Group, Trade, Union Local, Zone, Package, Start Date, End Date, <rate columns…>
UA, Sprinkler, 483, Residential, Journeyman, 1/1/26, 7/31/26, 47.82, 47.82, 71.73, …
```

### 10. Publisher (Lambda) — merges into one rate_period
Reads the canonical CSV, then:

- **First write per (union, start_date)**: INSERTs a new `rate_periods`
  row (`approval_state=pending_review`, `version=1`). INSERTs all
  `rate_cells`. Stamps every cell with `provenance.source_pdf` so the
  reviewer can trace any value back to the PDF it came from.
- **Subsequent writes for the same (union, start_date)** — **merge mode**:
  - **New (zone, package, column) triple** → INSERT.
  - **Existing triple, current value is NULL** → UPDATE to the new value.
    This is how the CBA's Pension fills the kernel's blank for
    Residential Foreman.
  - **Existing triple, current value is non-null** → first-write wins,
    skip (reviewer resolves real value conflicts via override).
- After every write, **recomputes `gap_count` and `gaps_detail`** from
  the actual NULL cells in Aurora — so the Inbox banner shrinks as the
  CBA fills in gaps the Rate Notice left.

### 11. Step Functions ends in `Published`
EventBridge can emit a `laboraid.rate-sheet.created` event to any
downstream consumer (notifications, dashboards, payroll calculator). The
reviewer sees the new rate sheet card in the Business Inbox, with an
amber "needs more input" banner if any cells remain NULL.

---

## What a batched upload looks like in Aurora

For Sprinkler 483 / 2026-01-01 after a CBA + Rate Notice batch upload:

```
rate_periods row:
  id: 0f207243-…
  union_id: <Local 483>
  start_date: 2026-01-01
  version: 1
  approval_state: pending_review
  source_files.uploads: [
    "laboraid/uploads/.../2026.01.01.483 Rate Notice.pdf",
    "laboraid/uploads/.../2024.08.01-2030.07.31.483 CBA.pdf"
  ]
  canonical_json.gap_count: 42
  canonical_json.gaps_detail: [
    ["Residential", "Apprentice Class 1", "Wage",
     "residential apprentice scale not in provided docs"],
    ...
  ]

rate_cells: 378 rows, of which:
  - 336 have non-null `value` (89% coverage)
  - 42 have value=NULL (the "gaps")
  - Each row's provenance.source_pdf identifies which PDF filled it:
    * 2026.01.01.483 Rate Notice.pdf → Building zone + a few
      Residential cells (H&W, RESA, H&W Metal, Bay Area IP).
    * 2024.08.01-2030.07.31.483 CBA.pdf → Residential Foreman/Journeyman
      package + Residential Apprentice benefits.
```

See [docs/gap_report_483_2026-01-01.md](./gap_report_483_2026-01-01.md)
for a plain-English breakdown of the 42 gaps with reasons.

---

## The five paths at a glance (updated)

| Path | When | What runs | Output quality |
|---|---|---|---|
| **A. Kernel** | Rate Notice / Rate Sheet on local in {537, 704, 483, 281, 821} | Hand-written Python in AgentCore container | 99%+, deterministic |
| **B. LLM** | Anything not Path A (any CBA, any Apprentice Scale, Rate Notice on an unknown local) | Bedrock Claude Sonnet 4.6 multimodal with a `doc_type`-specific prompt | High, may need reviewer corrections |
| **C. Rework (merge)** | Reviewer rejected v1 and clicked "Apply overrides → v2" | Publisher copies cells + applies stored overrides | Mechanical, no AI |
| **D. Rework (AI)** | Reviewer clicked "Re-extract with AI feedback → v2" | AgentCore re-invoked with `rework_context` carrying rejection reason + comments | LLM-driven correction pass |
| **E. Hand-edit** | Reviewer types a per-cell override directly | Cell-override Lambda writes to DynamoDB; next rework folds it in | Human-canonical |

**A and B can run in parallel on the SAME batch** — when a Rate Notice
(Path A) and a CBA (Path B-CBA) are uploaded together. Each writes its
own CSV; Publisher merges both into one rate_period.

C/D/E only fire after a human action in the UI.

---

## Cost + latency summary (updated)

| Path | Typical latency | Cost per PDF | Determinism |
|---|---|---|---|
| A (kernel, Rate Notice) | 30-60 s | ~$0.001 (Lambda + AgentCore) | Yes |
| B-RN (LLM, Rate Notice) | 15-90 s | ~$0.10-$0.30 | No |
| B-CBA (LLM, CBA) | 60-180 s | ~$0.30-$0.60 | No |
| B-AS (LLM, Apprentice Scale) | 15-30 s | ~$0.05-$0.10 | No |
| C (rework merge) | 2-5 s | ~$0.0001 | Yes |
| D (rework AI) | 30-90 s | ~$0.10-$0.30 | No |
| E (override) | <1 s | negligible | Yes |

For a typical batched upload (Rate Notice + CBA) the wall-clock is
**60-180 s** (paths A and B-CBA run in parallel, B-CBA is the slower
of the two on a 35-page CBA). Cost: **~$0.30-$0.60 per period**.

---

## Versioning + history (unchanged from prior version)

The Publisher is **not** an upsert on rate cells. The path a PDF takes
through history is:

### 1. Multi-PDF uploads belonging to one period

The browser stamps a single `batch_id` (UUID) per multi-select. The
batch_id rides through:
- the S3 key: `laboraid/uploads/<batch_id>/<batch_period>/<filename>`
- the Step Functions input
- the Publisher's `source_files.uploads` list on the rate_periods row
- the Jobs UI (visible as a small Batch pill)

Reviewers can see at-a-glance which jobs belong to the same intent. If
later parts of a batch arrive (a forgotten Apprentice Scale doc), the
Publisher detects `(union, start_date)` already exists and **merges
the new cells in with `provenance.source_pdf = <new filename>`** — never
overwriting cells from the prior PDF.

### 2. Same PDF uploaded twice (idempotency)

Browser computes SHA-256 before requesting a presign. `/v1/uploads`
checks `file_hashes` DDB:

- **Hash already processed** (period_id populated): Lambda returns
  `{status: "duplicate", existing_period_id, existing_s3_key,
  first_seen_at}` immediately — no PUT, no Bedrock, no duplicate Aurora
  row, no extra Lambda cost.
- **Fresh hash**: a row is written *before* the PUT URL is returned.
  Publisher back-fills `period_id` after the Aurora commit so future
  re-uploads of the same bytes are deduped.

### 3. Different content, same period (reviewer-driven rework)

If the union sends an *amended* version of the same period:
- The new upload publishes alongside the existing period (cells merged
  with their own `provenance.source_pdf`).
- The reviewer sees both sources in the Inbox and decides whether to
  **approve as-is**, **override** specific cells, or **reject + rework**
  the period into v2 (Path C or D).
- Approval ratchets `pending_review → approved`. We never silently
  overwrite an approved period — a new version row is written and the
  old one is archived for audit.

### 4. What this gives the customer

- **No silent data loss.** A duplicate PDF is told it's a duplicate.
- **No surprise overwrites.** Cells from different PDFs in the same
  period each carry the filename they came from in
  `provenance.source_pdf`.
- **Traceable batches.** The Batch pill in the Jobs UI lets a reviewer
  click through and see all PDFs that were intended to land together.
- **Self-reporting gaps.** Every period's `canonical_json.gaps_detail`
  lists what's missing AND why — so the reviewer knows whether to wait
  for another document, ask the customer for one, or override.
- **Audit-friendly history.** Every published period has a hash trail
  in `file_hashes` and a per-cell `provenance.source_pdf` — answering
  "where did this rate come from" without git-archaeology.
