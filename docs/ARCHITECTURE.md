# LaborAid Rate Engine — Architecture (one-pager)

Converts union CBA + rate-notice PDFs into reviewed, published rate sheets. Built on
AWS, fully Infrastructure-as-Code (CDK), single environment.

## Flow
```
 React SPA (CloudFront/S3, Cognito) ──HTTP API (JWT)──► API Lambdas
   │ upload PDFs → S3                                      │
   │ "Process"  → POST /v1/batches/process ──► STEP FUNCTIONS ──┐
   │                                                            ▼
   │   Plan (batch-planner) ─► Synthesize (synthesizer, Bedrock Opus 4.5,
   │        reads ALL PDFs + the union profile) ─► SynthPublish (Aurora write)
   │                                                            │
   │ review / approve / publish ◄──────── dual-control gate ◄───┘
```
Run state changes emit EventBridge events → a **job-writer** projects them into a
DynamoDB read-model the dashboards query.

## Where each thing lives — and why (polyglot persistence)
| Data | Store | Why (defensible) |
|---|---|---|
| **Rate-sheet content** (cells, wages, fringes, cohorts) + **approval/audit** | **Aurora PostgreSQL** | The authoritative business record. Relational, needs SQL reporting/exports, and approvals are **transactional** (dual-control). |
| **Operational telemetry** (jobs, run status/trace/timeline, dashboard lists) | **DynamoDB** | High-volume, event-sourced, key/time access; fast dashboards at any scale. Event-sourced from Step Functions so we never query the workflow engine live. |
| **Documents & outputs** (source PDFs, CSV, XLSX) | **S3** | Object storage; served to the UI via short-lived presigned URLs. |
| **Config** (agent toggles) | **DynamoDB** | Simple key-value. |

Principle: **right store per access pattern, one source of truth per domain.** The
dashboards read **only DynamoDB** (no live Step Functions / no Aurora list scans);
rate-sheet *content* and *approval state* stay in Aurora where transactional integrity
and reporting belong.

## Read-model (CQRS)
- **Write side:** `job-writer` Lambda consumes *Step Functions Execution Status Change*
  events and upserts the `jobs` table (status, union/period, per-stage timeline +
  durations, artifacts) — resolved **once** at the state change.
- **Read side:** `GET /v1/jobs` = one indexed query (a `by-recency` GSI). Was a live
  `ListExecutions` + N×`DescribeExecution` N+1; now flat at any scale, no 90-day cliff.

## AI extraction
- **synthesizer** (the pipeline's core) runs **Claude Opus 4.5 on Bedrock** with a PII
  guardrail (ANONYMIZE), reading all of a period's PDFs against the union's profile
  (stored in Aurora) and producing the rate sheet — every value extracted from the
  source PDFs, gaps flagged, never fabricated.
- **Strands agents on Bedrock AgentCore** provide the agentic layer (extraction +
  profile authoring today; an agentic **reviewer** is Phase 2 — see
  [`PHASE2_STRANDS_DESIGN.md`](PHASE2_STRANDS_DESIGN.md)).

## Infrastructure
- **CDK**, 9 stacks (UI, Security, Storage, AI, Processing, Validation, API,
  Orchestration, Observability). Deploy: `cdk deploy <stack>`.
- As of 2026-06-12 the CDK is **reconciled and IN_SYNC** with the live account
  (`cdk diff` = no differences across all 9 stacks; tag `cdk-reconciled-v1`).
- The SPA loads a runtime `config.json` (Cognito IDs + API URL) so one bundle targets
  any environment without a rebuild.
