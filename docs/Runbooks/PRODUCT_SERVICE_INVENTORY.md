# LaborAid Rate Engine — Service Inventory & Observability Matrix

> **Audience.** Same as [`PRODUCT_END_TO_END_FLOW.md`](PRODUCT_END_TO_END_FLOW.md). This is the slide-deck appendix you keep open in a second tab during Q&A.
> **Pairs with.** The end-to-end flow doc for narrative; this doc for "where in the stack does X live."

---

## 1. Every Lambda (17 total)

Naming convention: `laboraid-<env>-l<layer>-fn-<purpose>`. Layer mapping: l1 = security/KMS, l2 = API tier, l3 = storage/SFN tier, l4 = processing tier, l5 = agent runtime, l6 = obs tier, l7 = rendering.

| # | Function name | Runtime | Memory | Timeout | Triggered by | Calls out to | Reads | Writes |
|---|---|---|---|---|---|---|---|---|
| 1 | `…-l2-fn-upload-presign` | py3.12 | 1024 | 30s | API GW POST `/v1/uploads` | S3 presign | DDB `file_hashes` | DDB `file_hashes` |
| 2 | `…-l2-fn-ratesheet-list` | py3.12 | 1024 | 30s | API GW GET `/v1/inbox` | RDS Data API | Aurora `rate_periods` | — |
| 3 | `…-l2-fn-ratesheet-get` | py3.12 | 1024 | 60s | API GW GET `/v1/.../rate-sheets/:p` | RDS Data API + DDB + S3 presign + SFN list | Aurora `rate_periods` + `rate_cells` + DDB `overrides` | — |
| 4 | `…-l2-fn-ratesheet-approve` | py3.12 | 1024 | 30s | API GW POST `/v1/.../approve` | RDS Data API + EventBridge | Aurora `rate_periods` | Aurora UPDATE + `audit_log` |
| 5 | `…-l2-fn-ratesheet-reject` | py3.12 | 1024 | 30s | API GW POST `/v1/.../reject` | RDS Data API + EventBridge | Aurora | Aurora UPDATE + `audit_log` |
| 6 | `…-l2-fn-ratesheet-unapprove` | py3.12 | 1024 | 30s | API GW POST `/v1/.../unapprove` | RDS Data API + EventBridge | Aurora | Aurora UPDATE |
| 7 | `…-l2-fn-ratesheet-publish` | py3.12 | 1024 | 30s | API GW POST `/v1/.../publish` | RDS Data API + EventBridge | Aurora (gate read) | Aurora UPDATE |
| 8 | `…-l2-fn-ratesheet-audit` | py3.12 | 1024 | 30s | API GW GET `/v1/.../audit` | RDS Data API | Aurora `audit_log` | — |
| 9 | `…-l2-fn-ratesheet-rework` | py3.12 | 1024 | 60s | API GW POST `/v1/.../rework` | RDS Data API + SFN | Aurora + new SFN start | Aurora INSERT v+1 |
| 10 | `…-l2-fn-cell-override` | py3.12 | 1024 | 30s | API GW POST `/v1/.../overrides` | DDB | DDB `overrides` | DDB `overrides` |
| 11 | `…-l2-fn-cell-comment` | py3.12 | 1024 | 30s | API GW POST `/v1/.../comments` | DDB | DDB `review` | DDB `review` |
| 12 | `…-l4-fn-classifier` | py3.12 | 1024 | 5 min | SFN ExtractViaAgent | Bedrock Claude + S3 GetObject | S3 inputs | — |
| 13 | `…-l4-fn-ocr-preprocess` (**NEW**) | py3.12 | 1024 | 15 min | SFN OCRPreprocess | Textract sync/async + S3 PutObject | S3 inputs | S3 outputs (`<key>.layout.json`) |
| 14 | `…-l4-fn-llm-extractor` | py3.12 | 2048 | 15 min | extractor-invoker for unknown locals | Bedrock Claude + S3 | S3 inputs + outputs (`layout.json`) | S3 outputs (canonical CSV) |
| 15 | `…-l3-fn-extractor-invoker` | py3.12 | 1024 | 15 min | SFN ExtractViaAgent | bedrock-agentcore.InvokeAgentRuntime OR Lambda invoke (llm-extractor) | classify input | — |
| 16 | `…-l4-fn-publisher` | py3.12 | 1024 | 5 min | SFN PublishToAurora | RDS Data API + Lambda invoke (xlsx-renderer) + EventBridge | S3 outputs (canonical CSV) | Aurora + `audit_log` |
| 17 | `…-l7-fn-renderer-xlsx` | py3.12 | 1024 | 60s | publisher invoke | S3 GetObject + S3 PutObject (vendored openpyxl) | S3 outputs (canonical CSV) | S3 outputs (final_ratesheet.xlsx) |

