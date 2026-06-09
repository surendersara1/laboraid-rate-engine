# Feature Improvement Plan #1 — Business Persona Review Experience

**Date:** 2026-06-09
**Owner:** Surender Sara (NBS) · LaborAid POC
**Scope:** Business reviewer workflow + Admin trace integration
**Engagement model:** 24-hour push, ship Tier 1 → 2 → 3 in order

## 0. Context

Day 1 (2026-06-08) shipped the end-to-end pipeline: PDF upload triggers
EventBridge → Step Functions → AgentCore extraction → Aurora write → UI
display. First clean SUCCEEDED run finished in 40.7 s with 14/14 stages green
(commit `e420657`).

Today we focus on the **Business reviewer** experience: what the human
domain expert actually does with each extracted rate sheet. The Admin / Ops
dashboard already shows pipeline mechanics; this plan covers how a
benefit-fund analyst reviews, comments on, approves, rejects, and forces a
rework of an extraction.

---

## 1. Target page layout

The `/business/rate-sheets/<union>/<period>` route is the workhorse. Today
it's a 12-column grid: PDF · cells · provenance. We add a **header card**,
an **action bar**, an **artifacts panel**, and an **activity timeline**.

```
┌─────────────────────────────────────────────────────────────────────┐
│ HEADER                                                              │
│ Sprinkler 704 · 2026-01-01           [SUCCEEDED] [pending_review]   │
│ Extracted 2026-06-08 18:42 by ExtractorAgent · 40.7s · job bd21e75c │
│ 13 classifications · 221 cells · 1 gap                              │
├─────────────────────────────────────────────────────────────────────┤
│ ACTION BAR                                                          │
│ [Approve]  [Request rework]  [Reject]   reason: [____________]      │
├─────────────┬──────────────────────────────┬────────────────────────┤
│ SOURCE      │ EXTRACTED CELLS              │ DETAIL PANEL           │
│ (PDF inline │ Classification | Field | Val │ Selected cell:         │
│  scroll/    │ Apprentice 1   | Wage  |20.93│  Apprentice 1 · Wage   │
│  zoom,      │ Apprentice 1   | H&W   |12.60│  Value: $20.93         │
│  no auto-   │ ...                          │  Confidence: 100 %     │
│  download)  │                              │  Provenance: page 2    │
│             │                              │  row 11 · method kernel│
│             │                              │  Comments (3) ────     │
│             │                              │  [+ Add comment]       │
│             │                              │  [✎ Override value]    │
├─────────────┴──────────────────────────────┴────────────────────────┤
│ ARTIFACTS (every file this run produced)                            │
│ ▸ Source PDF      s3://.../704 Rate Notice.pdf       3.0 MB  Open ↗ │
│ ▸ Canonical CSV   s3://.../output.csv                2.2 KB  Open ↗ │
│ ▸ Excel (xlsx)    (Tier 2 work)                                     │
│ ▸ Gap report JSON (Tier 2 work)                                     │
│ ▸ Step Functions trace  →  /admin/jobs/bd21e75c…                    │
├─────────────────────────────────────────────────────────────────────┤
│ ACTIVITY  (audit trail, newest first)                               │
│ 18:43  Agent published v1 with 1 gap (Apprentice Class 10 S&E)      │
│ 18:42  Agent extracted 13 rows from Rate Notice.pdf                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Feature tiers

Three tiers, in execution order. Each tier is self-contained and shippable.

### Tier 1 — Reviewable demo polish (~2 hr)

Goal: a business user can land on the review page and immediately understand
**what this ratesheet is**, **where the source PDF is**, and **where the
output artifacts live**, without anything auto-downloading.

| # | Feature | Where data lives | LLM? |
|---|---|---|---|
| 1.1 | **Header card**: Union name, period, status pill (approval + SFN status), "Extracted by ExtractorAgent · {duration} · job {id-link}" subtitle, counts of classifications / cells / gaps | Aurora `rate_periods.canonical_json` + enrichment from `/v1/jobs/{job_id}` | No |
| 1.2 | **No-auto-download PDF**: replace iframe with PDF.js (worker hosted on our CloudFront origin) so it renders inline, scroll + zoom, no download dialog | Frontend (`PdfViewer.tsx`) | No |
| 1.3 | **Artifacts panel**: explicit Source PDF + Canonical CSV with sizes + presigned "Open ↗" links (links open in new tab, browser respects Content-Disposition we set on S3) | Reuses `/v1/jobs/{id}` `artifacts[]` | No |
| 1.4 | **Admin job trace link**: header subtitle's job ID jumps to `/admin/jobs/<id>` for ops handoff | Frontend link | No |
| 1.5 | **Cell click → provenance sidebar**: shows page number, line number, extraction method (kernel / Bedrock multimodal / Claude generic), confidence pill | Aurora `rate_cells.provenance` (already JSONB) | No |

### Tier 2 — Workflow primitives (~3 hr)

Goal: a business user can **act** on a ratesheet — approve it, reject it
with a reason, comment on individual cells, override individual values.

| # | Feature | Where data lives | LLM? |
|---|---|---|---|
| 2.1 | **Approve flow**: button POSTs to `/v1/unions/{local}/rate-sheets/{period}/approve`, writes `approval_state=approved`, `approved_by`, `approved_at` to Aurora; locks cells from further edits | `ratesheet-approve` Lambda (exists, needs wiring) + Aurora + `audit_log` | No |
| 2.2 | **Reject flow**: required reason textarea, writes `approval_state=rejected`, `rejected_by`, `rejected_at`, `rejection_reason`, `rejection_tags[]` (taxonomy: value-wrong, missing-field, wrong-period, format-issue, other) | `ratesheet-reject` Lambda + Aurora + `audit_log` | No |
| 2.3 | **Cell-level comments**: per-cell threaded discussion. Click a cell → sidebar shows existing comments + textarea to add. Comments visible to all reviewers. | New table `cell_comments` in Aurora (cell_id FK, author, body, ts) | No |
| 2.4 | **Cell-level overrides**: reviewer types a different value for a single cell. Original value preserved; override audit-logged with author + timestamp + optional justification | DynamoDB `overrides` (already provisioned) | No |
| 2.5 | **Activity timeline**: scrollable feed at bottom of review page, newest first, showing every audit_log entry for this ratesheet | Aurora `audit_log` | No |
| 2.6 | **Real .xlsx output**: bundle `openpyxl` into the `xlsx-renderer` Lambda asset (was POC pass-through). Writes a formatted Excel file to outputs bucket; shows up in Artifacts panel | `xlsx-renderer` Lambda + outputs bucket | No |
| 2.7 | **Gap report JSON**: separate artifact listing every cell the kernel flagged as a gap (with reason). Today's "1 gap" on 704 (Apprentice Class 10 S&E 0.17 vs 0.20 GT divergence) becomes a structured artifact | Extend `kernel_extract_to_csv_s3` to emit `gaps.json` alongside the CSV | No |

### Tier 3 — LLM rework loop (~half day)

Goal: when a reviewer rejects an extraction with annotations, the system
re-runs the agent with the rejection context baked in, producing a v2
extraction the reviewer can re-evaluate.

Storage shape change:
- Add `version` (int, default 1) and `prior_period_id` (uuid FK to itself)
  columns to `rate_periods`. v1 has prior=NULL; rework produces v2 with
  prior=v1.id; the Business inbox by default filters to the **latest
  version** of each (union, period) pair.

| # | Feature | Where data lives | LLM? |
|---|---|---|---|
| 3.1 | **Reviewer cell annotations**: during reject, the reviewer can optionally annotate specific cells: "this value should be 0.18 not 0.17 — see page 4 line 7". Annotations attached to the rejection record | `audit_log.details` JSONB | No (input only) |
| 3.2 | **Rework trigger**: on reject-with-annotations, the `ratesheet-reject` Lambda fires a Step Functions execution with an enriched payload that carries the prior canonical + rejection reason + cell annotations | Step Functions + new event source | No |
| 3.3 | **Agent rework prompt**: the `ExtractorAgent` system prompt is extended at invoke time with a "REWORK CONTEXT" block carrying the reviewer feedback. The agent uses Path B (Bedrock multimodal) to re-read the flagged cells specifically | Strands prompt + extractor-invoker payload | **YES** — Bedrock Claude Sonnet on the flagged cells |
| 3.4 | **Versioned ratesheet rows**: a successful rework writes a new `rate_periods` row with `version=N+1` and `prior_period_id=<v1.id>`. v1 stays in the DB as the audit trail | Aurora schema change | No |
| 3.5 | **Diff view**: when v2 of a ratesheet is open, the UI shows changes vs v1 (added / changed / removed cells, with the prior value greyed-through and the new value highlighted) | Frontend + Lambda returns both versions | No |
| 3.6 | **Diff badge on the Inbox card**: "v2 · 14 cells changed" so the reviewer knows what to look at | Lambda computes diff count, UI badge | No |

#### Tier 3 flow diagram

```
Business user rejects with:
  "Pension values for Apprentice Class 1-3 should not be $0;
   they're 50 %, 60 %, 65 % of journeyman per the CBA on page 4"
                    │
                    ▼
