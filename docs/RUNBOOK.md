# LaborAid Rate Engine — Runbook

**Single operational runbook.** Supersedes all prior runbook/deploy/demo docs.
Last updated 2026-06-12. Architecture: see `docs/DESIGN.md`.

---

## Environment

| Resource | Value |
|---|---|
| AWS profile / region / account | `laboraid` / `us-east-2` / `908106425069` |
| API (HTTP API) | `https://clp6kku691.execute-api.us-east-2.amazonaws.com` |
| UI (CloudFront) | `https://d3ggwschjt81wu.cloudfront.net` |
| Cognito pool / client | `us-east-2_CC90iICJt` / `7g8l4dfcirofqtkafoi1u3869g` |
| State machine | `laboraid-dev-l3-sfn-main` (`Plan → Synthesize → SynthPublish`) |
| Buckets | inputs `…-l3-bucket-inputs`, outputs `…-l3-bucket-outputs`, SPA `…-l1-bucket-spa` |
| Model | `us.anthropic.claude-opus-4-5-20251101-v1:0` (Bedrock) |

**Users (Cognito, all in Business group):** `demo@laboraid.test`,
`team-share@laboraid.test`, `e2e@laboraid.test`. Dual-control needs two distinct
users (a reviewer and a different approver).

## Process a rate sheet (operator flow)

1. **Uploads** → stage all PDFs for one period (CBA + rate notices) → **Process this batch**.
2. **Jobs** → watch the run; **Details** shows the stage timeline + per-stage call
   trace (classifier, Bedrock, Aurora, S3) + artifacts (source PDFs, CSV, XLSX).
3. **Business → Inbox** → open the sheet → review the grid + provenance.
4. **Mark Reviewed** → **Approve** (as a *different* user) → **Publish**. Or
   **Reject** with a reason + tag.

## Onboard a new union

Two paths, both LLM-based (structure extracted from the CBA, never from an answer
sheet):

- **Automatic:** just upload the union's CBA + rate notices and **Process**. If no
  profile exists, the synthesizer auto-onboards (profile-builder reads the CBA →
  saves `unions.profile_yaml`) and continues in the same run.
- **Manual / refine:** Admin **Profiles** tab to view/edit a union's profile; or
  invoke `profile-builder` directly with `{local, trade, docs:[{s3_key,filename}]}`.

Profiles live in Aurora `unions.profile_yaml` — adding/editing a union is a data
change, **no redeploy**.

## Deploy (current method: boto3 hot-patch)

> CDK is **behind** the live state — do **not** `cdk deploy`/`destroy` until the
> one-time reconciliation is done (it would revert the live system). Deploy via
> boto3 `update_function_code`, matching how the running system was built.

- **Lambda code:** zip `handler.py` (+ `master_data.py` / `pdf_utils.py` and the
  vendored `pypdf`/`openpyxl` for the synthesizer) and `update_function_code`.
- **UI:** `cd ui && npx vite build`, then upload `dist/` to the SPA bucket and
  invalidate CloudFront (`_TMP_/redeploy_ui.py`).
- **SFN definition / IAM / API routes:** applied via boto3; snapshot the SFN
  definition before changing it (rollback = re-apply the snapshot).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Upload says "already processed" | Content-hash dedup (DDB `…-file-hashes`). Use **Force re-process**, or clear the hash row. |
| API returns 500 on a review action | The live Lambda may be a **stale build** — redeploy that handler from repo source. |
| Wages wrong on a multi-notice union | The CBA holds Foreman/apprentice formulas; ensure it isn't dropped by the 100-page budget (budget keeps CBA + current notice, drops old notices). |
| "Source PDF / job metadata not available" | Period's `source_files.uploads` empty — re-run; synth-publish records source-PDF keys. |
| `dual_control_violation` on Approve | Working as designed — approve as a **different** user than the reviewer. |
| Profile missing / generic output | No `unions.profile_yaml` for the local — include the CBA so auto-onboard can build it. |
| Bedrock "100 PDF pages" error | Too many/large PDFs in one request — the synthesizer caps the CBA + drops old notices; reduce the batch if needed. |

## Reset for a clean demo

- Clear dedup: scan + delete all rows in `…-l3-ddb-file-hashes`.
- Reset a period to reviewable: `UPDATE rate_periods SET approval_state='pending_review',
  reviewed_by=NULL, approved_by=NULL, published_by=NULL WHERE …`.
- Empty the Inbox (DESTRUCTIVE): `DELETE FROM rate_cells; DELETE FROM rate_periods;`
  (keeps `unions` + profiles).

## Validation

Offline diff a union's live output vs the client sheet by (zone, package, cohort)
key across all fund columns. 281 = row-for-row cell-exact; 704 = all 13
classifications correct. Re-validate any union after profile/objective changes.