**Layers attached:** all 17 carry the AWS Lambda Powertools v3 layer (Logger + Tracer + Metrics). `ocr-preprocess`, `llm-extractor`, `xlsx-renderer` additionally vendor their own Python deps in the deployment zip (pypdf / no extra runtime / openpyxl + et_xmlfile respectively).

---

## 2. Step Functions main pipeline — every state

State machine name: `laboraid-dev-l3-sfn-main`. Type: STANDARD. Tracing: enabled. Log level: ALL.

| State | Type | Input | Output | Retries | Catch | Notes |
|---|---|---|---|---|---|---|
| **OCRPreprocess** | Task (Lambda) | `$.detail` | `$.ocr_result.ocr` | 2× on Lambda 5xx + `States.TaskFailed`, 5s base, 2× backoff | (none — Lambda handles internal errors) | NEW. Detects text layer; Textract fallback. |
| **FlattenOcr** | Pass | `$.ocr_result.ocr` + `$.detail` | `{detail, ocr}` | — | — | Reshapes state. |
| **Classify** | Task (Lambda) | `{s3_key: "$.detail.object.key"}` | `$.classify` | 3× on `Lambda.*Exception` + `States.TaskFailed` | `States.ALL` → `PipelineFailed` | Filename regex first; Claude fallback. |
| **GetAgentConfig** | Task (DDB GetItem) | key `{agent_name: "ExtractorAgent"}` | `$.agentCfg` | (DDB native retry) | — | KMS-decrypt the agent-config row. |
| **AgentEnabled** | Choice | `$.agentCfg.Item.enabled.BOOL` | branch | — | — | Kill-switch lives here. |
| **ExtractViaAgent** | Task (Lambda) | `{classify, ocr}` | `$.extract` | 2× on `Lambda.*Exception`, 5s base, 2× | (none — top-level catch) | Routes to AgentCore or llm-extractor. |
| **PublishToAurora** | Task (Lambda) | `$.extract` | `$.publishtoaurora` | 3× | (none) | Aurora upsert + M3 validate + render invoke. |
| **Published** | Succeed | $ | — | — | — | Terminal success. |
| **AgentDisabledSkip** | Succeed | $ | — | — | — | Terminal success (kill-switch path). |
| **PipelineFailed** | Fail | `error="PipelineError"`, `cause="See execution input"` | — | — | — | Top-level catch target. |

Top-level catch is attached to `Classify` (errors=`States.ALL`, `result_path="$.error"`). Catch targets `PipelineFailed`.

---

## 3. Bedrock surface

| Model / runtime | Used by | Why this model | Cost lever |
|---|---|---|---|
| Cross-region inference profile `us.anthropic.claude-sonnet-4-5-20251019-v1:0` | Classifier + LLM extractor + Strands ExtractorAgent tool calls | Sonnet 4.6 — vision-capable, strong on JSON, in our inference profile region pool. | `max_tokens=16000` cap on extractor; classifier under 2k. Caching enabled via ephemeral cache_control on system prompts. |
| Bedrock Guardrail (`BEDROCK_GUARDRAIL_ID` env) | Classifier + LLM extractor | Compliance gate — blocks PII / restricted content from leaking into canonical CSVs. | `DRAFT` version in dev; pinned in prod. |
| AgentCore Runtime (ExtractorAgent) | extractor-invoker | Hosts the deterministic Python kernel inside a Strands agent shell; AgentCore handles container scaling + IAM. | Runtime is single-tenant in POC; production splits per tenant. |

---

## 4. Textract surface (new June 10)

| API | Used when | Latency | Cost | Failure mode |
|---|---|---|---|---|
| `AnalyzeDocument(FORMS, TABLES)` sync | Single-page scanned PDF | ~3-8 s | $15 / 1k pages (FORMS+TABLES) | `UnsupportedDocumentException` → caught, fallback to vision |
| `StartDocumentAnalysis` async + `GetDocumentAnalysis` poll | Multi-page scanned PDF | 30 s – 13 min | same per-page rate | Timeout caught at 13 min; falls back to vision |

`ocr-preprocess` IAM: `textract:AnalyzeDocument | StartDocumentAnalysis | GetDocumentAnalysis` on `Resource: "*"` (Textract is regional, no ARN scoping).

---

## 5. Storage inventory

### S3 buckets (6)

| Bucket | Purpose | Encryption | Versioning | Lifecycle |
|---|---|---|---|---|
| `…-l3-bucket-inputs` | Customer-uploaded PDFs | SSE-KMS (`alias/laboraid/dev/master`) | ON | none (POC) |
| `…-l3-bucket-outputs` | Canonical CSV, xlsx, gap report, Textract layout JSON | SSE-KMS | ON | none (POC) |
| `…-l3-bucket-assets` | CDK assets, Lambda packages | SSE-S3 | OFF | 90-day expiration |
| `…-l3-bucket-logs` | Cross-bucket access logs | SSE-S3 | OFF | 1-year archive |
| `…-l1-bucket-web` | UI web bundle | SSE-S3 | ON | none |
| `…-l3-bucket-cdk` | CDK staging | SSE-S3 | OFF | 30-day |