ratesheet-reject Lambda
  1. Save rejection reason + cell annotations to audit_log
  2. Set approval_state='rejected' on the current version
  3. StartExecution on the main SFN with enriched payload:
       { union, period, s3_key,
         prior_period_id,
         reviewer_feedback: {
           reason: "...",
           rejection_tags: ["value-wrong"],
           cell_annotations: [
             { pkg:"Apprentice Class 1", field:"Pension",
               current:0.00, expected_hint:"~50% of JM",
               source_hint:"CBA page 4" },
             ...
           ]
         } }
                    │
                    ▼
ExtractorAgent (re-invoke)
  System prompt addendum:
    "Prior extraction was rejected. Reviewer says: {reason}.
     Verify these specific cells: {annotations}. Re-read the
     source PDF with focus on those fields. Use Path B
     (escalate_to_claude_multimodal) to double-check flagged cells."

  Agent re-extracts -> produces v2 canonical
                    │
                    ▼
Publish Lambda writes new rate_periods row (version=2, prior=<v1.id>)
Aurora rate_cells rows for v2
                    │
                    ▼
Business inbox refreshes; shows "Sprinkler 704 · 2026-01-01 (v2)"
                    │
                    ▼
Reviewer opens v2; UI shows diff vs v1
```

---

## 3. Cross-cutting concerns

### 3.1 Data model deltas

```sql
-- Tier 3: rework versioning
ALTER TABLE rate_periods ADD COLUMN version       INT NOT NULL DEFAULT 1;
ALTER TABLE rate_periods ADD COLUMN prior_period_id UUID REFERENCES rate_periods(id);
CREATE INDEX rate_periods_union_period_version_idx
  ON rate_periods (union_id, start_date, version DESC);

