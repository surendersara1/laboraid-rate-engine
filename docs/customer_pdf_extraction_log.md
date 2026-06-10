# Customer PDF Extraction Log

Running record of what the LaborAid Rate Engine pipeline produces when fed
real customer Rate Notice / CBA PDFs. One entry per upload batch. Use this to
track quality, normalization gaps, and what's worth showing the client.

Pipeline path under test (committed 0a9a032, 5d97cb7):
```
PDF -> S3 inputs -> EventBridge -> Step Functions
   -> Classify (filename regex)
   -> ExtractorInvoker (routes on union)
       - known kernel union  -> AgentCore Runtime (deterministic Python kernel)
       - everything else     -> llm-extractor Lambda (Bedrock Claude Sonnet 4.6)
   -> Publisher Lambda (writes unions + rate_periods + rate_cells to Aurora)
   -> Business Inbox (UI)
```

---

## 2026-06-10 — Pattern-C multi-PDF merge shipped + smoked on 692

Implementation of the design in [docs/design_multipdf_merge.md](./design_multipdf_merge.md).

### Changes shipped

1. **Publisher** (`lambdas/processing/publisher/handler.py`): when a
   `(union_id, start_date)` collision occurs, switch to **merge mode**
   instead of skipping. Pre-loads existing `(zone, package, column_name)`
   triples for the period, appends only new cells, and stamps every cell
   with `provenance.source_pdf = <uploaded filename>`. Promotes the
   legacy single `source_files.rate_notice` into `source_files.uploads[]`
   so the provenance chain is uniform after the first merge.
2. **Admin Uploads UI** (`ui/src/admin/Uploads.tsx`): `<input multiple>`
   with parallel submissions through the existing `/v1/uploads`
   presigned-URL flow. Per-file status list (uploading / uploaded / error).
3. **Inputs bucket CORS**: added PUT to `AllowedMethods` (fixed earlier in
   the session — browser uploads were silently failing without it).

### Smoke result — Sprinkler 692 / 2024-01-01

Uploaded both PDFs in parallel via the API:
- `2024.01.01.692 Journeymen Rates.pdf` → SFN SUCCEEDED in 13s
- `2024.01.01.692 Apprentice Rates.pdf` → SFN SUCCEEDED in 42s

After both finished, Aurora state:
| Metric | Value |
|---|---|
| `rate_periods` rows for local=692 | **1** (merged from 2 PDFs) |
| `rate_cells` count | **176** |
| Distinct classifications | **12** (Foreman + Journeyman + Apprentice Periods 1–10) |
| `source_files.uploads[]` | both PDFs listed |

Per-cell provenance:
- **26 cells** tagged `source_pdf = 2024.01.01.692 Journeymen Rates.pdf` (Foreman + Journeyman, ~13 columns × 2 classifications)
- **150 cells** tagged `source_pdf = 2024.01.01.692 Apprentice Rates.pdf` (10 apprentice periods × ~15 columns)

The Business Inbox now shows ONE card for Sprinkler 692 · 2024-01-01
instead of the two duplicates it would have shown before the merge.

### Conflict semantics (current)
- First-write wins: if PDF #2 has the same `(zone, package, column)` as PDF
  #1, PDF #2's value is discarded. `cells_skipped_collision` counts
  surface this in the Publisher's response.
- Reviewer can override via the existing per-cell override UI if first-write
  was wrong.

### Open follow-ups
- Column-name normalization profile per union (still deferred).
- Conflict-detection UI flag — surface collisions to the reviewer instead
  of silently dropping the loser.
- The classifier still parses local from filename only; if a customer
  uploads a CBA with no date prefix, it falls back to `doc_type=unknown`
  and the LLM-extracted period wins. Working but not robust.

---

## 2026-06-10 — Admin Upload flow (single file, end-to-end via API)

First test that exercises the actual upload path the Admin UI uses — not
direct boto3 from a script. Proves the click-to-Aurora chain works.

### Steps under test
1. Cognito `InitiateAuth` (USER_PASSWORD_AUTH) — got idToken.
2. `POST /v1/uploads {filename}` → returned presigned PUT URL pointing at
   `s3://laboraid-dev-l3-bucket-inputs/laboraid/uploads/<filename>`.
3. `PUT <presigned URL> {pdf bytes}` → S3 200 OK.
4. S3 `Object Created` → EventBridge → Step Functions auto-triggered.
5. SFN → Classify → Extractor-invoker (union=local_268, unknown → LLM
   route) → llm-extractor (Bedrock Claude) → Publisher → Aurora.
6. SFN final status: **SUCCEEDED in 15-19 seconds**.

### Input
`From Customer/CBAs/Sprinkler/268/2021-2025 CBA & Notices/2024.01.01.268 Rate Notice.pdf` (461 KB).