All buckets: `BlockPublicAccess` full, TLS-only bucket policy, same-account-only condition.

### DynamoDB tables (7)

| Table | PK / SK | Used by | Purpose |
|---|---|---|---|
| `…-l3-ddb-file-hashes` | `tenant#content_hash` | upload-presign | Dedup |
| `…-l3-ddb-jobs` | `job_id` | (legacy) | Per-execution metadata |
| `…-l3-ddb-files` | `key`, `period#filename` | (POC unused) | Per-file metadata |
| `…-l3-ddb-review` | `period_id`, `cell_id#timestamp` | cell-comment | Reviewer comments |
| `…-l3-ddb-overrides` | `tenant#union#period`, `cell_id#timestamp` | cell-override + ratesheet-get | Per-cell overrides; newest wins |
| `…-l3-ddb-agent-config` | `agent_name` | SFN GetAgentConfig | ExtractorAgent on/off + per-agent config |
| `…-l3-ddb-tenants` | `tenant_id` | (POC partial) | Per-tenant config |

All tables: PROVISIONED-PAY-PER-REQUEST, CMK-encrypted, PITR enabled.

### Aurora Serverless v2 PostgreSQL

Cluster: one. Database: `laboraid`. ACU range: 0.5-4 in dev. RDS Data API enabled.

| Table | Key | Purpose |
|---|---|---|
| `unions` | `id UUID` | One row per (trade, local) |
| `rate_periods` | `id UUID` | One row per (union_id, start_date). Carries `approval_state`, `reviewed_by`, `approved_by`, `canonical_json`, `source_files`. |
| `rate_cells` | `id UUID`, FK `period_id` | One row per zone × package × column. Carries `value`, `confidence`, `provenance::jsonb`. |
| `audit_log` | `id BIGSERIAL` | Append-only: `tenant`, `actor`, `action`, `details::jsonb`, `at`. |

**Constraints:**
- `publish_requires_approval`: `approval_state IN ('pending_review','pending_approval','approved','rejected','published')`
- `dual_control_required`: `approval_state <> 'approved' OR (reviewed_by IS NOT NULL AND approved_by IS NOT NULL AND reviewed_by <> approved_by)`

---

## 6. Observability cross-reference

| Signal | Source | Retention | Use case |
|---|---|---|---|
| Lambda structured JSON | CloudWatch Logs `/aws/lambda/laboraid-*` | 30 days | Debug a single execution |
| SFN execution history | Step Functions console | 90 days | Replay any state, see input/output per state |
| X-Ray trace | X-Ray service map | 30 days | End-to-end latency breakdown per request |
| Bedrock token metrics | CloudWatch `AWS/Bedrock` | 15 months | Cost attribution; throttle headroom |
| Textract job metrics | CloudWatch `AWS/Textract` | 15 months | Cost; async job duration histogram |
| Aurora `audit_log` | RDS | retained with DB | Compliance audit; "who approved 483 v3?" |
| DDB `jobs` table | DDB | TTL 90 days | Operator dashboard view |
| EventBridge metrics | CloudWatch | 15 months | Invocation success/failure on every bus rule |

### Recommended CloudWatch alarms (P0)

| Alarm | Threshold | Notify |
|---|---|---|
| SFN ExecutionsFailed | > 0 over 5 min | PagerDuty + #ops Slack |
| Bedrock ThrottlingException | > 0 over 1 min | #ops Slack |
| Aurora CPUUtilization | > 80% for 5 min | #ops Slack |
| Lambda DurationMaximum on llm-extractor | > 13 min | #ops Slack |
| OCRPreprocess errors | > 1 in 5 min | #ops Slack |
| DDB SystemErrors on any table | > 0 | #ops Slack |

(None of these alarms are wired in the POC stack today — they're the first-week production work.)

---

## 7. Cost model (rough, dev-tier)

| Item | Unit | POC volume | Cost |
|---|---|---|---|
| Lambda invocations (avg 4s, 1 GB) | per 1M | ~50/day | $0.20/day |
| Bedrock Claude Sonnet 4.6 | per 1M input tokens | ~150k/day (5 unions × 30k tokens) | $0.45/day |
| Textract FORMS+TABLES | per 1k pages | 0 today (all digital) | $0 |
| Aurora Serverless v2 | per ACU-hour | 0.5 ACU avg, 24h | $1.44/day |
| S3 storage | per GB-month | ~2 GB | $0.05/month |
| S3 GET/PUT | per 1k requests | ~5k/day | $0.05/day |
| CloudWatch Logs ingest | per GB | ~0.5 GB/day | $0.25/day |
| Step Functions transitions (STANDARD) | per 1k transitions | ~150/day | <$0.01/day |
| **Total dev** | | | **~$2.50/day** |

Production scaling: linear with the number of unions onboarded; the LLM extractor is the dominant cost lever per-unit (Bedrock tokens).
