# Design — Upload Grouping + Re-upload Idempotency + Versioning

Two real questions raised after the 6-PDF Sprinkler 692 upload test:

1. The Jobs page shows N independent rows for an N-PDF multi-upload — no
   way to tell which jobs came from one click.
2. What happens if a user uploads the same PDF twice? Do we overwrite?
   Keep history? Are older outputs traceable?

This doc captures the design think + the chosen MVP.

---

## Question 1: How to group multipart uploads

### Current behavior

The Admin Uploads page now accepts multiple PDFs via
`<input type="file" multiple>`. The browser fires N parallel PUT-via-presigned-URL
requests; each PDF lands in S3 → EventBridge → its own Step Functions
execution. Result: the Jobs page shows N rows with no link between them.

### Options considered

| Option | How it works | Verdict |
|---|---|---|
| **A. Browser-generated `upload_group_id` (UUID)** | Browser mints one UUID per click. Included in the `/v1/uploads` request body. `upload-presign` stores it as S3 object metadata (`x-amz-meta-upload-group-id`). Classifier reads the metadata. SFN state carries it. Publisher tags `rate_periods.source_files.upload_group_id` and the audit_log entry. | **Chosen** — clean, explicit, automatic. |
| **B. Time-window heuristic** | Jobs within N seconds of each other = batch. | Rejected — fragile, no real signal, false positives. |
| **C. UI "Batch name" form** | Reviewer types a batch label before picking files. | Rejected — adds friction; the click event IS the batch boundary. |

### Chosen architecture (A)

```
Admin UI
  ↓ (user picks N files in one click)
  generate batch_id = crypto.randomUUID()
  ↓
  for each file (parallel):
    POST /v1/uploads { filename, batch_id }
      → upload-presign returns presigned PUT URL
        with x-amz-meta-batch-id=<batch_id> baked in
    PUT to S3 with that header
  ↓
S3 object lands with batch_id in its metadata
  ↓
EventBridge → SFN
  ↓
Classifier reads s3.head_object metadata, surfaces batch_id in state
  ↓
ExtractorInvoker, llm-extractor, Publisher — all carry batch_id through
  ↓
Publisher writes:
  - rate_periods.source_files.upload_group_id = batch_id
  - audit_log.details.batch_id = batch_id (action=extracted)
Jobs DDB row stores batch_id at write
  ↓
UI Jobs page: "Batch of N (xxxxxxxx)" pill on each row; filter by batch
```

### What changes (small)
- `lambdas/api/upload-presign/handler.py`: accept `batch_id` in body, include in PUT URL signature as a required header.
- `ui/src/admin/Uploads.tsx`: generate `crypto.randomUUID()` once per onChange, pass to every `/v1/uploads` call, include in the actual S3 PUT headers.
- `lambdas/processing/classifier/handler.py`: `s3.head_object` to read x-amz-meta-batch-id, add to output.
- `lambdas/processing/publisher/handler.py`: write `batch_id` into `rate_periods.source_files.upload_group_id`.
- `lambdas/api/job-list/handler.py`: surface batch_id from the SFN execution input.
- `ui/src/admin/Jobs.tsx`: render batch grouping (collapse rows under a batch header).

Estimate: ~1 hour.

### Sibling bug to fix in the same pass

The Jobs page screenshot shows union/period columns as `—` for every
UI-uploaded job. Root cause: `job-list` parses union/period from the S3
key path (e.g., `laboraid/Sprinkler/704/...`), but UI uploads go to
`laboraid/uploads/<filename>` (flat). Fix: also parse the filename for the
local + period using the same regex the classifier uses. Single Lambda
change, ~10 lines.

---

## Question 2: Re-upload idempotency + versioning

### Current behavior (uncomfortable truth)

Re-uploading the same PDF today:
1. Full pipeline runs again (~30 s wasted Bedrock cost).
2. Publisher hits merge mode on `(union_id, start_date)`.
3. Every `(zone, package, column)` triple already exists → 0 new cells.
4. `source_files.uploads[]` grows with the duplicate filename.
5. UI shows no change. First-write wins. Older value preserved.

**This is safe (no data loss) but wasteful and confusing.** A re-upload should
either be a true no-op (identical PDF) or an explicit revision flow
(corrected PDF for the same period).

### The three real re-upload scenarios

