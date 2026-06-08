# LaborAid Rate Engine — Architecture

POC architecture for the CBA → canonical rate-sheet engine. This summarizes the
deployed system; the build-ready detail lives in
[`09_Technical_Implementation_Spec.md`](09_Technical_Implementation_Spec.md).

> **Status update (2026-06-05) — see [`STATUS.md`](STATUS.md).** The kernel now
> covers **all 5 POC unions** (537, 704, 821, 483, 281) through a CI accuracy gate;
> 281 & 821 (indenture cohorts, 4 zones) are fully wired. Added a **Stage 6
> completeness-coverage critic** (`kernel/pipeline/critic.py`), fixed a multiplier
> rounding bug (Decimal-multiply) and the 537 wage source, and hardened the agent
> model calls (guards + prompt caching + profile-driven checksum).

## Shape

One Strands `ExtractorAgent` on AgentCore Runtime, wrapping Ashwani's deterministic
extraction **kernel**, with deterministic Lambdas for everything else. Eight CDK
(Python) stacks, ARM64 throughout, single region (`us-east-1`).

```
Upload (Admin UI) ─▶ S3 inputs ─▶ EventBridge ─▶ Step Functions main pipeline
   1. Classify (Lambda)
   2. Extract  (ExtractorAgent / AgentCore — wraps kernel.pipeline.extract)
   3. Validate (checksum + range + confidence Lambdas, in parallel)
   4. Gate     (all passed? → render ; else → review queue)
   5. Render   (xlsx + csv + articles Lambdas)
   6. Publish  (Aurora rate_periods/rate_cells; SNS rate-sheet.published)
        │
   Business UI ── review ── Approve/Reject ──▶ Admin Publish (409 unless approved)
```

## Stacks (dependency order)

| Stack | Layer | Contents |
|---|---|---|
| Security | — | KMS CMK, Cognito (4 groups, MFA), hosted-UI domain |
| Storage | L3 | 6 S3 buckets, 7 DynamoDB tables, Aurora Serverless v2 (+ schema-init) |
| Ai | L5 | Bedrock PII Guardrail |
| Processing | L4/L5 | Classifier Lambda, ECR, ExtractorAgent AgentCore Runtime |
| Validation | L6/L7 | Validator + renderer Lambdas, 3 SNS topics, EventBridge bus, SES, Slack |
| Api | L2 | HTTP API GW, Cognito authorizer, WAF, 19 Lambdas |
| Ui | L1 | Private S3 + CloudFront + OAC, serves the React SPA |
| Orchestration | L3 | Step Functions main pipeline + S3-upload trigger |
| Observability | — | 5 dashboards, 6 alarms, CloudTrail |

## Personas (two-persona SPA, one Vite build)

- **Admin / Operations** (`/admin/*`, Cognito `Admins`/`Operations`) — keep the
  engine healthy: uploads, jobs, agents (enable toggle), profiles, audit, costs.
- **Business** (`/business/*`, Cognito `Business`) — review each rate sheet,
  override low-confidence cells, **Approve / Reject**, sign off before publish.

## Approval gate

`rate_periods.approval_state` flows `pending_review → approved | rejected →
published`. The publish API returns **409 unless `approval_state='approved'`**
(also a DB CHECK constraint). Approve requires an empty review queue; reject
requires a reason.

## Conventions

- Naming: `laboraid-{env}-{layer}-{type}-{purpose}`.
- 13 mandatory tags on every resource via a CDK Aspect.
- KMS CMK on S3/DDB/Aurora/SNS/Secrets; TLS-only buckets; IAM least-privilege
  per-Lambda/agent; X-Ray + structured Powertools logs.
- Never fabricate: unreadable cells are blank + flagged in `<union>.gaps.md`.
