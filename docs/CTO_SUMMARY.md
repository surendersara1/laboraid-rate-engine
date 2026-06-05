# LaborAid Rate Engine POC — Build Summary (CTO read)

**Customer:** LaborAid · **Delivered by:** NorthBay Solutions (AWS Premier Tier)
**Engagement:** Signed SOW · 2-week build · $30,900 NBS PS + $25K AWS funding + $900 NBS funding ($5K net to customer)
**Repository:** `github.com/surendersara1/laboraid-rate-engine` · Branch `feat/aws-strands-integration` (PR-ready)
**Status as of audit:** **Build complete. All 8 audit blockers fixed. All quality gates green. Ready for PR, merge, and first deploy.**

---

## 1. What the POC does

Converts union Collective Bargaining Agreement (CBA) PDFs into structured rate sheets that feed LaborAid's benefit-fund Calculator. End-to-end:

```
PDF upload (Admin UI)
  → S3 inputs (KMS, TLS-only)
  → EventBridge → Step Functions main pipeline
  → Classify (Lambda) → Extract (Strands ExtractorAgent on AgentCore wrapping deterministic kernel)
  → Validate (checksum + range + confidence)
  → gate: passed → Render (xlsx/csv) → Business approval queue
                  failed → Review queue (low-confidence cells)
  → Business UI: SME reviews + Approves or Rejects (audit-logged)
  → Admin/Ops Publish (API returns 409 unless approval_state='approved')
  → Aurora + S3 outputs → LaborAid Calculator consumes via API
```

Two-persona UI under one React build:
- **Admin** (`/admin/*`) — operations: jobs, agents, profiles, audit, costs
- **Business** (`/business/*`) — review + Approve/Reject + sign-off before publish

---

## 2. SOW commitment vs delivery

| SOW deliverable (Page 6 tech stack) | Status | Implementation |
|---|---|---|
| **Strands Agents** framework | ✅ Delivered | `agents/extractor/` — `Agent` + `@tool` + `SteeringHandler` self-validation gate |
| **AWS Bedrock AgentCore** | ✅ Delivered | CDK `AwsCustomResource` calls `bedrock-agentcore:CreateAgentRuntime` |
| **AWS Bedrock** (Claude) | ✅ Delivered | Sonnet 4.6 (extraction) + Haiku 4.5 (classification) via `bedrock-runtime` |
| **AWS S3** (shared + tenant) | ✅ Delivered | 6 KMS-encrypted buckets, TLS-only policies, lifecycle to Glacier |
| **React UI** | ✅ Delivered | Vite + React 18 + TS, two personas, Cognito auth, MFA required |
| Document-Agnostic Processing | ✅ Delivered | Kernel: `pdfplumber` text + `rapidocr-onnxruntime` for scans (no API key, no system deps) |
| LLM-Centric Extraction | ✅ Delivered | Deterministic kernel first; Bedrock Claude multi-modal fallback when kernel confidence drops |
| Validation Layer | ✅ Delivered | 4 Lambdas: checksum (Total Package match), range, confidence rollup, review-router |
| Human-in-the-Loop | ✅ Delivered | Business Review UI: per-cell override, comments, **Approve / Reject with reason**, full audit trail |
| Separation of Concerns | ✅ Delivered | Raw S3 / pipeline / Aurora / Calculator API are isolated layers |