| Scenario | Today's behavior | Desired |
|---|---|---|
| Re-upload identical PDF (mistake, network blip, duplicate click) | Wastes Bedrock call; cells unchanged; phantom upload entry | **Detect → skip pipeline entirely, return 200 "already processed"** |
| Re-upload CORRECTED PDF (same period, fixed numbers) | Wastes Bedrock call; corrected values **silently dropped** (first-write wins keeps the wrong data) | **Auto-create v2 + flag for reviewer** (the existing rework loop is the explicit path for this; reviewer can also trigger it manually) |
| Re-upload to a TRULY new period | Creates a new `rate_periods` row | Works as-is |

### Chosen approach (MVP)

1. **Content-hash dedup at the API boundary.** Browser computes
   `SHA256(file.bytes)` before upload. `/v1/uploads { filename, batch_id,
   content_hash }`. `upload-presign` (or a new `dedup-check` Lambda) checks
   a DDB table `file_hashes` keyed by `(content_hash, union_id, period)`.
   - If hit: return `{ status: "duplicate", existing_period_id, existing_s3_key }`. UI shows "already processed — go to rate sheet." No upload, no pipeline run.
   - If miss: return the presigned URL as before; record the hash on completion.
2. **No auto-revision on hash divergence.** If a different-hash PDF lands
   for the same `(union, period)`, Publisher's first-write-wins still
   applies. The customer must use the **rework loop** to revise — explicit
   reviewer action, not a magic "newest data wins." Documented.
3. **History is already preserved.** The rework loop creates
   `rate_periods` v2/v3/… with parent_version links. Each version has its
   own `output.v(N).xlsx` in S3 (older ones aren't deleted). The version
   switcher in the rate-sheet view exposes both. Just document this story.

### What changes (small)
- New DDB table: `file_hashes` PK=`content_hash`, attributes={union_id, period, period_id, s3_key, first_seen_at}.
- `lambdas/api/upload-presign/handler.py`: accept `content_hash`, check DDB, branch on hit.
- `lambdas/processing/publisher/handler.py`: on successful publish, write the hash row to DDB.
- `ui/src/admin/Uploads.tsx`: compute SHA256 with `crypto.subtle.digest` before POSTing.
- New per-file status `duplicate` distinct from `done`.

Estimate: ~45 minutes.

### Why NOT auto-version on hash divergence

Tempting: "if a different-hash PDF lands for an existing (union, period),
auto-create v2." Rejected because:
- The customer who uploads a corrected PDF may not realize they have stale
  data; auto-versioning would silently keep the old v1 visible as
  "historical" and they wouldn't notice the change.
- The rework loop already exists for this. It's reviewer-triggered and
  carries the *reason* (rejection_reason + tags + comments) — auto-versioning
  loses that context.
- Explicit > magic for a POC where every behavior must be defensible.

### What about output XLSX history?

Already handled by the rework loop:
- v1 produces `output.csv` + `output.xlsx`.
- Rework creates v2 with `output.v2.csv` + `output.v2.xlsx`.
- Older files stay in S3 (S3 lifecycle archives them after 30 d to
  intelligent-tiering, 365 d to glacier; never deleted).
- UI version switcher: `v2 · current` pill (emerald), `v1 · historical`
  pill (amber). Reviewer toggles between them via the dropdown.

Document this in `docs/extraction_flow_for_client.md` so the client sees
the audit trail story.

---

## What I'll ship next

1. Upload group ID end-to-end (UI → API → S3 metadata → SFN → Aurora → Jobs UI).
2. Fix the Jobs-page "—" columns by parsing union/period from filename.
3. Content-hash idempotency at `/v1/uploads`.
4. Append the versioning story to `extraction_flow_for_client.md`.

Total: ~2 hours. Each piece independently committable.

## Open follow-ups (deferred)

- **Pre-upload content-hash check**: today the SHA256 must be computed
  client-side. For files > ~50 MB this can lock the browser tab. Web
  Workers can offload; deferred until we see a real customer hit it.
- **Cross-customer hash collisions**: `file_hashes` is single-tenant in the
  POC; once we have multi-tenancy, the key needs `tenant#hash`.
- **Auto-version on hash divergence (opinionated)**: as discussed, not in
  the POC. Revisit if customer feedback says explicit rework is too friction.
