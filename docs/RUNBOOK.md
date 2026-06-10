# LaborAid Rate Engine — Runbook

Operational procedures for the POC. Audience: NBS + LaborAid operations.

## Deploy

```bash
# Prereqs: AWS creds for the target account, Bedrock model access enabled
# (Claude Sonnet 4.x, Haiku, Titan Embed), AgentCore Runtime available in us-east-1.
cd cdk
uv sync
export CDK_DEFAULT_ACCOUNT=<acct>  CDK_DEFAULT_REGION=us-east-1
npx cdk bootstrap
npx cdk synth                 # acceptance gate: exits 0 (9 stacks)
npx cdk deploy --all          # deploy order resolved by dependencies

# UI build (must run before deploying the Ui stack)
cd ../ui && corepack pnpm install && corepack pnpm build   # -> ui/dist
```

Select prod with context: `npx cdk deploy -c env=prod --all`.

## Build / push the ExtractorAgent image

```bash
# Build context is the repo root so the kernel is included.
docker build -f agents/extractor/Dockerfile -t laboraid-extractor .
# Tag + push to the ECR repo laboraid-{env}-l5-ecr-agent-extractor, then the
# AgentCore Runtime picks up :latest.
```

## Process a document (happy path)

1. Admin uploads a Rate Notice PDF at `/admin/uploads` (presigned PUT to the
   inputs bucket).
2. S3 `Object Created` → EventBridge → Step Functions `laboraid-{env}-l3-sfn-main`.
3. Pipeline: classify → extract (agent) → validate → render → publish.
4. Business reviews at `/business/inbox`, approves; Admin publishes.

## Common tasks

| Task | How |
|---|---|
| Retry a failed job | `/admin/jobs/:id` → Retry (or `POST /v1/jobs/{id}/retry`) |
| Abort a job | `POST /v1/jobs/{id}/abort` (Admins) |
| Disable an agent | `/admin/agents` toggle (Admins) → `agent-config` DDB `enabled=false`; the Step Function bypasses it |
| Re-run a rejected sheet | Business rejects → `rate-sheet.rejected` event → re-upload / re-run |
| Inspect the audit trail | `/admin/audit` or `GET /v1/audit` |

## Alarms (→ failures SNS topic → email + Slack)

| Alarm | Threshold | First response |
|---|---|---|
| `pipeline-failure` | >3 failed/1h | Check the failed execution input + per-stage logs |
| `bedrock-spend` | >$100/day | Inspect agent escalation rate; throttle if runaway |
| `aurora-cpu` | >80%/15m | Check query load; Serverless v2 should scale |
| `ddb-throttling` | any | Confirm on-demand mode; check hot partitions |
| `review-queue-depth` | >50 cells | Notify reviewers; check extraction confidence |
| `api-5xx` | >1%/5m | Check API Lambda logs + WAF blocks |

## Kernel regression guard

```bash
cd kernel && uv run python pipeline/run.py --all
# Expected accuracy: 704 >= 99.0%, 483 Building = 100%, 537 >= 67%.
```

Never edit `kernel/` by hand — it is a `git subtree`. New extractors go through
the kernel's own `.claude/harness/`.

## Rollback

`npx cdk deploy` is idempotent; to roll back a stack, redeploy the prior commit.
Buckets + Aurora are `RETAIN` in prod — data survives stack deletion.

---

# Q&A — "What if…"

Practical answers for the questions that come up in conversation. Append
new entries at the end of each section. Keep answers short (one short
paragraph) and link the relevant code path or commit.

Last verified: 2026-06-10 against commit `750a65d`.

## Uploads & batching

### Q. What happens if I upload the same PDF twice?
Nothing wasteful. The browser hashes the file (SHA-256) before requesting
a presigned URL. `/v1/uploads` checks the `file_hashes` DDB table and
returns `{status: "duplicate", existing_period_id: <uuid>}` — no S3 PUT,
no SFN, no Bedrock spend, no duplicate `rate_period` row. The UI marks
that file "duplicate (skipped)" with a pointer to the existing period.

