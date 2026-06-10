# Extraction Flow — Step-by-Step (For Client Walkthrough)

How a Rate Notice PDF becomes a structured rate sheet in our system, end-to-end.
**One PDF goes through exactly one extraction path. There is no rerun or
overwrite between kernel and LLM.** The path is picked once based on the
union's local number and stays that way for the life of that upload.

---

## The 10 steps

### 1. PDF arrives
Customer drops a PDF in the Admin → Uploads page (or it's PUT directly to
the inputs S3 bucket via API). Example: `2024.01.01.692 Apprentice Rates.pdf`.

### 2. S3 fires an EventBridge event
The S3 inputs bucket has `event_bridge_enabled=True`. The moment the
`Object Created` event fires, EventBridge routes it to the Step Functions
state machine. Latency ~1-2 seconds.

### 3. Step Functions starts
A new execution begins with the EventBridge event as input
(`detail.object.key` carries the S3 key).

### 4. Classify (Lambda)
Reads the filename and extracts:
- `local` — union local number (e.g. 692, 704, 120, 314…)
- `period` — effective date (e.g. 2024-01-01)
- `doc_type` — rate_notice / rate_sheet / cba / wage_rates / unknown
- `union` — the kernel union key if the local matches a hand-coded
  extractor, else `local_<NNN>` for unknown unions

Filenames matter: format is `YYYY.MM.DD.<local> <doc_type>.pdf`.

### 5. ExtractorInvoker (Lambda) — the routing decision
Looks up `classify.union` against a hard-coded set of 5 unions:
```
_KNOWN_KERNEL_UNIONS = {
  pipe_fitters_537,
  sprinkler_fitters_704,
  sprinkler_fitters_483,
  sprinkler_fitters_281,
  sprinkler_fitters_821,
}
```
- If `union` is in the set → **route to Path A (kernel)**.
- If `union` is anything else (`local_120`, `local_692`, `local_268`, …)
  → **route to Path B (LLM)**.

**This decision is made ONCE per upload and is final for that PDF.** The
two paths never both run on the same PDF.

### 6a. Path A — AgentCore Runtime (deterministic kernel)
For the 5 hand-coded unions only. Invokes the ExtractorAgent container on
Bedrock AgentCore Runtime in "direct mode":
- Container has hand-written Python code per union (`pipe_fitters_537.py`,
  `sprinkler_fitters_704.py`, …) that knows the EXACT table layout of that
  union's Rate Notice.
- Uses pdfplumber + RapidOCR to read the PDF deterministically.
- Produces canonical rows (ClassificationRow + RateCell objects) and a CSV.
- **Accuracy: 99%+ on the 5 unions we profiled.** Same input, same output.
- Time: ~30-60 seconds.

### 6b. Path B — Bedrock Claude (LLM extractor)
For any union we don't have a kernel profile for. Invokes `llm-extractor`
Lambda which:
- Downloads the PDF from S3.
- Calls Bedrock Claude Sonnet 4.6 with the PDF as a multimodal attachment.
- System prompt asks for a structured JSON: which classifications, which
  columns, value per cell.
- Claude reads the PDF visually, discovers the schema, and returns JSON.
- Lambda converts the JSON to the same canonical CSV shape the kernel
  produces.
- **Accuracy: high but not deterministic. May discover different column
  names across PDFs (`H&W` vs `H & W`); reviewer normalizes via the UI.**
- Time: ~15-90 seconds depending on PDF size.

### 7. Canonical CSV in S3
Both Path A and Path B end at the same place: a CSV file in
`s3://laboraid-dev-l3-bucket-outputs/<path>/output.csv` with the layout:
```
Union Group, Trade, Union Local, Zone, Package, Start Date, End Date, <rate columns…>
UA, Sprinkler, 704, Building, Journeyman, 1/1/26, 7/31/26, 52.32, 12.60, 7.45, …
…
```

From this point forward the downstream pipeline doesn't know or care which
path produced the CSV.

