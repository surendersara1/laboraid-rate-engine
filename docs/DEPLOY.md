# Deploy runbook

One script brings up the whole system in the correct order:
[`scripts/deploy.sh`](../scripts/deploy.sh). This doc explains the order, the
prerequisites, and how to verify a PDF flows all the way to outputs + Aurora.

## What runs where (no EKS / no Kubernetes)

| Component | Compute |
|---|---|
| **ExtractorAgent** (kernel + LLM fallback) — the only container | ECR image → **Bedrock AgentCore Runtime** |
| Classifier, validators, renderers, 19 API handlers | **Lambda** (Python, ARM64) |
| Orchestration | **Step Functions** |
| `unions / rate_periods / rate_cells / audit_log` | **Aurora Serverless v2** (Postgres, DB `laboraid`) |
| files / jobs / review / overrides / cadence / idempotency / agent-config | **DynamoDB** (7 tables) |
| PDFs in / rendered sheets out | **S3** |
| Admin + Business SPA | **S3 + CloudFront** |

**ECR holds exactly one image** (the agent). There is no EKS, ECS, or Fargate.

## Prerequisites

- An AWS account, **region `us-east-1`** (hard requirement — Bedrock + AgentCore).
- AWS credentials with deploy rights (env vars or a profile).
- **Amazon Bedrock model access enabled** for Claude **Sonnet** + **Haiku**
  (Bedrock console → Model access). The agent calls these at runtime.
- **Bedrock AgentCore Runtime** available/allow-listed in the account.
- Tools on PATH: `aws`, `docker` (with `buildx`), `node`/`npx`, `uv`, `corepack` (pnpm).

## Why the order matters (the one non-obvious thing)

`ProcessingStack` **imports** the ECR repo by name and its **AgentCore runtime
references `{repo}:latest` at deploy time**. So the repo *and* the image must
exist **before** `cdk deploy`. A naive `cdk deploy --all` on a fresh account would
fail. `deploy.sh` enforces:

```
build UI  →  create ECR repo  →  build & push image  →  bootstrap  →  cdk deploy --all  →  enable agent
```

(Before this, the repo was created *inside* ProcessingStack alongside the runtime —
the classic chicken-and-egg. The stack now does `ecr.Repository.from_repository_name`,
and `deploy.sh` owns creating the repo + pushing the image.)

## Deploy

```bash
# dev
scripts/deploy.sh --env dev

# prod (non-interactive)
scripts/deploy.sh --env prod --yes
```

Useful flags: `--skip-ui`, `--skip-image`, `--skip-bootstrap` (after the first
run), `--smoke` (also run the local kernel regression at the end). The script is
idempotent — re-run it to re-push the image and re-deploy.

What it does, in order:
1. **Preflight** — checks tools + creds; resolves the account from STS.
2. **UI bundle** — `pnpm build` → `ui/dist` (the Ui stack needs it present).
3. **ECR repo** — `aws ecr create-repository` (idempotent, scan-on-push).
4. **Image** — `docker buildx build --platform linux/arm64` from the repo root
   (the Dockerfile copies `kernel/` + `agents/extractor/` + `kernel/profiles/`),
   then push `:latest`.
5. **Bootstrap** — `cdk bootstrap aws://<acct>/us-east-1` (idempotent).
6. **Deploy** — `cdk deploy --all -c env=<env>`; CDK resolves the 9-stack order.
   Aurora's schema-init custom resource applies the (idempotent) DDL.
7. **Enable the agent** — writes `{agent_name: ExtractorAgent, enabled: true}` to
   the `agent-config` table; Step Functions reads this to decide whether to invoke
   the agent (without it, extraction is skipped and everything routes to review).

## Verify end-to-end

Upload a Rate Notice to the inputs bucket and watch it flow:

```bash
aws s3 cp 'kernel/data/sprinkler_fitters_704/cba/2026.01.01.704 Rate Notice.pdf' \
  s3://laboraid-dev-l3-bucket-inputs/laboraid/Sprinkler/704/2026-01-01/ --region us-east-1
```

Then confirm each hop:
- **Step Functions** — a new execution of `laboraid-dev-...` main pipeline (Console
  → Step Functions) goes Classify → Extract (AgentCore) → Validate → Render → Publish.
- **Outputs S3** — `s3://laboraid-dev-l3-bucket-outputs/laboraid/Sprinkler/704/2026-01-01/`
  gets the rendered `xlsx` + `csv`.
- **Aurora** — a `rate_periods` row + ~13 `rate_cells` (RDS console Query Editor, DB
  `laboraid`, or the Data API).
- **DynamoDB** — a `files` row for the upload, a `jobs` row for the execution.
- **UI** — the CloudFront URL (in `cdk/cdk-outputs.<env>.json`) shows the sheet in
  the Business inbox with the provenance panel.

Deterministic kernel check (no AWS, proves the extraction math the agent runs):

```bash
cd kernel && uv run python pipeline/run.py --all --min-accuracy 99.0   # all 5 unions ≥99%
```

## Manual fallback (what the script automates)

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text); REGION=us-east-1; ENV=dev
REPO=laboraid-$ENV-l5-ecr-agent-extractor
cd ui && corepack pnpm install && corepack pnpm build && cd ..
aws ecr create-repository --repository-name $REPO --image-scanning-configuration scanOnPush=true --region $REGION
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker buildx build --platform linux/arm64 -f agents/extractor/Dockerfile -t $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest --push .
cd cdk && uv sync && npx cdk bootstrap aws://$ACCT/$REGION && npx cdk deploy --all -c env=$ENV --require-approval never
```

## Teardown

```bash
cd cdk && npx cdk destroy --all -c env=dev
# dev ECR repo is NOT managed by CDK anymore — remove it explicitly if desired:
aws ecr delete-repository --repository-name laboraid-dev-l5-ecr-agent-extractor --force --region us-east-1
```
Prod buckets/Aurora use RETAIN policies and survive destroy by design.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| AgentCore runtime create fails: image not found | The `:latest` image isn't in ECR. Re-run without `--skip-image`. |
| `exec format error` / agent won't start | Image built for the wrong arch — must be `linux/arm64` (the script forces it; ensure `docker buildx` + qemu on x86 hosts). |
| Bedrock `AccessDenied` at extract time | Enable Claude Sonnet + Haiku in Bedrock → Model access for this account/region. |
| `cdk deploy` wants credentials at synth | Only with `-c domain_name=...`; the default dev/prod synth is credential-free. Set `CDK_DEFAULT_ACCOUNT` (the script does). |
| Pipeline runs but every sheet goes to review | The `agent-config` row is missing/`enabled:false` — re-run step 7 (the script seeds it). |
| Not in `us-east-1` | Unsupported — Bedrock/AgentCore. Use `us-east-1`. |