-- Tier 2: cell comments
CREATE TABLE cell_comments (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  cell_id    UUID NOT NULL REFERENCES rate_cells(id) ON DELETE CASCADE,
  author     TEXT NOT NULL,        -- cognito sub
  body       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cell_comments_cell_idx ON cell_comments (cell_id, created_at);

-- Tier 2: rejection tags taxonomy (free-form, but UI offers presets)
-- already supported by rate_periods.rejection_tags TEXT[]
```

### 3.2 IAM additions

- `ratesheet-reject` Lambda role needs `states:StartExecution` on the
  main SFN ARN (Tier 3.2).
- `xlsx-renderer` Lambda needs `s3:PutObject` on outputs bucket
  (already has it).

### 3.3 PDF behaviour

Tier 1.2 requires PDF.js worker bundled to our CloudFront origin so the
inline viewer works on Chrome / Edge / Firefox without auto-download
prompts. Vite-bundled worker path needs `?worker` import + a static asset
copy step.

### 3.4 Agent system prompt addendum (Tier 3.3)

Append to `EXTRACTOR_SYSTEM_PROMPT` when `payload.reviewer_feedback` is
present:

```
## REWORK CONTEXT
A prior extraction (v{N-1}) was REJECTED by reviewer
{reviewer.email} at {ts}.

Reviewer's reason: {feedback.reason}
Rejection tags: {feedback.rejection_tags}

Specific cell concerns:
{for each cell_annotation:}
  - {pkg} · {field}: current value {current}, reviewer hint:
    "{expected_hint}", source hint: "{source_hint}"

You MUST:
  1. Re-extract the PDF with explicit attention to the cells above.
  2. For each annotated cell, call escalate_to_claude_multimodal
     (Path B) to verify the value directly from the source PDF.
  3. Record the prior value and the new value in your output so the
     diff view can render the change.
  4. If you cannot resolve a flagged cell, record it as a gap; do
     NOT fabricate.
```

---

## 4. Open questions to resolve before coding each tier

These are choices that affect the final UX. We answer one per tier before
opening editor for that tier.

| Q | Tier | Question | Default if no answer |
|---|---|---|---|
| Q1 | T1 | PDF inline behaviour — PDF.js (no download button) **or** keep iframe (clean URL, browser-native, but shows download icon) | **PDF.js** |
| Q2 | T2 | Comment scope — per-cell **or** per-row (whole classification) **or** per-sheet **or** all three? | **per-cell only for v1**, expand later |
| Q3 | T2 | xlsx formatting — plain values **or** styled (bold headers, alternating rows, frozen header) | **styled** — sells the demo |
| Q4 | T3 | Reject behaviour — **(a)** flag only (no LLM) · **(b)** immediate LLM re-run · **(c)** queued batch rework | **(b) immediate** |
| Q5 | T3 | Diff view — side-by-side v1 vs v2 **or** inline "v1 → v2" deltas on one table | **inline deltas** — simpler, more readable |

---

## 5. 24-hour execution plan

Working in order, each tier is shippable on its own (commit + push +
deploy after each). If we run out of time mid-tier, the prior tier is
still a complete demo.

```
Hour  0   →  start Tier 1
Hour  2   →  Tier 1 ships, commit + push + deploy
              client sees: PDF without download, full artifacts, header
              card, click-cell-for-provenance
Hour  2-5 →  Tier 2 work
Hour  5   →  Tier 2 ships
              client can: approve, reject, comment, override, view
              activity, download real xlsx
Hour  5-12→  Tier 3 work
Hour 12   →  Tier 3 ships
              client can: reject with annotations -> agent re-runs
              with feedback -> v2 appears in inbox -> diff view
Hour 12-24→  Buffer + polish + 2nd union (537 or 483) for variety
              in the demo
```

Each tier commit message includes the tier number for traceability.

---

## 6. Decisions log (filled as we go)

| Decision | Date | Choice | Note |
|---|---|---|---|
| Q1 | _open_ | _ _ | _ _ |
| Q2 | _open_ | _ _ | _ _ |
| Q3 | _open_ | _ _ | _ _ |
| Q4 | _open_ | _ _ | _ _ |
| Q5 | _open_ | _ _ | _ _ |

---

## 7. Out of scope today

Explicitly NOT on this push (keep them on the v1.1 backlog):

- Notifications (email / Slack on new ratesheet) — Tier 2.5 only does
  activity timeline; no push.
- Bulk approve / reject across multiple ratesheets.
- Per-reviewer assignment ("Suzanne reviews UA Sprinkler, Mike reviews
  Pipefitters") — everyone sees every pending sheet.
- Approval workflows with multiple sign-offs (legal + finance both
  approve before publish).
- Cost tracking dashboard (Admin → Costs page is a stub).
- Re-extraction triggered by a *new* PDF for the same period (replacing,
  not reworking) — that's a different flow.
- Mobile responsive layouts.

These are all real features for v1.1 / production; today is POC depth on
the rework loop, not breadth.