**"Agentic AI feasibility"** (SOW Page 2 Executive Summary): met via one Strands `ExtractorAgent` on AgentCore Runtime. The agent orchestrates multi-path extraction (kernel → OCR → Bedrock fallback), self-validates via SteeringHandler (won't return "done" until checksum passes), and adaptively retries with different models when confidence is low. Scope was reduced from the 9-agent topology in our broader design to fit the 2-week timeline — the remaining 8 agents are documented as v1.1+ roadmap.

---

## 3. Layer-by-layer summary

### Layer 1 — User / UI

| Item | Detail |
|---|---|
| Tech | React 18 + Vite + TypeScript; Tailwind; React Router v6; Zustand; Cognito via Amplify Auth; `react-pdf` viewer |
| Hosting | Private S3 bucket + CloudFront + OAC + ACM cert; deployed by Python CDK `UiStack` via `BucketDeployment` |
| Personas | **Admin** (8 pages) + **Business** (7 pages) — same build, two shells, route-guarded by Cognito group |
| Admin pages | Dashboard (6-pillars snapshot), Uploads, Jobs (retry/abort), JobDetail (CloudWatch deep-links), **Agents (enable/disable toggle)**, Profiles (read-only), Audit, Costs |
| Business pages | **Inbox** (pending_review), **RateSheetReview** (3-panel: PDF + extracted + provenance, Approve/Reject bar), ByUnion, Approved, Rejected, ReviewQueue (low-confidence cells; **Approve disabled until empty**), Me |
| Auth | Cognito user pool, 4 groups (`Admins`, `Operations`, `Business`, `ServiceClients`); MFA required; OAC; CSP via Lambda@Edge |
| Status | Built, all tests green |

### Layer 2 — API / Application

| Item | Detail |
|---|---|
| Tech | API Gateway HTTP API + Cognito JWT authorizer + AWS WAF |
| Compute | **19 Python Lambdas** (10 Admin + 9 Business/shared); ARM64; AWS Lambda Powertools (Logger/Tracer/Metrics); Pydantic models |
| Security | Per-route Cognito group gating in shared `authz` layer; every gated handler verifies `cognito:groups` |
| Approval API | `POST /v1/.../approve` (Business), `POST /v1/.../reject` (Business; reason required), `POST /v1/.../unapprove` (within 24h before publish), `POST /v1/.../publish` (Admin/Ops; **returns HTTP 409 unless `approval_state='approved'` in Aurora**) |
| Reliability | Per-Lambda DLQ + SNS failures topic + `onFailure`/`onSuccess` destinations |
| Status | Built; 71 unit tests passing (up from 30 after audit fix) |

### Layer 3 — Storage & Orchestration

| Item | Detail |
|---|---|
| S3 | 6 buckets: inputs, processed, outputs, profiles, audit, cba-corpus — KMS-encrypted, TLS-only, BlockPublicAccess, Object Lock in prod, lifecycle to Glacier Deep Archive |
| DynamoDB | **7 tables**: files, jobs, review-queue, overrides, cadence, idempotency, **agent-config** (drives Admin enable/disable toggle — Step Functions reads `enabled` before invoking ExtractorAgent) |
| Aurora Postgres | Serverless v2 cluster (0.5–2 ACU); `unions`, `rate_periods` (with full **approval_state lifecycle** + `approved_by`/`approved_at`/`rejected_by`/`rejected_at`/`rejection_reason`/`published_by`/`published_at`), `rate_cells`, `audit_log`; PITR; Secrets Manager rotation |
| Step Functions | Standard workflow; 6-stage pipeline; Choice states; per-task retry + DLQ; **reads `agent-config.enabled` and bypasses extract step when disabled** |
| EventBridge | Custom bus emitting `laboraid.rate-sheet.{approved,rejected,published,...}` for downstream consumers |
| Status | All built; encryption + retention policies in place |

### Layer 4 — Document Processing (Hybrid Path)

| Item | Detail |
|---|---|
| Approach | Kernel-as-library inside ExtractorAgent container; no separate Docling/Textract Fargate services in POC |
| Document classifier | Python Lambda — filename regex + Bedrock Haiku fallback for ambiguity; cross-validates filename + folder + content; routes unknowns to human review |
| PDF parsing | `pdfplumber` (text) + `pypdfium2` rendering + `rapidocr-onnxruntime` (scanned PDFs) — all in kernel, no API costs |
| Status | Kernel proven: 704 = 99.6%, 483 Building = 100%, 537 = 67.4% (sub-100% are confirmed-absent source values per **never-fabricate rule**) |

### Layer 5 — AI Extraction (Strands + AgentCore + Bedrock)

| Item | Detail |
|---|---|
| Agent count (POC) | **2** — `ExtractorAgent` (runtime extraction) + `ProfileDrafterAgent` (build-time auto-authoring of profile YAML + Python extractor for new unions). 7 of 9 original-design agents remain v1.1+ roadmap. |
| ExtractorAgent tools | **7** `@tool` functions: `stage_inputs_from_s3`, `run_kernel_extractor`, **`extract_via_claude_only`** (Path C, new), `compute_derived_columns`, `pivot_to_ratesheet_csv`, `escalate_to_claude_multimodal`, `validate_total_package_checksum` |
| ProfileDrafterAgent tools | **5** `@tool` functions: `analyze_groundtruth`, `draft_profile_yaml`, `draft_extractor_python`, `validate_generated`, `iterate_or_finalize` |
| Extraction paths | **3 paths**: A — deterministic kernel (Path A, 99.6%/100% Building on 704/483) · B — per-cell Bedrock fallback for kernel gaps · C — full-sheet Claude extractor for unions without a kernel extractor (NEW) |
| Steering | `ExtractorSteering(SteeringHandler)` — blocks `return_extraction_complete` until checksum validates; forces Bedrock fallback when kernel reports unresolved gaps |
| Models | Sonnet 4.6 (extraction) + Haiku 4.5 (classification); Bedrock PII Guardrail applied to all invocations |
| Deployment | ECR container (ARM64 Python 3.12) + AgentCore Runtime CustomResource; observability via OpenTelemetry → CloudWatch |
| Bedrock Knowledge Base | **Deferred** to v1.1+ (advanced RAG ambiguous in SOW Page 7 exclusion) |
| Status | Built; first end-to-end test happens at first deploy |

### Layer 6 — Validation & Human Review

| Item | Detail |
|---|---|
| Pre-publish validation | 4 Lambdas: checksum (Total Package), range (column bounds), confidence rollup, review-router |
| SNS topics | `failures`, `successes`, `review-needed` — subscribers: ops email, reviewer email, Slack-notifier Lambda, SQS audit queue |
| Review queue | DDB `review` table → Business `ReviewQueue` UI; bulk-accept/override actions |
| Human-in-the-loop gate | Business UI's **Approve** button is disabled until the rate sheet's review queue is empty; **Reject** requires a reason (free text + optional structured tag) |
| Year-over-year delta validation | **Deferred** to v1.1+ (needs 2+ historical periods) |
| Status | Built; SNS subscriptions wired |

### Layer 7 — Data Storage & Downstream Consumption

| Item | Detail |
|---|---|
| Renderers (3 Lambdas) | xlsx (`openpyxl` from kernel CSV), CSV (kernel pivot direct), articles (extracts CBA structural rules from kernel `gaps.md`) |
| Outputs | `s3://laboraid-{env}-l3-bucket-outputs/laboraid/{Trade}/{Local}/{period}/` + Aurora `rate_periods` row + `rate_cells` rows with provenance |
| Calculator integration | LaborAid's product reads via authenticated API (`/v1/unions/{local}/rate-sheets/{period}`); M2M Cognito client_credentials |
| Status | Built; xlsx output validated against customer's existing 537 spreadsheet |

---

## 4. Cross-cutting standards

| Area | Standard | Status |
|---|---|---|
| **AWS Well-Architected** | 6 pillars covered per layer (Op Excellence, Security, Reliability, Perf Eff, Cost Opt, Sustainability) | Verified per layer in Spec §9 |
| **Tagging** | 13 mandatory tags on every resource (Project, Customer, Environment, ManagedBy, Repository, CostCenter, Owner, Layer, SOW, AwsPartner, PublicUseCase, +conditional) via Python CDK Aspect | Aspect applied at app root |
| **Naming** | `laboraid-{env}-{layer}-{type}-{purpose}` everywhere | Helper-enforced; no hardcoded names |
| **Encryption** | KMS CMK on S3 + DDB + Aurora + Secrets; TLS-only bucket policies | Verified |
| **IAM** | Least-privilege per Lambda; per-route Cognito group gating; no static AWS credentials anywhere in repo | Verified by grep + manual review |
| **Compute** | ARM64 Graviton on every Lambda + Fargate task (cost + cold-start advantage) | All Lambdas + container |
| **Observability** | AWS Lambda Powertools (Logger/Tracer/Metrics) + X-Ray + 5 CloudWatch dashboards + 6 named alarms | Built in Observability stack |

---

## 5. Quality gates — independently verified

| Gate | Result |
|---|---|
| `cdk synth` (all 9 stacks) | ✅ Pass |
| `ruff check` + `black --check` (Python) | ✅ Pass |
| `mypy --strict` (CDK + Lambdas + agents) | ✅ Pass |
| `pytest` (CDK 18 + Lambdas 71) | ✅ Pass |
| `pnpm typecheck` + `lint` + `vitest` + `build` (UI) | ✅ Pass |
| Kernel regression accuracy | ✅ Held: 704 = 99.6% · 483 Building = 100% · 537 = 67.4% |
| Hard-rule compliance | ✅ kernel/ untouched · no static creds · MandatoryTagsAspect applied · language-split holds (TS only in `ui/`) · every gated Lambda checks `cognito:groups` · publish API queries Aurora · approve/reject/unapprove write to Aurora + fire EventBridge · Step Functions reads `agent-config.enabled` |

Audit + verification artifacts: [`docs/AUDIT_REPORT.md`](AUDIT_REPORT.md) (initial audit, 8 BLOCKER + 9 DRIFT + 7 NICE-TO-HAVE) and [`docs/AUDIT_VERIFICATION.md`](AUDIT_VERIFICATION.md) (re-audit: 8/8 BLOCKERS fixed, 8/9 DRIFT fixed, all gates green).

---

## 6. Explicitly deferred to v1.1+ (post-POC)

Per signed SOW + Spec §15. None of these are blockers for POC sign-off:

- 7 of 9 agents remain deferred (Orchestrator, agent-Classifier, CBAMiner, agent-Validator, Citation, Concierge, ReviewAssist) — **ProfileDrafterAgent moved from deferred → shipped** on `feat/path-c-and-drafter` (87 tests passing, self-audit 31/31 PASS — see [`Overnight_Delivery_Report.md`](Overnight_Delivery_Report.md))
- AgentCore Memory, Gateway, Identity, Policy (Cedar), Registry, Evaluations (kept Runtime + Observability only)
- Bedrock Knowledge Base + S3 Vectors (advanced RAG; SOW Page 7 exclusion ambiguity)
- Year-over-year delta validation + Article-20 awareness
- AI sanity review of validation outliers
- Profile editor UI (POC: profiles edited as YAML in repo)
- Multi-tenant separation
- Cross-region DR
- "Ask the CBA" Q&A (ConciergeAgent dependency)
- Cadence reminders / bulk backfill / scheduling
- **Group G — kernel extractors for unions 281 + 821** (runs in the kernel's own harness loop, separate from the AWS build)

---

## 7. Risks + readiness

**Before first deploy** (manual prerequisites in AWS console — one-time):
- AWS account chosen + `CDK_DEFAULT_ACCOUNT` set
- Bedrock model access enabled for Sonnet 4.6 + Haiku 4.5 + Titan Embed v2
- AgentCore service available in `us-east-1` (confirm regional availability)
- Cognito admin user invited + MFA enrolled
- Route53 hosted zone for `laboraid.app` — **optional**; UI falls back to CloudFront domain if absent

**Risks:**
1. **First deploy is the first real test** of two pieces: the AgentCore `CreateAgentRuntime` custom resource (B5) and the agent container entrypoint (B7). Unit tests can't fully validate these. Mitigation: deploy to a dev account first, smoke-test, then prod.
2. **Group G accuracy unknown** until the kernel harness completes 281 + 821 extractors. The other 3 unions (537/483/704) are proven; 281/821 are structurally similar and expected to land at 90%+.
3. **One documentation typo remains**: spec §14 still says "8 stacks" in two places (lines 1720, 1732); rest of the spec correctly reflects 9 stacks. Call out in PR description; 30-second fix.

**Cost (POC scale, monthly estimate):**
- AWS funding ($25K) covers approximately 6–9 months at POC traffic
- Major drivers: Bedrock InvokeModel (Sonnet > Haiku), Aurora Serverless v2 (idles to 0.5 ACU), Lambda + Step Functions (negligible at POC scale)
- Aurora Serverless and ARM64 Graviton are the two biggest cost-optimization levers and both are already in place

---

## 8. What's next

1. **PR + merge** to `main` (PR description ready at [`docs/PR_DESCRIPTION.md`](PR_DESCRIPTION.md))
2. **First deploy** to a dev AWS account — validates B5 + B7 end-to-end
3. **Group G** — kernel harness run for unions 281 + 821 (separate, in `kernel/.claude/harness`)
4. **UAT** with customer-provided scenarios (per SOW Assumption K)
5. **Production deploy** + customer sign-off
6. **AWS Public Use Case** writeup (per SOW post-success clause)

Repository: `github.com/surendersara1/laboraid-rate-engine` · Branch `feat/aws-strands-integration` (32 commits ahead of `main`)