### Q. What if I upload a different version of a PDF I already sent?
Different bytes = different hash = fresh upload. If the classifier
resolves it to the SAME `(union, start_date)`, Publisher enters **merge
mode**: new cells append to the existing `rate_period` with
`provenance.source_pdf = <new filename>`. NULL cells from the prior run
can be upgraded; non-null cells stay (first-write-wins on value
collisions). Reviewer can override via the UI.

### Q. What if I upload the CBA without the Rate Notice?
The CBA filename (`YYYY.MM.DD-YYYY.MM.DD.<local> CBA.pdf`) has a date
range, not an anchor. With no anchor in the batch the classifier falls
back to the range's start date (e.g. `2024-08-01`) with `confidence=low,
method=filename_range_only` and logs a warning. **Recommendation:**
always send the Rate Notice in the same batch.

### Q. What if I multi-select two Rate Notices for different periods + one CBA?
Browser picks the most recent Rate Notice date as the batch anchor. The
CBA inherits that anchor; the older Rate Notice still uses its own
filename date. Two separate `rate_period` rows result — the CBA only
merges into the newer one. To merge the CBA into the older period,
upload them in a separate batch.

### Q. How does the system know the Rate Notice and CBA "go together"?
The browser stamps both with the same `batch_id` (UUID) and encodes the
anchor `batch_period` in the S3 key:
`laboraid/uploads/<batch_id>/<YYYY-MM-DD>/<filename>`. Both PDFs route
through the pipeline independently; Publisher's merge mode joins them on
`(union, start_date)` in Aurora.

### Q. How big can a batch be?
No hard cap. Each file is its own SFN execution (parallel). Practical
ceiling is Lambda concurrency on the LLM extractor (~50 with current
reserved concurrency); beyond that you'll see throttling, which
extractor-invoker retries adaptively (`max_attempts=8, mode=adaptive`).

## Extraction paths

### Q. Which extractor handles my PDF?
| Doc type | Union local | Path |
|---|---|---|
| Rate Notice / Rate Sheet | 537, 704, 483, 281, 821 | **Kernel** (hand-coded Python via AgentCore) |
| Rate Notice / Rate Sheet | any other local | **LLM** (Bedrock Claude Sonnet 4.6) |
| CBA (`*.<local> CBA.pdf`) | any | **LLM** (CBA-specific prompt) |
| Apprentice Scale | any | **LLM** (apprentice-scale prompt) |
| Unknown | any | **LLM** generic rate-notice prompt; flagged for review |

Classifier decides this from filename keywords; extractor-invoker routes
accordingly (`lambdas/processing/extractor-invoker/handler.py:_route`).

### Q. Why does the kernel give "Apprentice Class 1-5 Wage = NULL" on Residential 483?
The `2026.01.01.483 Rate Notice.pdf` contains only Building/Commercial
rates. The Residential package lives in the CBA. The Residential
Apprentice (Trainee) Scale lives in a third document Local 483 maintains
separately. The kernel correctly emits NULL with reason "residential
apprentice scale not in provided docs" and surfaces it in the gap banner.

### Q. Can the LLM fill those NULL cells from the CBA?
For Residential **Foreman + Journeyman** benefits (Pension, Vacation,
SIS, J&A Training, etc.) — yes, the CBA carries them and the CBA prompt
extracts them. For Residential **Apprentice Wages** — no, this CBA only
has the **Building** Article 15 percentages (40%, 42.5%, …) which do NOT
apply to Residential. The prompt is explicit: don't fabricate. Wage cells
stay NULL until the customer provides a Residential Apprentice Scale.

### Q. Why is Pension $7.30 instead of $7.45?
The 483 CBA states `N.A.S.I. Pension $7.30 per hour` effective 8/1/2024.
The customer's 1/1/2026 number is $7.45 — the union exercised its
package-reallocation right (referenced in the CBA but not numerically
specified for 1/1/2026). The reallocation values live in a separate
notice. Reviewer can override via the cell-override UI or upload the
reallocation notice into the same batch.

## Data provenance & gaps

### Q. How do I see where a cell's value came from?
Every `rate_cells` row carries `provenance.source_pdf` (the filename it
was extracted from). The UI's Provenance panel surfaces this per cell.
SQL: `SELECT package, column_name, value, provenance->>'source_pdf' FROM
rate_cells WHERE period_id = :id`.

### Q. What's the difference between `gap_count` and `gaps_detail`?
- `gap_count` = total NULL `rate_cells` rows on the period (recomputed
  after every Publisher run).
- `gaps_detail` = `[zone, package, column, reason]` tuples from the
  kernel/LLM, filtered to entries that are STILL NULL in Aurora. The
  Inbox banner reads this for the per-cell reasons.

### Q. The banner says "74 cells blank" but `gaps_detail` only has 7 entries. Why?
A single `gaps_detail` entry like `["Residential", "*", "Pension", ...]`
covers Pension across every Residential package (7 packages × 1 column
= 7 cells). The count is the actual NULL row count; the detail list is
grouped by the kernel's reason categories.

### Q. How do I clear a gap?
Three options, in order of fidelity:
1. **Upload the missing document into the same period.** Drop the
   Residential Apprentice Scale PDF or the package-reallocation notice
   in a fresh batch — Publisher's merge mode fills the NULL cells and
   `gaps_detail` auto-shrinks.
2. **Override the cell from the UI.** Goes into the `overrides` DDB
   table; Publisher applies it on the next rework.
3. **Reject + Rework via AI.** Use the rework bar; the AI re-extracts
   with the reviewer's comments in context.

## Operations / troubleshooting

### Q. A PDF upload completed but nothing shows up in the Inbox. Where do I look?
1. **Admin → Jobs**: every PDF that fires an SFN run is listed there
   with status + parsed union + period.
2. If the row shows `Local NNN` with an empty period, the classifier
   couldn't parse the filename — fix the filename or check the SFN
   execution input.
3. If the SFN status is FAILED, click into the job for the failing
   state's error. Typical causes: Bedrock throttle (auto-retry),
   AgentCore session timeout (kernel run > 15 min), Aurora cold start.

### Q. How do I re-extract without re-uploading?
- **From the UI**: open the rate sheet, click "Rework with AI". Creates
  a v2 of the period; original stays for audit.
- **From the CLI**: invoke `laboraid-dev-l4-fn-llm-extractor` directly
  with `{"classify": {...}, "out_s3_key": "..."}` — useful when iterating
  on prompt changes.

### Q. How do I check what Bedrock actually returned for a CBA?
On JSON parse failure, the LLM extractor writes the raw Claude response
to `s3://laboraid-dev-l3-bucket-outputs/llm-extractor-debug/<ts>.txt`.
For successful parses, the canonical CSV lives at
`s3://.../laboraid/uploads/<batch_id>/<period>/<stem>.csv`.

### Q. Can I hot-patch a Lambda without redeploying the stack?
Yes. Patch scripts live in `_TMP_/hotpatch_*.py` — each zips a single
handler and calls `update_function_code`. Faster than CDK for prompt
tweaks or single-Lambda fixes, **but anything that touches IAM, env
vars, layers, or new resources must go through CDK**.

### Q. How do I roll back a single Lambda?
```bash
git checkout <prior-sha> -- lambdas/processing/llm-extractor/handler.py
python _TMP_/hotpatch_gaps.py    # or whichever script targets that fn
git checkout HEAD -- lambdas/...  # restore working tree
```

## Append new questions below this line

When you hit a "what happens if…" worth documenting, add it under the
right section with the date and commit you verified against. Keep
answers under ~150 words and link the relevant code path.