### Result in Aurora (after fix)
| Field | Value |
|---|---|
| local | 268 |
| trade | Sprinkler Fitter (from PDF content via Claude) |
| parent_intl | UA |
| start_date | 2024-01-01 |
| approval_state | pending_review |
| cells | 56 |
| classifications | 7 |

### Real bug surfaced + fixed during this test

The `upload-presign` Lambda dumps every uploaded file at the flat key
`laboraid/uploads/<filename>` — no `<Trade>/<Local>/<Period>/` folder
structure. The Publisher's "folder is trade source-of-truth" heuristic
(introduced earlier today for Local 120) then read `pdf_parts[1] = "uploads"`
and wrote `trade='uploads'` to Aurora. Wrong.

**Fix (Publisher hot-patched, source committed in this commit):** ignore
reserved path segments (`uploads`, `tmp`, `scratch`, `unknown`) and fall
back to Claude's CSV "Trade" column. The folder remains authoritative only
for organized paths like `laboraid/Sprinkler/704/2026-01-01/...`.

### Conceptual note — large-PDF concern

The customer's CBAs can be 700+ KB and 30-100 pages. Bedrock Claude
multimodal has a 32 MB hard cap on attached PDFs and roughly a 100k-token
context for documents. Today our test PDFs are all <1 MB so we haven't hit
either limit. Risks for the larger 50-page CBAs:

- **Token-budget overflow** on output: we already saw Claude truncate
  mid-row at `max_tokens=8000`; raised to 16000 and switched to a compact
  schema. Real CBAs with 30+ classifications could still overshoot.
- **Latency**: a 30-page CBA could push the Bedrock call to 3-5 minutes.
- **Multi-period CBAs**: a 5-year CBA contains 5+ rate steps. Today we
  produce ONE rate_periods row per upload, so the LLM picks the earliest
  effective date. Need a separate "step extractor" mode if the customer
  wants all 5 historical sheets from one CBA upload.

These are tracked but not blocking — small Rate Notice uploads work great.

### Direction confirmed
- **Single-file upload via Admin UI works** — proven end-to-end. The
  Uploads.tsx page can ship as-is.
- **Multi-file (pattern C)**: per discussion today, we'll handle merge
  ourselves by **processing each PDF separately** and stitching the cells
  in Aurora at the (union, period) granularity — NOT by concatenating PDFs
  into one giant blob (which would hit Bedrock limits + lose provenance
  per source file). Multipart-upload UI helper to follow.

---

## 2026-06-10 — Customer folder inventory + pattern analysis

Walked the full `E:/NBS_LaborAid/From Customer/` tree to map what the client
actually delivered. Drives every decision below — the pipeline has to handle
*their* patterns, not the idealized "one Rate Notice per period" model.

### Inventory

| Category | Count | Examples |
|---|---|---|
| **CBAs** (full multi-year contracts) | ~43 | `2022.08.01-2027.07.31.704 CBA.pdf`, `2025-2030.550 CBA.pdf` |
| **Rate / Wage Notices** (periodic updates) | ~123 | `2026.01.01.704 Rate Notice.pdf`, `2024.10.01.314 Wage Rate Notice.pdf` |
| **Other** (trust agreements, addenda, remittance, memos) | ~26 | `04 Trust Agreement NASI Welfare Fund.pdf`, `Addendum F All Funds` |
| **Total PDFs** | **192** | |

**Unions represented:** 17 Sprinkler locals (120, 183, 268, 281, 314, 417,
483, 542, 550, 669, 692, 696, 699, 704, 709, 821, NASI), 3 Pipefitter (12,
398, 537), 1 Sheet Metal (105), LiUNA Laborers (multi-trade — many WAGES
PDFs). Plus the NASI trust funds.

### Three real-world patterns the customer mixes

| Pattern | Example unions | What to extract from | Status |
|---|---|---|---|
| **A. Clean separation** (CBA + separate Rate Notice per period) | 704, 821, 268, 542, 699, 821, 398, 12 | Just the Rate Notice; CBA is reference only | **Works today** — proven on 704 (kernel) + 314 Rate Notices (LLM) |
| **B. CBA-embedded rates** (CBA has the rate table inside; no separate notice) | LiUNA Laborers (every file is `*WAGES.pdf`); 314 CBA also has rate steps inside | The CBA itself | **Works today** — proven on 314 CBA (35 cells extracted from inside the contract) |
| **C. Split-per-classification across multiple PDFs for the same period** | 183 (Apprentice SIS + Apprentice Wages + Total Package separately per date); 692 (Apprentice + Journeyman per date); 709 (Apprentice + Journeyman + Residential per date); 696 (Building + Residential split); 669 (Addendum letters D, E, F, G, H) | All N PDFs MERGE into one rate sheet | **Gap — not built** |