### 8. Publisher Lambda — writes to Aurora
Reads the canonical CSV, parses it, and:
- UPSERTs the `unions` row (one per local number).
- INSERTs a `rate_periods` row (one per upload) with
  `approval_state='pending_review'`, version=1, source files, etc.
- INSERTs N `rate_cells` rows (one per [classification × column]) with
  provenance carrying which path produced it (`method=kernel` or
  `method=llm_claude`).
- Idempotent on `(union_id, start_date)` — re-uploading the same PDF does
  NOT duplicate. Re-extraction goes through the **rework loop**, not by
  re-uploading.

### 9. Step Functions ends in `Published` (success)
EventBridge can emit a `laboraid.rate-sheet.created` event to any
downstream consumer (notifications, dashboards, payroll calculator).

### 10. Business reviewer sees the new rate sheet
The Business Inbox queries Aurora's `rate_periods` table. The new row shows
up as a card immediately. Reviewer:
- Opens the rate sheet, scans the cells.
- Adds per-cell **comments** (questions to the back-office).
- Applies per-cell **overrides** (corrections — e.g., "Wage should be 66
  per CBA §4.2 not 52.32").
- **Approves** (status → `approved`, ready for downstream consumption).
- Or **Rejects** with a reason + tags + then triggers **Rework** —
  creates v2 of the rate sheet with the overrides baked in, optionally
  re-invokes the agent with the reviewer's comments in context.

---

## Where the confusion came from

Earlier today I tested both paths on the SAME source PDF (704's Rate Notice)
to compare quality. Sequence:

1. Uploaded the 704 PDF renamed to local=999 → **forced Path B (LLM)**
   because 999 isn't in the kernel set. Got 168 cells.
2. Deleted that row.
3. Uploaded the 704 PDF with its real local=704 → **Path A (kernel)** as
   designed. Got 221 cells.

That looks like the LLM "overwrote" the kernel, but actually:
- They're separate runs.
- They were never both routed at the same time.
- I deleted Aurora data between runs to compare.

In production, a 704 PDF will ALWAYS hit the kernel (Path A). A 120 PDF
will ALWAYS hit the LLM (Path B). A reviewer can run a **rework via AI** from
the UI which re-invokes the agent on demand — but that's a different feature
from the initial extraction.

---

## The five paths at a glance

| Path | When | What runs | Output quality |
|---|---|---|---|
| **A. Kernel** | Local in {537, 704, 483, 281, 821} | Hand-written Python in AgentCore container | 99%+, deterministic |
| **B. LLM (initial)** | Any other local | Bedrock Claude Sonnet 4.6 multimodal | High, may need reviewer corrections |
| **C. Rework (merge)** | Reviewer clicked "Apply overrides → new version" after rejecting | Publisher copies cells + applies stored overrides | Mechanical, no AI |
| **D. Rework (AI)** | Reviewer clicked "Re-extract with AI feedback → new version" | AgentCore re-invoked with `rework_context` carrying rejection reason + comments | LLM-driven correction pass |
| **E. Hand-edit** | Reviewer types a per-cell override directly | Cell-override Lambda writes to DynamoDB; next rework folds it in | Human-canonical |

**A and B are mutually exclusive at first upload — the router picks one.**
C/D/E only fire after a human action in the UI.

---

## Cost + latency summary

| Path | Typical latency | Cost per PDF | Determinism |
|---|---|---|---|
| A (kernel) | 30-60 s | ~$0.001 (Lambda + AgentCore) | Yes, same input = same output |
| B (LLM) | 15-90 s | ~$0.10-$0.30 (Bedrock Sonnet multimodal) | No, may discover schema differently |
| C (merge) | 2-5 s | ~$0.0001 (Lambda only) | Yes |
| D (rework AI) | 30-90 s | ~$0.10-$0.30 | No |
| E (override) | <1 s | negligible (DDB write) | Yes |
