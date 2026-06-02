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