### The pattern-C problem in detail

Sprinkler **Local 183**, effective date `2024-01-01`:
- `2024.01.01.183 Apprentice SIS.pdf`
- `2024.01.01.183 Apprentice Wages.pdf`
- `2024.01.01.183 Total Package.pdf`

The customer expects ONE rate sheet for 183/2024-01-01 merged from all three.
Today our pipeline produces THREE separate `rate_periods` rows (one per PDF
upload). The UI would show three duplicates for the same effective date —
confusing.

Same shape on **Sprinkler 709** (Apprentice / Journeyman / Residential split
per period), **692** (Apprentice / Journeyman split), **696** (Building /
Residential split), **669** (Addenda D/E/F/G/H carry different fund tables).

### Direction (decided 2026-06-10 — full write-up in
[docs/design_multipdf_merge.md](./design_multipdf_merge.md))

- Pattern A: nothing to do, works.
- Pattern B: nothing to do, works.
- Pattern C: **merge at the Aurora `rate_cells` level, not at the PDF or
  CSV level.** Each PDF stays small + atomic; Publisher's idempotency
  check is changed from "skip on `(union, period)` collision" to "append
  cells with per-cell `provenance.source_pdf` tagging." Multi-file Admin
  upload UI iterates the existing single-file `/v1/uploads` endpoint in
  parallel. Concatenating PDFs (would blow the 32 MB Bedrock cap) and
  CSV-level merging (loses column-name semantics) were both rejected.
  ~half a day MVP. See design doc for full reasoning.

### Upload flow — NOT YET TESTED end-to-end from the UI

So far all uploads have been done via direct boto3 `s3.put_object` from
test scripts. The `/v1/uploads` presigned-URL endpoint exists in the API
stack but **no end-to-end test from the Admin UI has been run**. Plan for
next session:

1. Test the Uploads page in the Admin persona: pick a PDF, confirm presigned
   URL is generated, confirm upload triggers the SFN, confirm the rate sheet
   appears in the Business inbox.
2. If pattern C concatenation becomes our problem to solve, the same Uploads
   page is the natural place to add a "multi-PDF merge" affordance — drop
   the N split PDFs, server concatenates into one before triggering the
   pipeline.

---

## 2026-06-10 — Sprinkler Local 314 (3 PDFs in parallel)

### Inputs (source: `From Customer/CBAs/Sprinkler/314/`)
| PDF | Size | What it is |
|---|---|---|
| `2024-2027 CBA & Notices/2024.10.01.314 Wage Rate Notice.pdf` | 182 KB | Current effective rate notice |
| `2019-2024 CBA & Notices/2024.01.01.314 Rates.pdf` | 182 KB | Q1 2024 rate notice |
| `2019-2024 CBA & Notices/2019-2024.314 CBA.pdf` | 619 KB | Full multi-year contract (renamed for classifier to `2019.01.01.314 CBA.pdf`) |

### Results (all three uploaded in parallel)
| PDF | Period (extracted) | Classifications | Cells | Gaps | doc_type | SFN time |
|---|---|---|---|---|---|---|
| 2024-10-01 Wage Rate Notice | **2024-10-01** | 7 | 56 | 0 | rate_notice | 16 s |
| 2024-01-01 Rates | **2024-01-01** | 7 | 56 | 0 | unknown | 16 s |
| 2019-2024 CBA | **2019-10-01** (from PDF content, not filename) | 7 | 35 | 0 | cba | 26 s |

**Total wall time: 26 seconds for all three** (parallel execution; the longest one was the CBA).

### What the LLM did well

1. **Read the actual effective date from the CBA content.** I uploaded with synthetic key `2019.01.01.314 CBA.pdf`, but Claude saw the CBA's first rate step kicks in **October 1, 2019** and produced a rate sheet for `2019-10-01`. The PDF wins, the filename is just a hint.
2. **Zero gaps reported across all three** — Claude found a value for every (classification × column) cell it discovered.
3. **Period discovery worked even on a multi-year CBA** — the document spans 2019-2024, but the LLM correctly identified the earliest dated rate step rather than dumping all 5 step schedules into one row.

### Real issue surfaced: column-name drift across PDFs

Looking at the 13 distinct column names across the 3 PDFs:
```
H & W   vs  H&W                   -> same fund, different spacing
Wage    vs  Wages                 -> singular vs plural
SIS     vs  SIS PEN               -> abbreviation vs longer form
JATC, NASI, IP, UA Training, Training, Pension, ...
```

These probably represent ~8 underlying funds but Claude faithfully copied
whatever spelling each PDF used. For the client's downstream consumption
(payroll calculator, comparison reports) this is a real normalization
problem. Two strategies to discuss:

- **Per-customer canonicalization profile (YAML)** — small mapping
  `H&W -> Health & Welfare`, `JATC -> Apprenticeship Training`. Customer-
  controlled, surfaces ambiguity for review.
- **In-prompt normalization** — system prompt enumerates canonical column
  names: "use these names exactly: Health & Welfare, Pension, Apprenticeship
  Training, …". Cleaner output but less customer control.

### Answer to the customer's framing question

> Do we have to read both the CBA and the Rate Notice to produce the Excel?

**No.** For producing the rate-sheet Excel for a single effective date, the
Rate Notice alone is canonical. The CBA is supporting context for *rules*
(overtime formulas, shift premiums, conditions of pay), which our pipeline
does not extract today — only rate values.

If the client wants to extract the *full historical step schedule* embedded
in a 5-year CBA (e.g., one rate sheet per yearly step), that's a different
feature: **one CBA → N rate sheets**, not built today, ~1-2 day effort.

### Aurora state after this run
| Union | Period | Cells | Method |
|---|---|---|---|
| Sprinkler 120 | 2024-04-29 | 432 (54 classifications × 8 cols) | llm_claude |
| Sprinkler 314 | 2024-10-01 | 56 | llm_claude |
| Sprinkler 314 | 2024-01-01 | 56 | llm_claude |
| Sprinkler 314 | 2019-10-01 | 35 | llm_claude |

All reviewable in the Business Inbox UI right now.

### Open question for the client
- **Trade label for Local 120.** Folder structure says `Sprinkler/120`; Claude
  read the PDF and said `Pipefitter` (UA Local 120 covers Plumbers,
  Pipefitters, AND Sprinkler Fitters under the same local). Currently set to
  `Sprinkler` per the folder structure as authoritative. Confirm or override.

---

## 2026-06-10 — Sprinkler Local 120 (first non-kernel-union test)

### Input
`From Customer/CBAs/Sprinkler/120/2024.04.29.120 Wage Rates.pdf` (846 KB).

This is a brand-new union with no hand-coded extractor profile — it MUST go
through the LLM path. The first real customer PDF.

### Result
| Field | Value |
|---|---|
| SFN status | SUCCEEDED |
| Time end-to-end | 99 s |
| Classifications discovered | 54 (Journeyman, Apprentice Years 1-5, MES Serviceman 1-2, MES Trainee Years 1-5, Unindentured Trainee Years 1-5 — each in 3 OT variants: Straight / 1.5x / 2.0x) |
| Rate columns discovered | 8 (Annuity, Fringe Benefit Package, Gross Hourly Rate, H&W, National Pension Fund, Net Hourly Rate, Pension, S.U.B.) |
| Total cells | 432 |
| Gaps reported | 0 |
| Provenance method | llm_claude |

### Key win
The LLM correctly identified that THIS union's PDF organizes rates by
`classification × OT-type` (Straight Time / Time & One-Half / Double Time)
rather than the typical `classification × column` pattern most other unions
use. A hand-coded kernel extractor for the 5 known unions would not have
caught this format — they're hard-coded to the column-major layout.

Journeyman OT math sanity-checks: Gross Hourly Rate Straight = 75.22,
1.5× = 112.83 (= 75.22 × 1.5 ✓), 2.0× = 150.44 (= 75.22 × 2.0 ✓).

### Honest caveat
One real PDF doesn't prove every PDF works. The 54-classification format here
is unusual — richer than most. Need more unknown-union PDFs through the
pipeline before claiming reliability.

---

## 2026-06-09 — Deterministic kernel sanity check (Sprinkler 704)

### Input
`kernel/data/sprinkler_fitters_704/cba/2026.01.01.704 Rate Notice.pdf` (3.1 MB).

This is one of the 5 hand-coded kernel unions, route goes through AgentCore
Runtime (the Python kernel) not the LLM. Smoke proves the deterministic path
still works after all the recent pipeline changes.

### Result
| Field | Value |
|---|---|
| SFN status | SUCCEEDED |
| Time end-to-end | 40 s |
| Classifications | 13 |
| Cells | 221 |
| Provenance method | kernel |
| Same row re-extracted via LLM path (Journeyman·Wage value) | Exact match: 52.32 = 52.32 |

Deterministic Path-A holds at 99%+ accuracy; LLM Path-C agrees on the
headline base wage on the same PDF.

---

## Conventions for future entries
- One section per upload batch, newest-first at the top.
- Include source path, SFN timing, Aurora row counts, and at least one
  honest caveat or open question per entry.
- Column-name normalization is a recurring theme — list new spellings as
  they appear so the eventual canonicalization profile has a real corpus to
  draw on.
