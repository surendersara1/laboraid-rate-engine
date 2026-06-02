# Technical Implementation Specification — Build-Ready

**Document:** 09 of `docs/` — this is the **executable spec** for an overnight CDK build
**Date:** 2026-06-02
**Status:** 2-week POC build spec; matches the signed SOW architecture diagram (7 layers) + minimum-viable Strands+AgentCore commitment
**Audience:** Engineers implementing the build

> This doc replaces the abstract design docs (01-08) with **concrete resource names, tags, CDK constructs, IAM policies, and end-to-end flows** that can be implemented directly. It is organized by architectural layer to match the SOW diagram (Page 11 of `LaborAid - POC SOW.docx.pdf`).

---

## ⚠️ Scope clarification (read this first)

**The signed SOW requires:**
- Strands Agent framework + AWS AgentCore (Tech Stack, Page 6)
- "AI Agentic solution" feasibility demonstration (Executive Summary, Page 2)
- "AI agents" + "agentic workflows" output validation (Assumption J, Page 9)

**The signed SOW does NOT require:**
- 9-agent topology (that's `docs/07`'s aspirational design)
- AgentCore Memory / Gateway / Identity / Policy / Evaluations / Registry as separate sub-services
- Bedrock Knowledge Base with S3 Vectors (ambiguous — "advanced RAG" is listed as out-of-scope; defer or confirm with customer)
- Per-cell provenance with 6 source types + drilldown UI
- 4-layer validation defense
- 9-agent steering policies

**This POC implementation: ONE Strands agent (`ExtractorAgent`) deployed on AgentCore Runtime, plus deterministic Lambdas for everything else.**

This still satisfies the contractual "agentic AI feasibility" commitment while fitting a 2-week build. The full 9-agent topology in `docs/07` remains the architectural roadmap for v1.1+ — see §15 below for what's deferred.

Where this doc previously listed 9 agents (Layer 5), it has been scoped down. Other layers are largely unchanged (storage, API, UI, validation are mostly Lambda-based work).

---

## 🧱 Kernel reuse — what's already built

Ashwani's working pipeline at `bitbucket.org:northbay/labor_aid_poc.git` (branch: `feat/cba-ratesheet-pipeline`) has been imported into `laboraid-rate-engine/kernel/` via `git subtree`. **The hardest part of the build — PDF reading, OCR, per-union extraction, derived-column compute, and a provenance-tagged canonical model — is already done and measured.**

See [`Ashwani_Repo_Assessment.md`](../Ashwani_Repo_Assessment.md) for the full assessment.

### Kernel → tech-spec layer mapping

| Tech-spec component | Kernel file/module | Status | What we still do |
|---|---|---|---|
| **Canonical schema** (docs/04 §2) | `kernel/canonical/model.py` (`RateCell`, `ClassificationRow`, `r2()`) | ✅ **Done** | Validate against our wider `docs/04` schema; minor extensions if needed |
| **Field dictionary** (label aliases) | `kernel/canonical/fields.yaml` | ✅ **Done** (50+ mappings across 5 unions) | Add 281- and 821-specific aliases as we build their extractors |
| **Per-union Profile YAMLs** (docs/04 §4) | `kernel/profiles/{537,483,704}.yaml` | ✅ **Done for 3 of 5** | Author profiles for `sprinkler_fitters_281` and `sprinkler_fitters_821` |
| **PDF ingestion** (docs/02 Stage 2, L4) | `kernel/pipeline/ingest.py` | ✅ **Done** (pdfplumber-based + text/image detection) | Wrap as a Lambda or invoke directly from the agent container |
| **OCR pipeline** (docs/02 Path B, L4) | `kernel/pipeline/ocr.py` (rapidocr-onnxruntime + pypdfium2) | ✅ **Done** (self-contained, no system deps, no API key) | Decide whether to keep rapidocr or also wire AWS Textract as a fallback |
| **Per-union extractors** (docs/02 Path A, L4) | `kernel/pipeline/extract.py` — `EXTRACTORS[union]` mapping | ✅ **Done for 537, 483, 704** | Add `extract_281` and `extract_821` |
| **Reference extractor for 483** | `kernel/extract/build_483.py` | ✅ **Done** (the proven kernel; reused by `pipeline/extract.py`) | Keep as regression guard |
| **Derived-column compute** (docs/04 §6 — multipliers, splits) | `kernel/pipeline/compute.py` (`resolve_row()`) | ✅ **Done** with half-up rounding via `r2()` | Validate against our DSL examples; extend if needed (e.g., 483 escalating Foreman premium needs date-keyed conditional) |
| **Pivot → ratesheet CSV** (docs/02 Stage 6, L7) | `kernel/pipeline/pivot.py` | ✅ **Done** (writes CSV matching groundtruth header) | Add an **xlsx renderer** for the 537 use case (kernel currently produces CSV only) |
| **Per-cell provenance** (docs/05) | `kernel/canonical/model.py` `RateCell.source_doc` + `source_locator` + `confidence` | ✅ **Done** (basic shape; 3 source types: `notice`, `cba`, `derived`) | Surface in admin UI side panel; extend to 6 source types if needed |
| **Groundtruth evaluator** (docs/02 Stage 5 — used post-hoc) | `kernel/pipeline/evaluate.py` | ✅ **Done** (cell accuracy ±0.01, header diff, per-zone breakdown) | Use as CI regression test; **separately build a pre-publish validator** (checksum + range; no groundtruth needed at runtime) |
| **Gaps reporting** (docs/05 — never-fabricate rule) | Kernel writes `data/<union>/ai_output/<union>.gaps.md` | ✅ **Done** | Surface in admin UI as a "Review needed" panel |
| **Build harness** (planner/builder/evaluator for iterative refinement) | `kernel/.claude/` | ✅ **Done** | Reuse for authoring 281 + 821 extractors |
| **CLI runner** | `kernel/pipeline/run.py` | ✅ **Done** | Wrap in Strands agent tool calls; keep CLI for local dev |

### What the kernel does NOT provide (we still build all of this)

| Layer | Component | Status |
|---|---|---|
| L1 — UI | React admin SPA + Cognito + CloudFront | 🟡 **All new** (per `docs/09 §4 L1`) |
| L2 — API | API Gateway + 10 Lambdas (upload, status, list, get, publish, override) | 🟡 **All new** (per `docs/09 §4 L2`) |
| L3 — Storage/orch | S3 buckets, DynamoDB tables, Aurora cluster, Step Functions, EventBridge | 🟡 **All new** (per `docs/09 §4 L3`) |
| L4 — Processing | Classifier Lambda + Lambda/Fargate hosts for the kernel | 🟡 **Wraps kernel** — kernel is the library, AWS is the host |
| L5 — AI | Strands `ExtractorAgent` on AgentCore Runtime | 🟡 **Wraps kernel** — agent's `@tool`s call `kernel/pipeline/extract.EXTRACTORS[union]` |
| L5 — AI fallback | Bedrock Claude multi-modal extraction (when kernel + OCR confidence drops) | 🟡 **New** (per `docs/09 §4 L5`) — kernel has no Bedrock dependency |
| L6 — Validation | Pre-publish checksum + range Lambdas (run without groundtruth) | 🟡 **New** — kernel's `evaluate.py` is post-hoc CI use only |
| L6 — Failure routing | SNS topics, EventBridge rules, review queue | 🟡 **All new** |
| L7 — Output | xlsx renderer, Aurora writes, API for LaborAid Calculator | 🟡 **xlsx new; CSV reuses kernel; Aurora new** |
| Missing extractors | `sprinkler_fitters_281` + `sprinkler_fitters_821` profiles + extractor code | 🟡 **New** — but follow the kernel's pattern (3-4 days using `.claude/harness`) |
| Tag-everything CDK | Per `docs/09 §1-2` naming + tagging strategy | 🟡 **All new** |

### Architectural agreement check (kernel vs our Design folder)

Ashwani independently arrived at the same conclusions our `docs/` proposed. Read this as **independent confirmation that our discovery was sound**:

| Our design says | Kernel does | Match |
|---|---|---|
| Per-union YAML Profile (docs/04 §4) | `kernel/profiles/*.yaml` with `multiplier_of`/`factor` | ✅ Same idea (simpler grammar; sufficient for POC) |
| Canonical intermediate model (docs/04 §5) | `RateCell` tidy/long + `ClassificationRow` wide | ✅ Same idea |
| Per-cell provenance (docs/05) | `RateCell.source_doc` + `source_locator` + `confidence` | ✅ Simpler taxonomy (3 sources vs our 6) — same intent |
| Half-up rounding (docs/04 §6) | `kernel/canonical/model.py:r2()` using `Decimal.ROUND_HALF_UP` | ✅ Identical |
| Never fabricate; blank + flag gaps (docs/08) | `gaps.md` per union | ✅ Same philosophy |
| Read-only on inputs, write-only to output dir (docs/05) | Enforced as a hard rule in kernel | ✅ Same |
| Multi-path extraction (docs/02 §2) | pdfplumber → rapidocr-onnxruntime fallback | ✅ 2 paths (we add Bedrock Claude as Path C) |
| Hand-authored extractors per union (docs/06) | Implemented for 537, 483, 704 | ✅ Confirmed approach works |

Where the kernel is simpler than our design: it doesn't have the formula DSL grammar, the 6-source provenance taxonomy, the 4-layer validation, or the Bedrock fallback. **All of those are POC-scope items per §15 — we extend the kernel where needed, leave it alone where it's already sufficient.**

### Future kernel updates (workflow)

The kernel was imported via `git subtree`. If Ashwani pushes new commits to `labor_aid_poc`'s `feat/cba-ratesheet-pipeline` branch (e.g., fixes the 537 reallocation or adds 281/821), pull them with:

```bash
cd laboraid-rate-engine
git checkout main
git remote add kernel-source git@bitbucket.org:northbay/labor_aid_poc.git  # one-time if missing
git fetch kernel-source feat/cba-ratesheet-pipeline
git subtree pull --prefix=kernel kernel-source feat/cba-ratesheet-pipeline --squash \
  -m "chore: pull kernel updates from upstream"
```

Conversely, if WE make improvements to `kernel/` that should flow back upstream, use `git subtree push`. (Practical recommendation: coordinate with Ashwani before either direction — submarine merges in subtrees are confusing.)

---

## 0. Build envelope

| Item | Value |
|---|---|
| Region (primary) | `us-east-1` (Bedrock model availability, AgentCore service availability) |
| Region (DR/standby) | Not in POC scope (single region) |
| Account model | Single AWS account for POC; separate dev/prod stages within (no multi-account) |
| Environments | `dev`, `prod` (no staging for POC; production = the UAT environment) |
| IaC | **AWS CDK v2 — Python** (`aws-cdk-lib` Python; NOT TypeScript) — full stack |
| Languages | **Python 3.12** for everything backend — CDK, Lambdas, the Strands agent, scripts, tests. **TypeScript only inside `ui/`** (React admin SPA). No other TS/Node anywhere in the repo. |
| Source control | GitHub (per SOW); single monorepo `laboraid-rate-engine` |
| Compute architecture | ARM64 across the board (Graviton — cheaper + faster cold starts on Lambda) |
| Default Lambda runtime | `python3.12` for all Lambdas; ExtractorAgent container also Python 3.12 ARM64 |
| Agent count (POC) | **1** (`ExtractorAgent`) — others deferred to v1.1+ (see §15) |

---

## 1. Naming convention

All AWS resources follow this pattern:

```
laboraid-{env}-{layer}-{resource_type}-{purpose}
```

Examples:
- `laboraid-prod-l3-bucket-inputs`
- `laboraid-prod-l4-fn-classifier`
- `laboraid-prod-l5-agent-extractor`
- `laboraid-dev-l6-sns-failures`

`{layer}` corresponds to the SOW diagram's 7 layers:
- `l1` — User / UI Layer
- `l2` — API / Application Layer
- `l3` — Storage & Orchestration Layer
- `l4` — Document Processing Layer (Hybrid Path)
- `l5` — AI Extraction Layer (Strands + AgentCore + Bedrock)
- `l6` — Validation & Human Review Layer
- `l7` — Data Storage & Downstream Consumption Layer

S3 keys (within input/output buckets) follow:
```
{tenant}/{trade}/{local}/{period_start_yyyy-mm-dd}/{filename}
e.g., laboraid/Sprinkler/704/2026-01-01/2026.01.01.704 Rate Notice.pdf
```

---

## 2. Tagging strategy

**Mandatory tags on every resource** (enforced via CDK Aspects):

```python
# cdk/laboraid_cdk/config/__init__.py
MANDATORY_TAGS: dict[str, str] = {
    "Project":       "LaborAid-POC",
    "Customer":      "LaborAid",
    "Environment":   env,                    # "dev" | "prod"
    "ManagedBy":     "CDK",
    "Repository":    "github.com/NorthBay/laboraid-rate-engine",
    "CostCenter":    "NBS-POC-2026",
    "Owner":         "NBS-Engineering",
    "Layer":         layer,                  # "l1".."l7"
    "SOW":           "LaborAid-POC-SOW-v1",
    "AwsPartner":    "NorthBay-Premier",
    "PublicUseCase": "true",                 # per SOW Public Use Case clause
}
```

**Conditional tags** by resource:
- `AgentName: ExtractorAgent` (on AgentCore Runtime resources)
- `DataClassification: customer-input | engine-output | audit-log` (on S3 + Aurora)
- `RetentionDays: 30 | 365 | 2555` (on S3 with lifecycle)
- `PII: false` (POC has no PII; explicit tag for compliance scanning)

CDK enforcement via Aspect:

```python
# cdk/laboraid_cdk/aspects/mandatory_tags.py
import jsii
from aws_cdk import IAspect, Tags
from constructs import IConstruct
from aws_cdk import Resource

from laboraid_cdk.config import MANDATORY_TAGS


@jsii.implements(IAspect)
class MandatoryTagsAspect:
    def visit(self, node: IConstruct) -> None:
        if isinstance(node, Resource):
            tags = Tags.of(node)
            for k, v in MANDATORY_TAGS.items():
                tags.add(k, v)


# Applied in app.py:
#   Aspects.of(app).add(MandatoryTagsAspect())
```

---

## 3. CDK stack organization

Single CDK app, **8 stacks** with cross-stack references:

```
LaboraidApp/
├── LaboraidNetworkStack         (VPC, subnets — minimal; Lambdas mostly outside VPC)
├── LaboraidSecurityStack        (KMS keys, Cognito, IAM roles)
├── LaboraidStorageStack         (S3 buckets, DynamoDB tables, Aurora cluster)
├── LaboraidProcessingStack      (L4 — Document classifier, Docling Fargate, Textract Lambdas)
├── LaboraidAIStack              (L5 — Bedrock KB, Strands agents on AgentCore Runtime)
├── LaboraidValidationStack      (L6 — Validator Lambdas, review queue, SNS topics)
├── LaboraidApiStack             (L1+L2 — API Gateway, Lambdas, Cognito integration)
├── LaboraidUiStack              (L1 — S3 static site + CloudFront for admin SPA)
└── LaboraidObservabilityStack   (CloudWatch dashboards, X-Ray, alarms, SNS subscriptions)
```

Stack deployment order (CDK figures it out via dependencies, but logically):

```
Network → Security → Storage → Processing → AI → Validation → API → UI → Observability
```

Each stack is independently deployable so failures in one don't block the others.

---

## 4. Layer-by-layer implementation

### LAYER 1 — User / UI Layer

**Purpose:** Two distinct human user experiences sharing one React SPA:
1. **Admin / Operations** — NBS + LaborAid ops keeping the engine healthy
2. **Business** — LaborAid business users + Union SMEs reviewing the engine's output and signing off before publish

The SPA renders **two completely different shells** (sidebar, top-bar, landing page, page set) routed under `/admin/*` and `/business/*`. The active shell is decided at login by the Cognito group claim. Shared components (auth, PDF viewer, rate-cell table) are reused; layouts and feature sets are persona-specific.

#### 1.1 Personas + UI separation (two-persona model)

| Persona | UI shell | Routes | Cognito group | Primary job |
|---|---|---|---|---|
| **Admin** (NBS + LaborAid ops) | Ops shell — sidebar: Dashboard / Jobs / Agents / Profiles / Uploads / Audit / Costs | `/admin/*` | `Admins` (full) + `Operations` (subset — no agent disable) | Keep the engine healthy, retry failed jobs, toggle agents, monitor 6-pillar metrics |
| **Business** (LaborAid business + Union SMEs) | Review shell — sidebar: Inbox / By Union / Approved / Rejected / Review Queue | `/business/*` | `Business` | Review each finalized rate sheet, override low-confidence cells, **approve or reject**, sign off before publish |
| **Service-to-service** (LaborAid product reads rates) | API only — no UI | `/api/v1/rate-sheets/*` (read-only) | Cognito M2M `client_credentials` | Pull approved + published rate sheets into LaborAid's Calculator |

**Routing rules:**
- A user in `Admins` lands on `/admin/dashboard`; a user in `Business` lands on `/business/inbox`. A user in both (rare) gets a chooser at `/`.
- `/admin/*` returns 403 for `Business` users; `/business/*` returns 403 for `Admins`-only users (Admins can be added to `Business` if they need both).
- Route guards live in `ui/src/components/RouteGuard.tsx`; every route declares allowed groups.

**Approval workflow (the business gate):**
1. Engine finishes → rate sheet enters Aurora `rate_periods` with `approval_state='pending_review'`.
2. Business inbox shows it. SME opens, reviews, overrides cells if needed, then clicks **Approve** or **Reject** with reason.
3. Approve → `approval_state='approved'`, `approved_by=<sub>`, `approved_at=NOW()`. Eligible for publish.
4. Reject → `approval_state='rejected'`, reason written, EventBridge fires `laboraid.rate-sheet.rejected`, engine can re-run.
5. Admin (or Operations) then triggers **Publish** — API enforces `approval_state='approved'` before allowing publish. Publish is a separate step from approval so Admins keep release-cadence control.

See §1.4 (Admin features) and §1.5 (Business features) for the full feature lists.

#### 1.2 Assets

| Resource | Name | Tags |
|---|---|---|
| S3 bucket (static hosting) | `laboraid-{env}-l1-bucket-spa` | Layer=l1, DataClassification=public-assets |
| CloudFront distribution | `laboraid-{env}-l1-cf-spa` | Layer=l1 |
| Origin Access Identity | `laboraid-{env}-l1-oai-spa` | Layer=l1 |
| Route53 record | `admin-{env}.laboraid.app` (or NBS subdomain initially) | — |
| ACM certificate | `laboraid-{env}-l1-cert-admin` | Layer=l1 |
| Cognito user pool | `laboraid-{env}-l1-cognito-userpool` | Layer=l1 |
| Cognito identity pool | `laboraid-{env}-l1-cognito-idpool` | Layer=l1 |
| Cognito groups | `Admins`, `Operations`, `Business`, `ServiceClients` | — |
| Agent-config DDB table | `laboraid-{env}-l3-ddb-agent-config` | Layer=l3, DataClassification=ops-config (read here, defined in §3.2) — backs the Admin "Agents" page (enable/disable, version, runtime metadata) |

#### 1.3 CDK skeleton

```python
# cdk/laboraid_cdk/stacks/ui_stack.py  (L1 UI hosting stack — Python CDK; SPA itself is React/TS under ui/)
from aws_cdk import Stack, RemovalPolicy, Duration
from aws_cdk import (
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_certificatemanager as acm,
    aws_cognito as cognito,
)
from constructs import Construct


class UiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env: str, cert: acm.ICertificate, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        spa_bucket = s3.Bucket(
            self, "SpaBucket",
            bucket_name=f"laboraid-{env}-l1-bucket-spa",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN if env == "prod" else RemovalPolicy.DESTROY,
        )

        # Modern OAC (replaces legacy OAI)
        oac = cf.S3OriginAccessControl(self, "SpaOac")

        distribution = cf.Distribution(
            self, "SpaDistribution",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(spa_bucket, origin_access_control=oac),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            error_responses=[
                cf.ErrorResponse(http_status=404, response_http_status=200, response_page_path="/index.html"),
            ],
            certificate=cert,
            domain_names=[f"admin-{env}.laboraid.app"],
        )

        # Deploy the React build output (produced by `cd ui && pnpm build`)
        s3_deploy.BucketDeployment(
            self, "SpaDeployment",
            sources=[s3_deploy.Source.asset("../ui/dist")],
            destination_bucket=spa_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name=f"laboraid-{env}-l1-cognito-userpool",
            self_sign_up_enabled=False,           # admin-invited only for POC
            sign_in_aliases=cognito.SignInAliases(email=True),
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(min_length=12, require_symbols=True),
        )

        for group_name in ("Admins", "Operations", "Business", "ServiceClients"):
            cognito.CfnUserPoolGroup(
                self, f"Group{group_name}",
                user_pool_id=user_pool.user_pool_id,
                group_name=group_name,
            )
```

#### 1.4 Admin UI features (`/admin/*`)

The Admin shell exists so NBS + LaborAid ops can run the engine. Every feature here is operational — none of it touches business approval semantics.

| Page | Route | Purpose | Cognito group |
|---|---|---|---|
| **Dashboard** | `/admin/dashboard` | Landing page. 6-pillars-aware snapshot: jobs in-flight, jobs failed (24h), P95 latency, Bedrock spend (7d), error budget burn, SNS alarm state. Quick-action tiles to Jobs / Agents / Review Queue. | Admins, Operations |
| **Uploads** | `/admin/uploads` | Drag-drop PDF upload via presigned URL. Classification preview before commit. Re-upload corrupt files. | Admins, Operations |
| **Jobs** | `/admin/jobs` | All Step Functions executions. Filter by status / union / period / date. Bulk retry; bulk abort. | Admins, Operations |
| **Job detail** | `/admin/jobs/:id` | Per-execution timeline, per-stage logs (CloudWatch deep-link), per-cell extraction trace, retry / abort / re-run controls. | Admins, Operations |
| **Agents** | `/admin/agents` | Strands agent registry. For each agent: name, version, container image tag, runtime status (Available / Starting / Failed), recent invocations, recent latency. **Enable / disable toggle** writes to `agent-config` DDB (an `enabled=false` agent makes the Step Function bypass it via a Choice state). | Admins **only** |
| **Profiles** | `/admin/profiles` | Read-only list of per-union Profile YAMLs + version history (last 10 commits). Diff view between versions. Profile edit UI is deferred to v1.1+ (see §15.6). | Admins, Operations |
| **Audit log** | `/admin/audit` | Searchable / filterable view over `audit_log` Aurora table. Filter by actor / action / time range / tenant. CSV export. | Admins, Operations |
| **Costs** | `/admin/costs` | Bedrock InvokeModel spend (7d / 30d), S3 storage by bucket, Lambda invocations + duration, Aurora ACU usage. Pulled from Cost Explorer + CloudWatch billing metrics. POC: read-only. | Admins **only** |

**What the Admin UI does NOT have** (intentional, gates the persona separation):
- No Approve / Reject buttons on rate sheets (that's the business persona's job)
- No rate-sheet-by-union browsing (admins look at jobs and agents, not the data product)
- No commenting on cells (business does that)

**Cross-cutting Admin behaviors:**
- Real-time updates: `/admin/jobs` and `/admin/agents` poll every 5s while any job is `in_progress`
- Alerting: a CloudWatch alarm in firing state shows as a red banner in the top-bar, link to `/admin/dashboard`
- Deep-links: every "View in CloudWatch" / "View in X-Ray" link opens the right resource (saves ops time)

#### 1.5 Business UI features (`/business/*`)

The Business shell exists so LaborAid business users + Union SMEs can review what the engine produced and sign off (or send it back) before LaborAid's Calculator consumes it. **The business persona is the human-in-the-loop gate that decides what is "final."**

| Page | Route | Purpose | Cognito group |
|---|---|---|---|
| **Inbox** | `/business/inbox` | Landing page. Rate sheets with `approval_state='pending_review'`, oldest-first. Each row: union, period, confidence summary, gap count, "Open" button. | Business |
| **Rate sheet review** | `/business/rate-sheets/:union/:period` | The heart of the persona. Three-panel layout: (1) source PDF preview (`react-pdf`), (2) extracted rate sheet as editable table (TanStack Table), (3) per-cell provenance + confidence + history side panel. Click a cell to open override modal; right-click to comment. Top bar: **Approve** / **Reject** buttons + rejection-reason field. | Business |
| **By Union** | `/business/by-union/:union` | All rate sheets for one union, latest first. Status badges (pending / approved / rejected / published). Filter by year. | Business |
| **Approved** | `/business/approved` | History of approved rate sheets across all unions. Columns: union, period, approved by, approved at, published? (yes/no), link to view. | Business |
| **Rejected** | `/business/rejected` | History of rejected rate sheets. Columns: union, period, rejected by, rejected at, reason, current state (re-running / abandoned / re-approved). | Business |
| **Review queue** | `/business/queue` | Cells flagged as low-confidence by the engine (from `review` DDB table). Group by rate sheet. Bulk-accept / bulk-override actions. Once empty for a rate sheet, the Approve button becomes available on its review page. | Business |
| **My approvals** | `/business/me` | The current user's recent activity (approved, rejected, overridden cells, comments). Provides "what did I sign off on?" audit. | Business |

**Actions available to Business users (and ONLY them):**
- **Approve a rate sheet** → `POST /v1/unions/{local}/rate-sheets/{period}/approve` — sets `approval_state='approved'`, stamps `approved_by` + `approved_at`, fires `laboraid.rate-sheet.approved`. Required: review queue empty for this rate sheet (no unresolved low-confidence cells).
- **Reject a rate sheet** → `POST /v1/unions/{local}/rate-sheets/{period}/reject` — needs `reason` (free text + optional structured tags: `missing_data`, `wrong_extraction`, `cba_mismatch`, `other`). Sets `approval_state='rejected'`, fires `laboraid.rate-sheet.rejected`. Triggers engine to either re-run or hold for fix.
- **Override a cell** → `POST /v1/cells/{cell_id}/override` — writes to `overrides` DDB; cell's "current value" becomes the override but engine value is preserved in provenance.
- **Comment on a cell or rate sheet** → `POST /v1/cells/{cell_id}/comment` (or `/v1/rate-sheets/{period}/comment`) — written to `audit_log` with `action='comment'`. Visible to other reviewers.
- **Unapprove** (within 24h, before publish) → `POST /v1/unions/{local}/rate-sheets/{period}/unapprove` — only available to the original approver; bumps state back to `pending_review`.

**What the Business UI does NOT have:**
- No system-health view (that's Admin's job; if engine is down, business sees "no new rate sheets" — Admin sees the alarm)
- No agent controls
- No raw-PDF re-upload (Admin uploads; Business reviews the result)
- No Publish button (Admin/Operations triggers publish AFTER Business has approved — keeps release cadence with ops)

**Cross-cutting Business behaviors:**
- "Approve" is disabled until the review queue for that rate sheet is empty (forces every low-confidence cell to be looked at)
- "Reject" requires a reason — no silent rejections
- All actions write to `audit_log` so the trail is complete: who approved, who rejected, when, why
- Compare-to-prior-period (YoY diff) is **deferred to v1.1+** (§15.4) — POC just shows the period being reviewed
- Comparison views, bulk approve across periods, and Profile-editor for unions are all v1.1+ stretch

#### 1.6 6-pillar coverage (L1)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | CloudFront access logs to S3; CloudWatch RUM for real-user monitoring |
| **Security** | MFA required; OAC over OAI for new accounts; HTTPS only; CSP headers via Lambda@Edge |
| **Reliability** | CloudFront global edge; S3 versioned bucket |
| **Performance Efficiency** | CloudFront caching; Brotli compression; HTTP/2 |
| **Cost Optimization** | CloudFront Price Class 100 (US/Europe only); S3 Intelligent-Tiering |
| **Sustainability** | Graviton-based Lambda@Edge if needed; minimal compute footprint (static SPA) |

---

### LAYER 2 — API / Application Layer

**Purpose:** Public HTTPS API for upload, query, override, publish. Service-to-service API for LaborAid product. Backend orchestration glue.

#### 2.1 Assets

| Resource | Name | Tags |
|---|---|---|
| API Gateway (HTTP API) | `laboraid-{env}-l2-apigw-main` | Layer=l2 |
| Custom domain | `api-{env}.laboraid.app` | — |
| Cognito authorizer | `laboraid-{env}-l2-authz-cognito` | Layer=l2 |
| Lambda — upload presign | `laboraid-{env}-l2-fn-upload-presign` | Layer=l2 |
| Lambda — job status | `laboraid-{env}-l2-fn-job-status` | Layer=l2 |
| Lambda — list rate sheets | `laboraid-{env}-l2-fn-ratesheet-list` | Layer=l2 |
| Lambda — get rate sheet | `laboraid-{env}-l2-fn-ratesheet-get` | Layer=l2 |
| Lambda — publish | `laboraid-{env}-l2-fn-ratesheet-publish` | Layer=l2 |
| Lambda — override cell | `laboraid-{env}-l2-fn-cell-override` | Layer=l2 |
| Lambda — ask CBA (proxy to Concierge agent) | `laboraid-{env}-l2-fn-ask-cba` | Layer=l2 |
| Lambda — list profiles | `laboraid-{env}-l2-fn-profile-list` | Layer=l2 |
| Lambda — update profile | `laboraid-{env}-l2-fn-profile-update` | Layer=l2 |
| Lambda execution role | `laboraid-{env}-l2-role-api-lambdas` | — |
| WAF Web ACL | `laboraid-{env}-l2-waf-api` (rate-limit + AWS managed rules) | — |

#### 2.2 API routes

| Method + Path | Authorizer | Handler | Notes |
|---|---|---|---|
| `POST /v1/uploads` | Cognito (Admins, Operations) | `upload-presign` | Returns S3 presigned PUT URL. Admin-only — Business cannot upload. |
| `GET /v1/jobs` | Cognito (Admins, Operations) | `job-list` | List jobs with filters (status, union, period). |
| `GET /v1/jobs/{id}` | Cognito (Admins, Operations) | `job-status` | Returns Step Function execution state. |
| `POST /v1/jobs/{id}/retry` | Cognito (Admins, Operations) | `job-retry` | Re-runs a failed execution from the last successful state. |
| `POST /v1/jobs/{id}/abort` | Cognito (Admins) | `job-abort` | Cancels an in-flight execution. |
| `GET /v1/agents` | Cognito (Admins, Operations) | `agent-list` | Reads `agent-config` DDB + AgentCore Runtime status. |
| `PATCH /v1/agents/{name}` | Cognito (Admins **only**) | `agent-toggle` | Toggle `enabled` / change version. Writes to `agent-config` DDB. |
| `GET /v1/unions` | Cognito | `profile-list` | List all configured unions |
| `GET /v1/unions/{local}/profile` | Cognito | `profile-list` | Read Profile YAML |
| `PUT /v1/unions/{local}/profile` | Cognito (Admins only) | `profile-update` | Versioned write — v1.1+ from UI; POC: edit YAML in repo |
| `GET /v1/unions/{local}/rate-sheets` | Cognito (or M2M) | `ratesheet-list` | List periods (filter by `approval_state`). |
| `GET /v1/unions/{local}/rate-sheets/{period}` | Cognito (or M2M) | `ratesheet-get` | Returns canonical JSON + current approval state. |
| **`POST /v1/unions/{local}/rate-sheets/{period}/approve`** | Cognito (**Business**) | `ratesheet-approve` | **Business sign-off.** Requires review queue empty for this rate sheet. Sets `approval_state='approved'`, stamps `approved_by`+`approved_at`, fires `laboraid.rate-sheet.approved`. |
| **`POST /v1/unions/{local}/rate-sheets/{period}/reject`** | Cognito (**Business**) | `ratesheet-reject` | **Business rejection.** Requires `reason` (free text + optional tag). Sets `approval_state='rejected'`, fires `laboraid.rate-sheet.rejected`. |
| **`POST /v1/unions/{local}/rate-sheets/{period}/unapprove`** | Cognito (Business; original approver only) | `ratesheet-unapprove` | Within 24h + before publish; flips state back to `pending_review`. |
| `POST /v1/unions/{local}/rate-sheets/{period}/publish` | Cognito (Admins, Operations) | `ratesheet-publish` | **Gated:** rejects 409 unless `approval_state='approved'`. Sets `approval_state='published'`. |
| `POST /v1/cells/{cell_id}/override` | Cognito (Business) | `cell-override` | Manual value override; writes to `overrides` DDB, preserves engine value in provenance. |
| `POST /v1/cells/{cell_id}/comment` | Cognito (Business) | `cell-comment` | Per-row note; written to `audit_log` with `action='comment'`. |
| `GET /v1/unions/{local}/rate-sheets/{period}/audit` | Cognito (Admins, Operations, Business) | `ratesheet-audit` | Full audit trail (approvals, rejections, overrides, comments) for the rate sheet. |
| `GET /v1/audit` | Cognito (Admins, Operations) | `audit-list` | Filterable audit_log query (Admin/Operations only). |
| ~~`POST /v1/cba/{local}/ask`~~ | Cognito | `ask-cba` | Proxies to ConciergeAgent — ⏸️ **v1.1+** (ConciergeAgent deferred) |

#### 2.3 Lambda config (defaults)

```python
# cdk/laboraid_cdk/constructs/tagged_lambda.py — shared Lambda defaults
from aws_cdk import Duration
from aws_cdk import aws_lambda as lambda_, aws_logs as logs

def lambda_defaults(env: str) -> dict:
    return dict(
        runtime=lambda_.Runtime.PYTHON_3_12,
        architecture=lambda_.Architecture.ARM_64,
        memory_size=512,
        timeout=Duration.seconds(30),
        tracing=lambda_.Tracing.ACTIVE,
        log_retention=logs.RetentionDays.ONE_MONTH,
        environment={
            "LOG_LEVEL": "INFO" if env == "prod" else "DEBUG",
            "POWERTOOLS_SERVICE_NAME": "laboraid-api",
            "ENV": env,
        },
        # Bundling via aws_lambda_python_alpha.PythonFunction (preferred) or Docker
    )
```

#### 2.4 Retry / failure handling

- API Gateway: throttle limits 100 RPS burst, 50 sustained per tenant (rate-limited via WAF)
- Lambda: built-in retry on async invocations (Lambda retries up to 2 times for async); DLQ on each function:
  - `laboraid-{env}-l2-sqs-dlq-{fn_name}`
- Failed invocations published to SNS topic:
  - `laboraid-{env}-l6-sns-failures` (shared topic, all layers publish)

```python
from aws_cdk import Duration
from aws_cdk import aws_sqs as sqs, aws_lambda as lambda_
from aws_cdk import aws_lambda_destinations as destinations

dlq = sqs.Queue(
    self, f"Dlq-{fn_name}",
    queue_name=f"laboraid-{env}-l2-sqs-dlq-{fn_name}",
    encryption=sqs.QueueEncryption.KMS_MANAGED,
    retention_period=Duration.days(14),
)

fn = lambda_.Function(
    self, fn_name,
    function_name=f"laboraid-{env}-l2-fn-{fn_name}",
    dead_letter_queue=dlq,
    on_failure=destinations.SnsDestination(failures_topic),
    on_success=destinations.SnsDestination(success_topic),
    **lambda_defaults(env),
)
```

#### 2.5 6-pillar coverage (L2)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | Structured JSON logs (Powertools); X-Ray traces; per-route latency metrics |
| **Security** | Cognito JWT authorizer; WAF rules; IAM least-privilege per Lambda; KMS-encrypted env vars |
| **Reliability** | DLQs + retry; multi-AZ Lambda; API Gateway is regional + multi-AZ |
| **Performance Efficiency** | Reserved concurrency on high-frequency Lambdas; ARM64 Graviton |
| **Cost Optimization** | HTTP API (cheaper than REST API); Lambda right-sized memory; no provisioned concurrency in POC |
| **Sustainability** | Graviton ARM64; minimal cold start; serverless scale-to-zero |

---

### LAYER 3 — Storage & Orchestration Layer

**Purpose:** Durable storage for inputs, intermediate artifacts, outputs, and audit. Orchestration of the pipeline via Step Functions.

#### 3.1 S3 buckets

| Bucket | Name | Purpose | Retention |
|---|---|---|---|
| Inputs | `laboraid-{env}-l3-bucket-inputs` | Raw uploaded CBAs + Rate Notices | 7 years (Object Lock + Glacier Deep Archive after 1 year) |
| Processed | `laboraid-{env}-l3-bucket-processed` | Intermediate artifacts (`ExtractedDocument`, `RuleManifest`) | 90 days |
| Outputs | `laboraid-{env}-l3-bucket-outputs` | Canonical JSON, xlsx, CSV, Articles file | 7 years (Object Lock) |
| Profiles | `laboraid-{env}-l3-bucket-profiles` | Per-union Profile YAMLs (versioned) | Forever |
| Audit logs | `laboraid-{env}-l3-bucket-audit` | CloudTrail, S3 access logs, audit events | 7 years |
| CBA corpus | `laboraid-{env}-l3-bucket-cba-corpus` | KB-managed copies of CBAs + chunks (provisioned but unused in POC v1; ready for v1.1+ KB ingestion) | Forever |

**All buckets:**
- SSE-KMS encryption (CMK: `laboraid-{env}-kms-master`)
- Versioning enabled
- Block all public access
- Lifecycle: Intelligent-Tiering → Glacier per retention rules
- Server access logs → audit bucket
- TLS-only bucket policy (deny non-TLS PUT/GET)

#### 3.2 DynamoDB tables

| Table | Name | Partition Key | Sort Key | Purpose |
|---|---|---|---|---|
| Files | `laboraid-{env}-l3-ddb-files` | `tenant#union` | `period#filename` | File metadata, classification, status |
| Jobs | `laboraid-{env}-l3-ddb-jobs` | `job_id` | — | Step Function execution state, retry counters |
| Review Queue | `laboraid-{env}-l3-ddb-review` | `tenant` | `created_at#cell_id` | Cells awaiting human review |
| Overrides | `laboraid-{env}-l3-ddb-overrides` | `tenant#union#period` | `cell_id#timestamp` | Manual override history |
| Cadence | `laboraid-{env}-l3-ddb-cadence` | `tenant#union` | — | Expected next-Notice date per union |
| Idempotency | `laboraid-{env}-l3-ddb-idempotency` | `request_hash` | — | TTL 24h; prevents duplicate processing |
| **Agent config** | `laboraid-{env}-l3-ddb-agent-config` | `agent_name` | — | Backs Admin "Agents" page (§1.4). Attrs: `enabled` (bool), `image_tag`, `version`, `updated_by`, `updated_at`. Step Functions reads `enabled` before invoking an agent and bypasses via Choice state when false. |

**All tables:**
- On-demand billing (POC scale)
- Point-in-time recovery enabled
- SSE-KMS with CMK
- DynamoDB Streams enabled on Files + Jobs (drives EventBridge events)

#### 3.3 Aurora Postgres

| Resource | Name | Purpose |
|---|---|---|
| Aurora Serverless v2 cluster | `laboraid-{env}-l3-aurora-cluster` | Profiles, rate_periods, rate_cells, audit_log, provenance index |
| Aurora writer instance | `laboraid-{env}-l3-aurora-writer` | min 0.5 ACU, max 2 ACU (POC) |
| Aurora reader (single) | `laboraid-{env}-l3-aurora-reader` | for `GET /rate-sheets/*` queries |
| Secrets Manager secret | `laboraid-{env}-l3-secret-aurora` | DB master credentials, rotated monthly |

Schema (per `docs/10`):

```sql
CREATE TABLE unions (
  id UUID PRIMARY KEY,
  local INT NOT NULL,
  trade TEXT NOT NULL,
  parent_intl TEXT,
  profile_yaml JSONB,
  profile_version TEXT
);

CREATE TABLE rate_periods (
  id UUID PRIMARY KEY,
  union_id UUID REFERENCES unions(id),
  start_date DATE,
  end_date DATE,
  status TEXT,                                  -- engine pipeline status: ingested/extracted/validated/rendered
  approval_state TEXT NOT NULL DEFAULT 'pending_review',
                                                -- business-facing state: pending_review | approved | rejected | published
  approved_by TEXT,                             -- Cognito sub of business user who approved
  approved_at TIMESTAMPTZ,
  rejected_by TEXT,                             -- Cognito sub of business user who rejected
  rejected_at TIMESTAMPTZ,
  rejection_reason TEXT,                        -- required when approval_state='rejected'
  rejection_tags TEXT[],                        -- optional structured tags: missing_data | wrong_extraction | cba_mismatch | other
  published_by TEXT,                            -- Cognito sub of admin/ops who published
  published_at TIMESTAMPTZ,
  canonical_json JSONB,
  source_files JSONB
);
-- Publish guard: enforced at API layer (returns 409 if approval_state != 'approved').
-- Also enforced by DB CHECK as a defense-in-depth:
ALTER TABLE rate_periods
  ADD CONSTRAINT publish_requires_approval
  CHECK (approval_state IN ('pending_review','approved','rejected','published'));
CREATE INDEX idx_periods_inbox ON rate_periods (approval_state, start_date DESC);

CREATE TABLE rate_cells (
  id UUID PRIMARY KEY,
  period_id UUID REFERENCES rate_periods(id),
  zone TEXT,
  package TEXT,
  dimensions JSONB,
  column_name TEXT,
  value NUMERIC,
  value_type TEXT,
  provenance JSONB,
  confidence NUMERIC
);
CREATE INDEX idx_cells_lookup ON rate_cells(period_id, zone, package, column_name);
CREATE INDEX idx_cells_prov_gin ON rate_cells USING GIN (provenance jsonb_path_ops);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ DEFAULT NOW(),
  tenant TEXT,
  actor TEXT,
  action TEXT,
  details JSONB
);
```

#### 3.4 Step Functions

| State machine | Name | Purpose |
|---|---|---|
| Main pipeline | `laboraid-{env}-l3-sfn-main` | Triggered by S3 ObjectCreated; runs Stages 1→6 |
| Backfill | `laboraid-{env}-l3-sfn-backfill` | Bulk historical Notice processing |
| Onboarding | `laboraid-{env}-l3-sfn-onboarding` | New-union Profile drafting workflow |

Type: **Standard** workflow (not Express) — runs minutes-to-tens-of-minutes, high audit value.

#### 3.5 EventBridge bus

| Bus | Name |
|---|---|
| Custom bus | `laboraid-{env}-l3-eb-engine` |

Events emitted:
- `laboraid.file.ingested` (after S3 upload)
- `laboraid.file.classified`
- `laboraid.extraction.complete`
- `laboraid.cba.mined`
- `laboraid.rate-sheet.resolved`
- `laboraid.rate-sheet.validated`
- `laboraid.rate-sheet.published`
- `laboraid.cell.overridden`
- `laboraid.job.failed`

#### 3.6 CDK skeleton

```python
# cdk/laboraid_cdk/stacks/storage_stack.py — buckets
from aws_cdk import Duration
from aws_cdk import (
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_iam as iam,
    aws_kms as kms,
)

inputs_bucket = s3.Bucket(
    self, "InputsBucket",
    bucket_name=f"laboraid-{env}-l3-bucket-inputs",
    encryption=s3.BucketEncryption.KMS,
    encryption_key=master_key,
    versioned=True,
    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
    server_access_logs_bucket=audit_bucket,
    server_access_logs_prefix="inputs/",
    object_lock_enabled=(env == "prod"),
    lifecycle_rules=[
        s3.LifecycleRule(transitions=[
            s3.Transition(storage_class=s3.StorageClass.INTELLIGENT_TIERING, transition_after=Duration.days(30)),
            s3.Transition(storage_class=s3.StorageClass.DEEP_ARCHIVE, transition_after=Duration.days(365)),
        ]),
    ],
)

# TLS-only policy
inputs_bucket.add_to_resource_policy(iam.PolicyStatement(
    effect=iam.Effect.DENY,
    principals=[iam.AnyPrincipal()],
    actions=["s3:*"],
    resources=[inputs_bucket.bucket_arn, f"{inputs_bucket.bucket_arn}/*"],
    conditions={"Bool": {"aws:SecureTransport": "false"}},
))

# Trigger Step Function on ObjectCreated
inputs_bucket.add_event_notification(
    s3.EventType.OBJECT_CREATED_PUT,
    s3n.SfnDestination(main_pipeline_sfn),
)
```

#### 3.7 Retry / failure handling

- **S3 ObjectCreated** → EventBridge → Step Function
- **EventBridge** has built-in retry (up to 24 hours with exponential backoff)
- **Step Function** has per-state retry policy:
  ```python
  classifier_task.add_retry(
      errors=["States.TaskFailed", "Lambda.ServiceException"],
      interval=Duration.seconds(2),
      max_attempts=3,
      backoff_rate=2.0,
  )
  classifier_task.add_catch(
      failure_handler,
      errors=["States.ALL"],
      result_path="$.error",
  )
  ```
- **Final failure** → publishes to `laboraid-{env}-l6-sns-failures` with full execution context

#### 3.8 6-pillar coverage (L3)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | CloudWatch metrics on bucket size, request counts, error rates; CloudTrail data events on outputs bucket |
| **Security** | KMS CMK encryption; Object Lock on inputs+outputs (7-year retention); IAM bucket policies; TLS-only |
| **Reliability** | S3 11 9s durability; versioning; cross-AZ Aurora; PITR on DynamoDB; Object Lock prevents deletion |
| **Performance Efficiency** | DynamoDB on-demand; Aurora Serverless v2 (scales to 0.5 ACU when idle); Intelligent-Tiering |
| **Cost Optimization** | Lifecycle to Glacier Deep Archive after 1 year; on-demand DynamoDB; Aurora Serverless scales to zero |
| **Sustainability** | Serverless throughout; storage tiering reduces energy/storage waste |

---

### LAYER 4 — Document Processing Layer (Hybrid Path)

**Purpose:** Convert PDF → structured RawDocumentJSON. Branch on document type: text PDFs go to Docling, scanned PDFs to Textract.

> **🧱 From kernel:** `kernel/pipeline/ingest.py` (PDF discovery + text/image detection), `kernel/pipeline/ocr.py` (self-contained OCR via `rapidocr-onnxruntime` + `pypdfium2`), and `kernel/pipeline/extract.py` (per-union extractors that pull fields from PDFs into the canonical model). The kernel already covers PDF reading for text PDFs (via `pdfplumber`) and image PDFs (via rapidocr). For POC, the **kernel handles 100% of the document processing** — we don't need Docling or Textract on the critical path. Docling stays as a v1.1+ option for documents the kernel can't parse; Textract similarly. **What we build:** Lambda or Fargate wrappers that invoke the kernel as a Python library, plus the Document Classifier Lambda (kernel doesn't have classification — it assumes the union is named upfront).

#### 4.1 Assets

| Resource | Name | Purpose | Status |
|---|---|---|---|
| Lambda — document classifier | `laboraid-{env}-l4-fn-classifier` | Identify doc type, union, period, format (kernel doesn't classify; assumes union is named) | ✅ POC v1 — new |
| **Kernel-as-library inside ExtractorAgent container** | (lives in L5 ECR — see §5) | `kernel/pipeline/{ingest,ocr,extract,compute,pivot}.py` handles all of L4's text/scan PDF reading inside the agent container | ✅ POC v1 — **from kernel** |
| SQS queue | `laboraid-{env}-l4-sqs-extraction` | Pipeline workflow queue (async processing) | ✅ POC v1 — new |
| SQS DLQ | `laboraid-{env}-l4-sqs-dlq-extraction` | Failed extractions | ✅ POC v1 — new |
| SNS topic | `laboraid-{env}-l4-sns-extraction-events` | Extraction lifecycle events | ✅ POC v1 — new |
| ~~Fargate task — Docling service~~ | — | Kernel handles text PDFs via `pdfplumber` already | ⏸️ v1.1+ (fallback if kernel can't parse a future PDF) |
| ~~Lambda — Textract caller~~ | — | Kernel handles scanned PDFs via `rapidocr-onnxruntime` (no API cost, no system deps) | ⏸️ v1.1+ (fallback for OCR-stubborn scans) |
| ~~ECS cluster + ECR for Docling~~ | — | Not needed when kernel handles document processing | ⏸️ v1.1+ |
| ~~Lambda — unified-representation builder~~ | — | Kernel does ingest → extract in one process, returns canonical rows directly | ⏸️ v1.1+ |
| ~~Lambda — chunker~~ | `laboraid-{env}-l4-fn-chunker` | CBA chunking for KB ingestion — Bedrock KB deferred per §15 | ⏸️ v1.1+ |

#### 4.2 Document classifier (Stage 1)

**Input:** S3 key of uploaded file
**Output:** `ClassificationResult` JSON (see `docs/04`)

Logic:
1. Try filename pattern match (deterministic regex)
2. If unambiguous → return
3. If ambiguous → invoke Bedrock Claude Haiku via Bedrock InvokeModel API
4. Cross-validate three signals (filename + folder + content) — flag for human if disagree

#### 4.3 Docling Fargate service

**Why Fargate (not Lambda):** Docling has Python deps + native libs ~1.5GB; cold starts on Lambda are too slow.

```python
# v1.1+ — kernel handles document processing in POC, this is the deferred fallback shape
from aws_cdk import aws_ecs as ecs, aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as sfn_tasks

docling_task_def = ecs.FargateTaskDefinition(
    self, "DoclingTask",
    family=f"laboraid-{env}-l4-fargate-docling",
    cpu=1024,                      # 1 vCPU
    memory_limit_mib=2048,         # 2 GB
    runtime_platform=ecs.RuntimePlatform(
        cpu_architecture=ecs.CpuArchitecture.ARM64,
        operating_system_family=ecs.OperatingSystemFamily.LINUX,
    ),
)

docling_task_def.add_container(
    "Docling",
    image=ecs.ContainerImage.from_asset("./containers/docling"),
    logging=ecs.LogDrivers.aws_logs(stream_prefix="docling"),
    environment={
        "AWS_REGION": self.region,
        "OUTPUT_BUCKET": processed_bucket.bucket_name,
    },
)

# Triggered by Step Function as ECS Run Task action
run_docling_task = sfn_tasks.EcsRunTask(
    self, "RunDocling",
    cluster=ecs_cluster,
    task_definition=docling_task_def,
    launch_target=sfn_tasks.EcsFargateLaunchTarget(
        platform_version=ecs.FargatePlatformVersion.LATEST,
    ),
    integration_pattern=sfn.IntegrationPattern.RUN_JOB,  # wait for completion
)
```

#### 4.4 Textract integration

```python
# Lambda handler — laboraid-prod-l4-fn-textract
import boto3
textract = boto3.client('textract')

def handler(event, context):
    s3_key = event['s3_key']

    response = textract.analyze_document(
        Document={'S3Object': {'Bucket': INPUTS_BUCKET, 'Name': s3_key}},
        FeatureTypes=['TABLES', 'FORMS', 'SIGNATURES'],
    )
    # Parse Textract response, write to processed bucket
    ...
```

For PDFs >5 MB or >1 page: use async Textract API (`StartDocumentAnalysis` + SNS callback).

#### 4.5 Retry / failure handling

- Step Function `Choice` state evaluates `document_type` after Classifier:
  - `cba` → run chunker → ingest to Bedrock KB
  - `rate_notice` (text PDF) → Docling Fargate
  - `rate_notice` (scanned) → Textract Lambda
  - `unknown` → route to human review queue

- Per-task retries (3 attempts with exponential backoff)
- Final failures → `laboraid-{env}-l4-sns-extraction-events` with action=`failed` + DLQ message

#### 4.6 6-pillar coverage (L4)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | CloudWatch metrics per extraction path; per-stage latency tracking; X-Ray spans |
| **Security** | Fargate tasks in private subnet; ECR image scanning; least-privilege task role |
| **Reliability** | Step Function retries; DLQ; Fargate Spot fallback if On-Demand exhausted |
| **Performance Efficiency** | ARM64 Fargate; right-sized CPU/memory; async Textract for large docs |
| **Cost Optimization** | Fargate Spot for backfill jobs (70% cheaper); Textract paid only when needed (Tesseract first if added later) |
| **Sustainability** | ARM64 Graviton compute; spot capacity preference |

---

### LAYER 5 — AI Extraction Layer (Strands + AgentCore + Bedrock) — SCOPED FOR POC

**Purpose:** The agentic core. **ONE Strands agent (`ExtractorAgent`) on AgentCore Runtime** that wraps the kernel's extraction logic and adds the agentic reasoning + Bedrock-Claude fallback. **No Bedrock KB in POC v1** (deferred — see §15).

> **🧱 From kernel:** The agent's `@tool`s are **thin wrappers around kernel functions**. The agent imports `kernel/pipeline/extract.py` (the `EXTRACTORS[union]` dict — function per union), `kernel/pipeline/compute.py` (derived-column compute), `kernel/pipeline/pivot.py` (canonical-rows → ratesheet CSV), and `kernel/canonical/model.py` (`RateCell`, `ClassificationRow`, `r2()` rounding). The "extraction reasoning" the agent does is choosing **which** extractor to call, **whether** OCR fallback ran successfully (kernel reports confidence per cell), and **whether** to escalate to Bedrock Claude multi-modal for cells the kernel couldn't read. The actual PDF-to-numbers work is the kernel's.

> **POC scoping decision:** The signed SOW requires Strands + AgentCore and "agentic AI feasibility." It does NOT mandate the 9-agent topology in `docs/07`. To fit 2-week timeline, we implement ONE agent that genuinely demonstrates agentic reasoning (orchestrating the kernel's multi-path extraction, self-validating via steering, escalating to Bedrock when kernel confidence drops), with everything else as Lambdas. The full topology is the v1.1+ roadmap.

#### 5.1 Assets (POC scope)

| Resource | Name | Purpose | Status |
|---|---|---|---|
| **AgentCore Runtime — Extractor** | `laboraid-{env}-l5-agent-extractor` | Rate Notice → ExtractedDocument with multi-path reasoning | ✅ **POC v1** |
| ECR repository (extractor container) | `laboraid-{env}-l5-ecr-agent-extractor` | Strands agent container image | ✅ POC v1 |
| Lambda — classifier | `laboraid-{env}-l4-fn-classifier` (in L4) | Filename + regex; Haiku fallback via `bedrock:InvokeModel` if ambiguous | ✅ POC v1 (plain Lambda, not an agent) |
| Lambda — Bedrock InvokeModel wrappers | `laboraid-{env}-l5-fn-bedrock-{model}` (1 per model) | Direct Bedrock calls when an agent isn't needed (e.g., Haiku classification, Claude sanity review) | ✅ POC v1 |
| Bedrock Guardrail (PII) | `laboraid-{env}-l5-guardrail-pii` | Applied to all Bedrock calls | ✅ POC v1 |
| AgentCore execution role | `laboraid-{env}-l5-role-agent-extractor` | IAM role for the Extractor agent | ✅ POC v1 |
| ~~AgentCore Memory~~ | — | Not needed for single-agent POC | ⏸️ v1.1+ |
| ~~AgentCore Gateway~~ | — | Direct Lambda invocation works for single agent | ⏸️ v1.1+ |
| ~~AgentCore Identity~~ | — | Cognito direct integration sufficient | ⏸️ v1.1+ |
| ~~AgentCore Policy (Cedar)~~ | — | IAM + Bedrock Guardrails sufficient for POC | ⏸️ v1.1+ |
| ~~AgentCore Registry~~ | — | Skills catalog not needed for 1 agent | ⏸️ v1.1+ |
| ~~AgentCore Evaluations~~ | — | Manual fixture testing for POC | ⏸️ v1.1+ |
| ~~Bedrock Knowledge Base + S3 Vectors~~ | — | "Advanced RAG" ambiguous in SOW; defer or confirm | ⏸️ v1.1+ or scope clarification |
| ~~Orchestrator / CBAMiner / Validator / Citation / Concierge / ReviewAssist / ProfileDrafter agents~~ | — | All deterministic Lambdas instead | ⏸️ v1.1+ |

#### 5.2 Why just one agent (rationale)

The `ExtractorAgent` is genuinely agentic because it:
- **Chooses among multiple extraction paths** (text PDF parser → OCR → multi-modal Claude) based on confidence
- **Self-validates** via Strands steering (`SteeringHandler`) — won't return "done" until checksum passes
- **Retries adaptively** with different prompts/models when confidence is low

Other pipeline stages don't benefit from agentic reasoning:
- **Classification** is mostly regex with a cheap LLM fallback → Lambda
- **CBA rule extraction** could be agentic but for 5 known unions we author Profiles manually (saves Bedrock KB scope question)
- **Validation** is checksums + range checks → Lambda
- **Resolution** is deterministic DSL evaluation → Lambda
- **Rendering** is openpyxl → Lambda
- **Review-assist / Concierge** are admin UX features, out of POC v1

This keeps the architecture honest: agents where reasoning helps, Lambdas where determinism is better.

#### 5.3 ExtractorAgent — Strands implementation (kernel-wrapping)

The agent imports the kernel as a Python library (the container has `kernel/` installed via `uv pip install -e ./kernel`). Tools are thin wrappers around kernel functions; the agent's value-add is orchestration + steering + Bedrock fallback.

```python
# agents/extractor/agent.py
from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent
from strands.vended_plugins.steering import SteeringHandler, Guide, Proceed
import boto3, json, base64, os, tempfile, yaml

# Kernel imports — Ashwani's deterministic pipeline
from kernel.pipeline import extract as k_extract, compute as k_compute, pivot as k_pivot
from kernel.canonical.model import r2, ClassificationRow

s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime')

UNION_DIR_CACHE = '/tmp/agent-runs'   # AgentCore Runtime has /tmp scratch space


@tool
def stage_inputs_from_s3(union: str, s3_prefix: str) -> dict:
    """Download all CBA + Rate Notice PDFs from S3 into the kernel's expected layout
    (data/<union>/cba/...) so kernel.pipeline.extract.EXTRACTORS[union] can read them.
    """
    union_dir = f'{UNION_DIR_CACHE}/{union}'
    os.makedirs(f'{union_dir}/cba', exist_ok=True)
    files = list_s3_objects(s3_prefix)
    for key in files:
        local_path = f'{union_dir}/cba/{os.path.basename(key)}'
        s3.download_file(INPUTS_BUCKET, key, local_path)
    return {'union_dir': union_dir, 'files': len(files)}


@tool
def run_kernel_extractor(union: str, union_dir: str) -> dict:
    """Run kernel's per-union deterministic extractor.

    Returns the canonical rows + gaps list. The kernel does PDF reading (pdfplumber),
    OCR (rapidocr-onnxruntime for image PDFs), and per-union field mapping.
    """
    extractor_fn = k_extract.EXTRACTORS[union]   # 537 / 483 / 704 / 281 / 821
    rows, gaps = extractor_fn(union_dir)
    return {
        'rows': [serialize_classrow(r) for r in rows],
        'gaps': gaps,
        'gap_count': len(gaps),
    }


@tool
def compute_derived_columns(union: str, rows: list) -> list:
    """Apply kernel's compute.resolve_row() to each row using the union's Profile YAML.

    This is where Wage Differential = Wage × 1.15, Wage 1.5x, etc., get computed
    with half-up rounding via kernel.canonical.model.r2().
    """
    profile = yaml.safe_load(open(f'/opt/profiles/{union}.yaml'))
    resolved = [k_compute.resolve_row(profile, deserialize_classrow(r)) for r in rows]
    return resolved


@tool
def pivot_to_ratesheet_csv(union: str, rows: list, out_s3_key: str) -> dict:
    """Apply kernel.pipeline.pivot to produce the union's ratesheet CSV
    matching the groundtruth header.
    """
    profile = yaml.safe_load(open(f'/opt/profiles/{union}.yaml'))
    local_csv = f'{UNION_DIR_CACHE}/{union}/output.csv'
    n_rows = k_pivot.write_csv(profile, [deserialize_classrow(r) for r in rows], local_csv)
    s3.upload_file(local_csv, OUTPUTS_BUCKET, out_s3_key)
    return {'s3_key': out_s3_key, 'rows_written': n_rows}


@tool
def escalate_to_claude_multimodal(s3_key: str, profile_aliases: dict, missing_fields: list) -> dict:
    """Fallback when kernel + rapidocr can't read specific cells.

    Sends the raw PDF to Bedrock Claude Sonnet with a focused prompt asking only
    for the missing fields. This is the agentic 'Path C' — used when the kernel
    reports low confidence on specific cells, not as a default.
    """
    pdf_bytes = s3.get_object(Bucket=INPUTS_BUCKET, Key=s3_key)['Body'].read()
    response = bedrock.invoke_model(
        modelId='us.anthropic.claude-sonnet-4-6-v1:0',
        body=json.dumps({
            'anthropic_version': 'bedrock-2023-05-31',
            'max_tokens': 4000,
            'system': EXTRACT_RATE_NOTICE_SYSTEM_PROMPT,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'document', 'source': {'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': base64.b64encode(pdf_bytes).decode()}},
                    {'type': 'text', 'text': build_focused_prompt(profile_aliases, missing_fields)}
                ]
            }],
        })
    )
    return parse_focused_response(response, missing_fields)


@tool
def validate_total_package_checksum(union: str, rows: list) -> dict:
    """Verify sum of fringes + wage matches printed Total Package (±$0.05).

    Reads the printed total from the kernel's extraction notes (kernel stores
    notice_total alongside the rows when found).
    """
    journeyman_row = next(r for r in rows if r['classification'] == 'Journeyman')
    computed = journeyman_row['cells']['wage']['value'] \
             + sum(c['value'] for c in journeyman_row['cells'].values()
                   if c['canonical_field'].startswith(('health_welfare','pension','sis','annuity','industry')))
    expected = journeyman_row.get('notice_total')
    if expected is None:
        return {'passed': None, 'reason': 'notice did not print a Total Package'}
    return {'passed': abs(computed - expected) <= 0.05,
            'computed': r2(computed), 'expected': expected,
            'diff': r2(computed - expected)}


# Steering enforces self-validation + escalation logic
class ExtractorSteering(SteeringHandler):
    async def steer_before_tool(self, *, agent, tool_use, **kwargs):
        # Don't claim done before validating checksum
        if tool_use['name'] == 'return_extraction_complete':
            if not agent.checksum_validated:
                return Guide(reason='Run validate_total_package_checksum first.')
            if agent.unresolved_gaps and not agent.bedrock_fallback_attempted:
                return Guide(reason=f'Kernel reported {len(agent.unresolved_gaps)} gaps. '
                                     f'Try escalate_to_claude_multimodal for these fields '
                                     f'before declaring done: {agent.unresolved_gaps}')
        return Proceed(reason='OK.')


# The agent
agent = Agent(
    name='ExtractorAgent',
    system_prompt=EXTRACTOR_SYSTEM_PROMPT,
    tools=[stage_inputs_from_s3, run_kernel_extractor, compute_derived_columns,
           pivot_to_ratesheet_csv, escalate_to_claude_multimodal,
           validate_total_package_checksum],
    plugins=[ExtractorSteering()],
    trace_attributes={'service': 'laboraid-extractor', 'env': ENV},
)
```

**Note the dependency direction:** the agent depends on the kernel, not the other way around. The kernel works perfectly without AWS or Strands (it has its own CLI: `uv run python pipeline/run.py`). The agent adds: cloud orchestration, S3 I/O, Bedrock fallback for kernel gaps, and Strands steering for self-validation.

#### 5.4 AgentCore Runtime via CDK (single agent)

CDK doesn't yet have an L2 construct for AgentCore Runtime; use the CFN resource. POC deployment is minimal (no Memory/Gateway/Identity/Policy in v1):

```python
from aws_cdk import CfnResource

CfnResource(
    self, "ExtractorAgentRuntime",
    type="AWS::BedrockAgentCore::Runtime",
    properties={
        "AgentRuntimeName": f"laboraid-{env}-l5-agent-extractor",
        "RuntimeImageUri": f"{extractor_ecr.repository_uri}:latest",
        "Environment": {
            "PROFILE_BUCKET": profiles_bucket.bucket_name,
            "ENV": env,
        },
        "Observability": {"Enabled": True, "OtelEndpoint": "cloudwatch"},
        "ExecutionRoleArn": extractor_role.role_arn,
        # v1.1+ additions (commented out for POC):
        # "Memory": {"MemoryId": shared_memory.attr_memory_id, "Strategies": ["SEMANTIC"]},
        # "Gateway": {"GatewayId": tools_gateway.attr_gateway_id},
        # "Identity": {"CognitoUserPoolId": agent_user_pool.user_pool_id},
        # "Policy": {"PolicyArn": extractor_policy.attr_policy_arn},
    },
)
```

**Alternative deployment path (faster for POC):** use the AgentCore CLI directly:

```bash
cd agents/extractor
agentcore configure --name laboraid-prod-l5-agent-extractor
agentcore deploy
```

This bypasses CDK for the agent itself (CDK still manages the IAM role, ECR repo, and surrounding resources). Some teams find this faster for iteration.

#### Bedrock Knowledge Base — DEFERRED for POC (interstitial note between §5.4 and §5.5)

Bedrock KB + S3 Vectors for CBA RAG is **deferred to v1.1+** unless explicitly confirmed in scope with the customer. The SOW lists "advanced RAG or evaluation frameworks" as out-of-scope (Page 7) — Bedrock KB usage is ambiguous against that exclusion.

For POC, CBA structural rules (Foreman premium formulas, apprentice ladders, fund definitions) are encoded **manually in each union's Profile YAML** during the build. The 5 POC unions are known; this is feasible. See §15 for v1.1 roadmap.

If customer confirms KB is in scope, uncomment this section:

```python
# V1.1+ — Bedrock Knowledge Base with S3 Vectors (DEFERRED for POC)
# from aws_cdk import aws_bedrock as bedrock
# kb = bedrock.CfnKnowledgeBase(self, "CbaKnowledgeBase", ...)
```

#### 5.5 Bedrock model access

Pre-requisite (manual, do at AWS account setup):
1. AWS Console → Bedrock → Model access → Enable:
   - `anthropic.claude-sonnet-4-6-v1:0` (or latest)
   - `anthropic.claude-haiku-4-5-20251001-v1:0`
   - `amazon.titan-embed-text-v2:0`
2. Wait for access (usually instant; can take up to a few hours)

#### 5.6 Bedrock Guardrails

```python
from aws_cdk import aws_bedrock as bedrock

guardrail = bedrock.CfnGuardrail(
    self, "PiiGuardrail",
    name=f"laboraid-{env}-l5-guardrail-pii",
    blocked_input_messaging="Input contains PII; please redact before resubmitting.",
    blocked_outputs_messaging="Output would contain PII; suppressed.",
    sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
        pii_entities_config=[
            bedrock.CfnGuardrail.PiiEntityConfigProperty(type="EMAIL", action="BLOCK"),
            bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PHONE", action="BLOCK"),
            bedrock.CfnGuardrail.PiiEntityConfigProperty(type="US_SOCIAL_SECURITY_NUMBER", action="BLOCK"),
            bedrock.CfnGuardrail.PiiEntityConfigProperty(type="CREDIT_DEBIT_CARD_NUMBER", action="BLOCK"),
        ],
    ),
)
```

Applied to every agent's Bedrock InvokeModel calls.

#### 5.7 Retry / failure handling

- Bedrock throttling → Strands SDK has built-in exponential backoff; max 3 retries per call
- Agent crash → AgentCore Runtime restarts container; session preserved via Memory
- Agent timeout → Step Function timeout (configured per state, default 15 min)
- Failed extractions → published to `laboraid-{env}-l5-sns-agent-events` with action=`failed`

#### 5.8 6-pillar coverage (L5)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | AgentCore Observability (OTEL); X-Ray traces; CloudWatch RUM-equivalent for agent invocations |
| **Security** | AgentCore Policy (Cedar); Bedrock Guardrails (PII); per-agent IAM roles least-privilege; KMS encryption on Memory |
| **Reliability** | AgentCore Runtime session isolation; Bedrock SLAs; KB highly available (S3-backed); Strands steering enforces self-correction |
| **Performance Efficiency** | Right-sized models per task (Haiku for classification, Sonnet for extraction); Knowledge Base retrieval (chunked, not full-doc) |
| **Cost Optimization** | Haiku for cheap tasks; Sonnet only when needed; KB query caching; Memory reduces redundant LLM calls |
| **Sustainability** | Bedrock managed efficiency; agents scale to zero between invocations; minimal token use via focused KB retrieval |

---

### LAYER 6 — Validation & Human Review Layer — SCOPED FOR POC

**Purpose:** Quality gate before publish. **POC v1: 2-layer defense (confidence + checksums).** Year-over-year delta sanity and Article-20 awareness are deferred to v1.1+ (need at least 2 historical periods to compare, which we only have for the latest periods anyway). Human-in-the-loop review for low-confidence cells.

> **🧱 From kernel:** `kernel/pipeline/evaluate.py` already implements **post-hoc evaluation against groundtruth** — header diff, key-based row alignment, cell accuracy ±0.01, per-column/per-zone breakdown, mismatch list. We **reuse this as CI regression-test infrastructure** (run on every PR to confirm we haven't regressed the 704 99.6% or 483 100% Building accuracy). It is NOT the pre-publish validator, because it requires the groundtruth file — which is not available at production runtime. The pre-publish validator (checksum + range + confidence rollup) is new code we write that runs without groundtruth. **Also from kernel:** the `<union>.gaps.md` reports that itemize blank/divergent cells with reasons — we surface this in the admin UI's review queue.

#### 6.1 Assets (POC scope)

| Resource | Name | Purpose | Status |
|---|---|---|---|
| Lambda — checksum validator | `laboraid-{env}-l6-fn-validator-checksum` | Total package + apprentice % checks | ✅ POC v1 |
| Lambda — range checker | `laboraid-{env}-l6-fn-validator-range` | Per-column range checks (wage $5-200, fringes $0-30) | ✅ POC v1 |
| Lambda — confidence rollup | `laboraid-{env}-l6-fn-validator-confidence` | Aggregate per-cell confidence; route low-conf to review | ✅ POC v1 |
| Lambda — review router | `laboraid-{env}-l6-fn-review-router` | Writes flagged cells to DDB review queue | ✅ POC v1 |
| SNS topic — failures | `laboraid-{env}-l6-sns-failures` | Cross-layer failure notifications | ✅ POC v1 |
| SNS topic — successes | `laboraid-{env}-l6-sns-successes` | Cross-layer success notifications | ✅ POC v1 |
| SNS topic — review-needed | `laboraid-{env}-l6-sns-review-needed` | Human review notifications | ✅ POC v1 |
| SES configuration set | `laboraid-{env}-l6-ses-notifications` | Email to ops | ✅ POC v1 |
| Lambda — Slack notifier | `laboraid-{env}-l6-fn-slack-notify` | Slack channel posts | ✅ POC v1 |
| EventBridge rules | (event routing) | Failure + success topic routing | ✅ POC v1 |
| ~~Lambda — YoY delta~~ | — | Year-over-year sanity with Article-20 awareness | ⏸️ v1.1+ |
| ~~Validator AI sanity review~~ | — | Claude reviews anomalies | ⏸️ v1.1+ |

#### 6.2 SNS topic subscriptions

```python
from aws_cdk import aws_sns_subscriptions as subs

# Failure topic subscriptions
failures_topic.add_subscription(subs.EmailSubscription("ops-laboraid@northbay.com"))
failures_topic.add_subscription(subs.LambdaSubscription(slack_notifier_fn))
failures_topic.add_subscription(subs.SqsSubscription(failure_audit_queue))

# Success topic — quieter, just metrics + Aurora write
successes_topic.add_subscription(subs.LambdaSubscription(metrics_recorder_fn))

# Review-needed — emails admin + posts to Slack
review_needed_topic.add_subscription(subs.EmailSubscription("reviewers-laboraid@northbay.com"))
review_needed_topic.add_subscription(subs.LambdaSubscription(slack_notifier_fn))
```

#### 6.3 Event payload schemas

All SNS messages are structured JSON:

```json
// laboraid.job.failed
{
  "event": "laboraid.job.failed",
  "version": "1.0",
  "timestamp": "2026-06-02T17:30:00Z",
  "env": "prod",
  "job_id": "j-abc123",
  "stage": "l4_extract",
  "union_local": 704,
  "period": "2026-07-01",
  "error": {
    "type": "ExtractionConfidenceTooLow",
    "message": "OCR confidence 0.62 below threshold 0.85",
    "details": { ... }
  },
  "next_action": "human_review",
  "links": {
    "execution": "https://console.aws.amazon.com/states/...",
    "input_file": "s3://laboraid-prod-l3-bucket-inputs/...",
    "review_url": "https://admin-prod.laboraid.app/review/j-abc123"
  }
}
```

#### 6.4 6-pillar coverage (L6)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | SNS + EventBridge + dashboards; structured events; auto-retry then escalate |
| **Security** | SNS topic policies (only authorized services publish); encrypted messages; CloudTrail audit |
| **Reliability** | SNS at-least-once delivery; multi-AZ; DLQ on subscriber Lambdas |
| **Performance Efficiency** | Async event-driven (no polling); SNS fanout for parallel processing |
| **Cost Optimization** | SNS is pay-per-message; no idle infrastructure |
| **Sustainability** | Event-driven scale-to-zero |

---

### LAYER 7 — Data Storage & Downstream Consumption Layer

**Purpose:** Final approved rate sheets in Aurora + canonical JSON in S3. LaborAid Calculator Engine consumes via API.

> **🧱 From kernel:** `kernel/pipeline/pivot.py` already produces a **CSV** ratesheet matching the groundtruth header for each union. The 704 / 821 / 483 / 281 groundtruths are CSV — so for those, kernel's pivot is sufficient. **537's groundtruth is XLSX** — for that one we add a small `xlsx renderer Lambda` that reads kernel's CSV output and writes the same data in XLSX with the same column order. **What we build new:** Aurora `rate_periods` + `rate_cells` schema (kernel doesn't have a DB), API endpoints for LaborAid Calculator to consume, and the xlsx renderer for 537.

#### 7.1 Assets

| Resource | Name | Purpose |
|---|---|---|
| Aurora schemas | (in L3 Aurora cluster) | `rate_periods`, `rate_cells`, `audit_log` (defined in L3 §3.3) |
| S3 outputs bucket | `laboraid-{env}-l3-bucket-outputs` | Canonical JSON + xlsx + CSV + Articles (defined in L3) |
| Lambda — renderer | `laboraid-{env}-l7-fn-renderer-xlsx` | Generates xlsx from canonical JSON |
| Lambda — CSV renderer | `laboraid-{env}-l7-fn-renderer-csv` | Generates LaborAid-format CSV |
| Lambda — articles renderer | `laboraid-{env}-l7-fn-renderer-articles` | Generates Articles sheet/file |
| Lambda — calculator integration | `laboraid-{env}-l7-fn-calc-publish` | Pushes canonical JSON to LaborAid Calculator API |
| Step Functions task — publish | (within main pipeline) | Marks period as published + emits event |

#### 7.2 Renderer Lambda (xlsx)

```python
import openpyxl
import boto3
import json

def handler(event, context):
    canonical = json.loads(s3.get_object(...)['Body'].read())
    profile = json.loads(s3.get_object(...)['Body'].read())

    wb = openpyxl.Workbook()
    # ... render per Profile output_schema (per docs/02 §6)

    output_bytes = save_to_bytes(wb)
    s3.put_object(
        Bucket=OUTPUTS_BUCKET,
        Key=f"{tenant}/{trade}/{local}/{period}/rate_sheet_v{version}.xlsx",
        Body=output_bytes,
        ServerSideEncryption='aws:kms',
        SSEKMSKeyId=MASTER_KEY_ID,
        Tagging='DataClassification=engine-output&Layer=l7',
    )
    return {'s3_key': key, 'version': version}
```

#### 7.3 LaborAid Calculator integration

Two integration patterns supported:

**Pattern A: Pull (LaborAid Calculator polls our API)**
- LaborAid Calculator calls `GET /v1/unions/{local}/rate-sheets/{period}` periodically
- Our API returns canonical JSON
- Calculator caches locally

**Pattern B: Push (we notify Calculator on publish)**
- On `laboraid.rate-sheet.published` event, Lambda `calc-publish` POSTs to LaborAid Calculator's webhook URL
- Webhook URL stored in Secrets Manager: `laboraid-{env}-l7-secret-calc-webhook`
- Retries with exponential backoff (up to 24h)

POC default: **Pattern A** (simpler, no webhook to maintain).

#### 7.4 6-pillar coverage (L7)

| Pillar | Implementation |
|---|---|
| **Operational Excellence** | Aurora Performance Insights; query metrics; data quality monitoring (DAR/SLA) |
| **Security** | KMS-encrypted Aurora + S3; IAM Database Authentication for service connections; row-level security |
| **Reliability** | Aurora multi-AZ; PITR; S3 11 9s; Object Lock on outputs |
| **Performance Efficiency** | Aurora indexes on (period_id, zone, package, column_name); JSONB GIN for provenance queries |
| **Cost Optimization** | Aurora Serverless v2 scales to 0.5 ACU when idle; S3 lifecycle to Glacier |
| **Sustainability** | Serverless DB + storage tiering |

---

## 5. End-to-end flow trace

### Happy path — new Rate Notice arrives

```
[T+0ms]    User uploads PDF via admin UI (L1)
           → React SPA calls POST /v1/uploads (L2)
           → API Lambda returns presigned URL
           → Browser PUTs PDF to S3 inputs bucket (L3)

[T+50ms]   S3 ObjectCreated event → EventBridge (L3)
           → Step Function main pipeline starts (L3 sfn-main)
           → DDB jobs table: status=ingested

[T+1s]     SFN Stage 1: Classify (L4 fn-classifier)
           → Reads file metadata, filename pattern, folder
           → Identifies: Rate Notice, union 704, period 2026-07-01, text PDF
           → DDB files table updated with classification
           → SNS published: laboraid.file.classified

[T+2s]     SFN Stage 2: Choice on format
           → text PDF → run Docling Fargate (L4 fargate-docling)
                       → OR if low confidence after Docling → ExtractorAgent (L5)
           → scanned PDF → run Textract (L4 fn-textract)
           → unknown → → human queue

[T+10s]    Docling Fargate completes
           → RawDocumentJSON written to S3 processed bucket
           → ExtractorAgent (L5) invoked via AgentCore
           → Reads Profile from S3 profiles bucket
           → Returns ExtractedDocument JSON
           → SNS: laboraid.extraction.complete

[T+12s]    SFN Stage 3: Choice on document type
           → if cba → store in S3 cba-corpus bucket for reference (POC: no auto-mining)
                       (v1.1+: chunker + KB ingestion + CBAMinerAgent)
           → if rate_notice → continue (Profile + hand-authored manifest in S3)

[T+13s]    SFN Stage 4: Resolve (deterministic resolver Lambda — no agent)
           → Reads Profile YAML + Manifest JSON + ExtractedDocument
           → Evaluates wage formulas via DSL
           → Outputs CanonicalRateSheet to S3 outputs bucket
           → Tags every cell with basic provenance (source + citation)

[T+14s]    SFN Stage 5: Validate (POC: 2-layer = checksum + range; deterministic Lambdas)
           → Checksum: sum of fringes + wage = printed Total Package ✓
           → Range check: all wages within $5-200 range ✓
           → Confidence rollup: all cells > 0.95 ✓
           → Auto-publish eligible
           (v1.1+: YoY delta, Article-20 awareness, AI sanity review)

[T+15s]    SFN Stage 6: Render & Publish (L7 fn-renderer-xlsx, fn-renderer-csv)
           → xlsx + CSV + Articles file written to S3
           → Aurora rate_periods + rate_cells inserted
           → SNS: laboraid.rate-sheet.published
           → SES email to admin

[T+18s]    LaborAid Calculator Engine notified (Pattern A: polls API)
           → GET /v1/unions/704/rate-sheets/2026-07-01 returns canonical JSON
           → Calculator uses new rates from effective date
```

**Total: ~18 seconds for a clean text-PDF Notice.**

### Failure path — extraction confidence too low

```
[T+10s]    Docling returns low confidence
           → ExtractorAgent steering blocks "return done"
           → ExtractorAgent retries with Claude multi-modal (Path C)
           → Still low confidence on 2 cells (OCR could not read SIS Class 5)
           → Validator flags those 2 cells
           → Step Function transitions to ReviewQueue state
           → DDB review queue: rows inserted
           → SNS: laboraid.review.needed
           → SES + Slack notify admin

[T+5min]   Admin opens review UI
           → Side-by-side: PDF page + extracted candidates
           → Selects correct value
           → POST /v1/cells/{id}/override (L2)
           → Override Lambda updates Aurora + DDB
           → SNS: laboraid.cell.overridden

[T+5min+30s] Step Function resumes (via Wait-for-callback pattern)
           → Re-validates → all passes
           → Continues to Render & Publish
```

### Hard failure path — Bedrock throttle / OCR catastrophic

```
[T+10s]    Bedrock throttled (rare, but possible at scale)
           → Strands SDK retries 3x with backoff
           → Still failing
           → Strands raises exception
           → Step Function catches → transitions to FailureHandler state
           → Job marked failed in DDB
           → SNS: laboraid.job.failed (with full context)
           → DLQ: laboraid-{env}-l4-sqs-dlq-extraction (message retained 14 days)
           → SES + Slack: notify ops
           → Admin can manually retry from admin UI
```

---

## 6. SNS topic catalog (cross-cutting)

| Topic | Name | Subscribers |
|---|---|---|
| Failures | `laboraid-{env}-l6-sns-failures` | Email (ops), Slack, audit Lambda |
| Successes | `laboraid-{env}-l6-sns-successes` | Metrics recorder Lambda |
| Review needed | `laboraid-{env}-l6-sns-review-needed` | Email (reviewers), Slack |
| Agent events | `laboraid-{env}-l5-sns-agent-events` | Observability Lambda |
| Extraction events | `laboraid-{env}-l4-sns-extraction-events` | Metrics Lambda |
| Lifecycle events | `laboraid-{env}-l3-sns-lifecycle` | EventBridge bus |

All topics encrypted with KMS, with topic policies restricting publishers to known service ARNs.

---

## 7. IAM roles & policies

### 7.1 Per-Lambda execution roles

Each Lambda has its own role with name `laboraid-{env}-{layer}-role-{fn_name}`. Permissions are explicit per-Lambda — no broad `AWSLambdaBasicExecutionRole` only; we attach specific S3, DynamoDB, Bedrock perms as needed.

### 7.2 AgentCore execution roles

Each AgentCore Runtime has its own role `laboraid-{env}-l5-role-agent-{agent_name}`. Permissions:
- `bedrock:InvokeModel` (specific model ARNs)
- `bedrock:Retrieve` (specific KB ARN)
- `s3:GetObject` / `s3:PutObject` (specific buckets + prefixes)
- `dynamodb:GetItem` / `PutItem` (specific tables)
- AgentCore service permissions (auto-attached by AgentCore Runtime)

### 7.3 Cross-account access (not in POC)
N/A. POC is single-account.

---

## 8. Operational dashboards

CloudWatch dashboards (one per layer):

| Dashboard | Name | Widgets |
|---|---|---|
| Overview | `laboraid-{env}-dashboard-overview` | Job throughput, success rate, mean latency, cost |
| Pipeline | `laboraid-{env}-dashboard-pipeline` | Per-stage latency, retries, failures |
| Agents | `laboraid-{env}-dashboard-agents` | Per-agent invocation count, latency, token spend, steering interventions |
| Storage | `laboraid-{env}-dashboard-storage` | Bucket sizes, DDB throttles, Aurora ACU usage |
| API | `laboraid-{env}-dashboard-api` | API Gateway 4xx/5xx rates, p99 latency |

CloudWatch alarms:

| Alarm | Threshold | Action |
|---|---|---|
| Pipeline failure rate | > 10% in 1h | SNS → ops email + Slack |
| Bedrock spend | > $100/day | SNS → ops email |
| Aurora CPU | > 80% sustained 15min | SNS → ops |
| DDB throttling | any | SNS → ops |
| Review queue depth | > 50 cells | SNS → reviewers |
| API 5xx rate | > 1% in 5min | SNS → ops + PagerDuty (if configured) |

---

## 9. AWS Well-Architected 6 Pillars — summary

| Pillar | Cross-layer summary |
|---|---|
| **1. Operational Excellence** | CDK IaC; structured logging (Powertools); X-Ray + OTEL; CloudWatch dashboards + alarms; SNS event-driven ops; runbooks committed to repo |
| **2. Security** | KMS CMK encryption everywhere; Cognito MFA; IAM least-privilege per Lambda + agent; AgentCore Policy (Cedar); Bedrock Guardrails (PII); WAF; TLS-only; CloudTrail; Object Lock; secrets in Secrets Manager (auto-rotated) |
| **3. Reliability** | Serverless multi-AZ; Aurora multi-AZ with PITR; S3 11 9s + versioning; Step Function retries with backoff; DLQs; AgentCore Runtime session isolation; SNS at-least-once delivery |
| **4. Performance Efficiency** | ARM64 Graviton throughout; right-sized memory; HTTP API (not REST); on-demand DynamoDB; Aurora Serverless v2; Bedrock model selection per task (Haiku vs Sonnet); KB chunked retrieval (not full-doc) |
| **5. Cost Optimization** | Scale-to-zero serverless; Fargate Spot for backfill; S3 Intelligent-Tiering + Glacier lifecycle; Aurora Serverless v2 min 0.5 ACU; on-demand DDB; AWS Budgets alerts; per-pipeline cost tracking via tags |
| **6. Sustainability** | Graviton ARM64 across all compute; event-driven scale-to-zero between invocations; storage tiering reduces footprint; managed services (AWS handles efficiency); minimal data transfer (same-region) |

---

## 10. Pre-build checklist (do these FIRST before code)

These are blockers — start them immediately, in parallel:

| # | Action | Owner | Why |
|---|---|---|---|
| 1 | Confirm AWS account credentials + admin role | LaborAid IT | All CDK deploys need this |
| 2 | Enable Bedrock model access (Claude Sonnet, Haiku, Titan Embed) | LaborAid AWS admin | Manual click in console; takes 0-2h |
| 3 | Confirm AgentCore Runtime available in us-east-1 | Verify in AWS console | **Critical** — POC requires it. If unavailable, fall back to ECS Fargate hosting (Strands code is unchanged; just a different runtime) |
| 4 | ~~Confirm S3 Vectors available in us-east-1~~ | — | ⏸️ **v1.1+** (Bedrock KB deferred from POC) |
| 5 | Set up GitHub repo with branch protection | NBS | Per SOW: code in GitHub |
| 6 | Resolve the 5 BLOCKER questions from `discovery/00_README.md` Q1-Q5 | LaborAid + NBS lead | 30-minute working session |
| 7 | Get customer's existing Excel rate sheets for 5 unions | LaborAid | Already in `From Customer/` — confirm versions are current |
| 8 | Get customer's UAT scenarios | LaborAid | Per SOW Assumption K |
| 9 | Set up CloudWatch + cost budget alarms | NBS | Avoid runaway Bedrock spend |
| 10 | Create Cognito user pool + invite admin users | NBS | Required before deploying admin UI |

---

## 11. CDK monorepo structure

```
laboraid-rate-engine/
├── cdk/                                     # Python CDK (aws-cdk-lib for Python)
│   ├── app.py                               # CDK app entry
│   ├── cdk.json
│   ├── pyproject.toml                       # CDK Python deps (managed by uv)
│   ├── uv.lock
│   └── laboraid_cdk/                        # Python package
│       ├── __init__.py
│       ├── stacks/
│       │   ├── __init__.py
│       │   ├── network_stack.py
│       │   ├── security_stack.py
│       │   ├── storage_stack.py
│       │   ├── processing_stack.py
│       │   ├── ai_stack.py
│       │   ├── validation_stack.py
│       │   ├── api_stack.py
│       │   ├── ui_stack.py                  # Hosts the React SPA on S3+CloudFront+OAC
│       │   └── observability_stack.py
│       ├── constructs/
│       │   ├── __init__.py
│       │   ├── strands_agent.py             # Custom construct for AgentCore Runtime
│       │   ├── tagged_lambda.py             # Lambda with mandatory tags + defaults
│       │   ├── tagged_bucket.py             # S3 bucket with mandatory tags + encryption
│       │   └── sns_topic_with_subs.py       # SNS topic with email + Slack + Lambda subs
│       ├── aspects/
│       │   ├── __init__.py
│       │   └── mandatory_tags.py            # IAspect — enforces 13 mandatory tags
│       ├── config/
│       │   ├── __init__.py
│       │   ├── dev.py
│       │   └── prod.py
│       └── util/
│           ├── __init__.py
│           └── naming.py                    # name(env, layer, type, purpose) -> str
├── lambdas/
│   ├── api/                                # L2 API Lambdas (Python)
│   │   ├── upload-presign/
│   │   ├── job-status/
│   │   ├── ratesheet-list/
│   │   ├── ratesheet-get/
│   │   ├── ratesheet-publish/
│   │   ├── cell-override/
│   │   ├── ask-cba/
│   │   ├── profile-list/
│   │   └── profile-update/
│   ├── processing/                          # L4 processing Lambdas
│   │   ├── classifier/
│   │   ├── textract/
│   │   ├── unifier/
│   │   └── chunker/
│   ├── validation/                          # L6 validation Lambdas
│   │   ├── checksum/
│   │   ├── range/
│   │   ├── yoy/
│   │   └── review-router/
│   ├── rendering/                           # L7 rendering Lambdas
│   │   ├── renderer-xlsx/
│   │   ├── renderer-csv/
│   │   └── renderer-articles/
│   └── shared/                              # Shared Python utilities
│       ├── schemas/                         # Pydantic models for all artifacts
│       ├── dsl/                             # Formula DSL evaluator
│       ├── provenance/                      # Provenance builder
│       └── powertools/                      # Logging + tracing wrappers
├── agents/                                  # L5 Strands agents
│   ├── orchestrator/
│   │   ├── agent.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── classifier/
│   ├── extractor/
│   ├── cbaminer/
│   ├── validator/
│   ├── citation/
│   ├── concierge/
│   ├── reviewassist/
│   └── profiledrafter/
├── containers/                              # L4 + L5 containers
│   └── docling/
│       ├── Dockerfile
│       └── server.py
├── ui/                                      # L1 React SPA
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
├── profiles/                                # Per-union YAML Profiles
│   ├── pipefitter-537.yaml
│   ├── sprinkler-704.yaml
│   ├── sprinkler-821.yaml
│   ├── sprinkler-483.yaml
│   └── sprinkler-281.yaml
├── fixtures/                                # Test fixtures for regression
│   ├── 537/
│   ├── 704/
│   └── ...
├── docs/                                    # Operational docs
│   ├── RUNBOOK.md
│   ├── ARCHITECTURE.md
│   └── ONBOARDING.md
├── scripts/                                 # Helper scripts
│   ├── deploy.sh
│   ├── seed-profiles.sh
│   └── invoke-pipeline.sh
└── README.md
```

---

## 12. Build sequence (for parallelism)

Tasks that can run in parallel (separate engineers / overnight build):

**Track A — Infrastructure (CDK)** — ALL NEW
1. Storage stack
2. Security stack
3. Network stack
4. API stack skeleton (Lambdas as placeholders)
5. UI stack skeleton
6. Observability stack

**Track B — Engine code (Python)** — ⭐ **MOSTLY DONE IN KERNEL**
1. ~~Pydantic schemas (ClassificationResult, ExtractedDocument, RuleManifest, CanonicalRateSheet, Profile)~~ — **Kernel has `RateCell` + `ClassificationRow` (dataclasses) and profile YAML loaders.** Wrap in pydantic if we want strict validation at API boundaries; otherwise reuse as-is.
2. ~~DSL evaluator~~ — **Kernel's `compute.resolve_row()` handles `multiplier_of`/`factor` formulas with half-up rounding.** Sufficient for POC. Extend only if 281's half-year sub-classes or 483's date-keyed Foreman premium need conditional logic the kernel doesn't have.
3. ~~Resolver Lambda~~ — **Kernel's `pipeline.run.run_union()` is the resolver.** Call directly from the agent or wrap in a Lambda if Step Functions needs it as a state.
4. xlsx Renderer Lambda for 537 — NEW (kernel produces CSV only)
5. Pre-publish Validation Lambdas — NEW (kernel's `evaluate.py` is post-hoc against groundtruth; this is checksum + range + confidence with no groundtruth needed at runtime)

**Track C — Strands Agent (POC: 1 agent)** — NEW (wraps kernel)
1. ExtractorAgent — Strands SDK setup, `@tool` definitions wrapping `kernel.pipeline.extract.EXTRACTORS[union]`, `SteeringHandler`, system prompt
2. Container build (kernel installed via `uv pip install -e ./kernel`) + ECR push
3. AgentCore Runtime deploy (`agentcore deploy` or CDK CFN resource)
4. Integration test with sample 537 + 704 Rate Notice PDFs
5. Bedrock Claude multi-modal fallback wired for kernel gaps (`escalate_to_claude_multimodal` tool)
6. (Defer Orchestrator, CBAMiner, Validator, Citation, Concierge, ReviewAssist, ProfileDrafter to v1.1+)

**Track D — Profiles (YAML, hand-authored)** — 🟡 **3 of 5 DONE IN KERNEL**
1. ~~537 Profile~~ → ✅ `kernel/profiles/pipe_fitters_537.yaml`
2. ~~704 Profile~~ → ✅ `kernel/profiles/sprinkler_fitters_704.yaml`
3. ~~483 Profile~~ → ✅ `kernel/profiles/sprinkler_fitters_483.yaml`
4. 821 Profile — **NEW** (use kernel's `.claude/harness` to author + iterate against groundtruth)
5. 281 Profile — **NEW** (same; note 281 has the half-year sub-class issue from `docs/04`)

**Track D-bis — Missing extractors (Python)** — NEW (2 extractors)
1. `kernel/pipeline/extract.py` — add `extract_281` and `extract_821` functions following the pattern of `extract_483` and `extract_704`
2. Register in the `EXTRACTORS` dict
3. Test via `uv run python pipeline/run.py --union sprinkler_fitters_281` and iterate against the groundtruth comparison

**Track E — UI (React)** — ALL NEW
1. SPA skeleton (Vite + React + Cognito auth)
2. Upload page (presigned-URL flow)
3. Job status dashboard
4. Rate sheet review page (side-by-side PDF + extracted CSV from kernel + provenance from `RateCell.source_doc`)
5. Manual override flow (writes to Aurora; updates kernel's view of the period)
6. Gaps panel (renders kernel's `<union>.gaps.md` content)

These tracks converge in the integration phase (deploy + end-to-end test).

**Summary of net-new effort** (after subtracting what the kernel covers):
- Track A: 100% new (CDK is greenfield)
- Track B: ~20% new (mostly xlsx renderer + pre-publish validator)
- Track C: 100% new (Strands agent wrapping kernel)
- Track D: 40% new (2 of 5 profiles + 2 of 5 extractors)
- Track E: 100% new (admin UI)

**~5-7 days of engineering effort saved by kernel adoption** vs greenfield.

---

## 13. Documentation deliverables (per SOW)

Per SOW Page 6, these are required:
- ✅ Architecture diagrams → `docs/ARCHITECTURE.md` + drawing
- ✅ Infrastructure and configuration documentation → this doc + `cdk/` source
- ✅ Onboarding and admin documentation → `docs/ONBOARDING.md` + Admin UI in-app help
- ✅ Test and validation reports → `docs/UAT_RESULTS.md` (produced at end of UAT)

---

## 14. Bottom line

This spec is layer-mapped to the customer's SOW architecture diagram, has every resource named per the convention, tagged per the strategy, and shows the end-to-end flow with retry/success/failure paths via SNS. It's CDK-deployable and split into 8 stacks that can be deployed independently.

**The build starts from Ashwani's kernel** (`laboraid-rate-engine/kernel/`, imported via `git subtree` from `bitbucket.org:northbay/labor_aid_poc.git`). The kernel already delivers:
- PDF reading + OCR for the 5 POC unions' document formats
- Per-union extractors for 537, 483, 704 (measured: 99.6% / 100%-Building / 67.4%)
- Canonical model with per-cell provenance
- Derived-column compute with half-up rounding
- CSV ratesheet output matching customer groundtruth
- Post-hoc evaluator (for CI regression testing)

**What we add on top** is the AWS+Strands+UI shell:
- Strands `ExtractorAgent` on AgentCore Runtime that wraps kernel as `@tool`s
- CDK deployment across 8 stacks
- API Gateway + Lambdas (upload, status, override, publish)
- React admin SPA
- Pre-publish validators (checksum, range, confidence — kernel's evaluator is post-hoc only)
- 2 missing extractors + profiles (281, 821)
- Bedrock Claude fallback for cells the kernel can't read

**For 2-week build:** start the pre-build checklist (§10) now in parallel. Tracks A-E (§12) can run concurrently — Tracks B (engine code) and D (profiles) are mostly done in the kernel, so engineering focus shifts to Tracks A (CDK), C (Strands agent), E (UI), D-bis (2 missing extractors).

The architecture covers all 6 Well-Architected pillars by design — not bolt-on. Every resource has a name, tag, and a clear owner. Every failure mode goes through SNS for visibility. Every cell ends up in Aurora with provenance, traceable to source (the kernel already produces per-cell `source_doc` + `source_locator` for free).

**Agentic AI commitment is satisfied by a single Strands `ExtractorAgent` on AgentCore Runtime that demonstrably orchestrates the kernel's multi-path extraction, self-validates via steering, and escalates to Bedrock Claude multi-modal when the kernel reports gaps.** This meets the SOW's contractual requirements for Strands + AgentCore + "AI Agentic feasibility" without over-building.

**Build the skeleton today; the kernel does the heavy lifting; UAT in 2 weeks.**

---

## 15. Explicitly deferred to v1.1+ (post-POC scope)

These were in our broader design (`docs/01-08`) but are **NOT being built for the POC** to fit the 2-week timeline. Each is a candidate for a v1.1+ change order.

> **Note on kernel-covered items:** A few items previously labeled "deferred" in earlier versions of this doc are actually **already implemented by the kernel** (PDF reading, OCR via rapidocr, per-union extraction for 3 of 5 unions, derived-column compute, half-up rounding, gaps reporting). Those are now in the "kernel reuse" matrix at the top of this doc — not "deferred." Items below remain genuinely deferred.

### 15.1 Additional agents (8 deferred)

| Agent | Purpose | Why deferred |
|---|---|---|
| `OrchestratorAgent` | Top-level dispatch | Step Functions handles orchestration deterministically — no reasoning needed |
| `ClassifierAgent` | Document classification | Filename regex + 1 Bedrock InvokeModel fallback fits in a Lambda |
| `CBAMinerAgent` | Auto-mine CBA structural rules | Manual Profile authoring for 5 known unions is faster + safer for POC |
| `ValidatorAgent` | LLM sanity review of anomalies | Need 2+ historical periods to anomaly-detect; defer |
| `CitationAgent` | KB-grounded citations | Needs Bedrock KB which is deferred |
| `ConciergeAgent` | "Ask the CBA" admin UX | Nice-to-have, not in SOW deliverables list |
| `ReviewAssistAgent` | Semantic memory of past overrides | Needs accumulated data; meaningful only after weeks of use |
| `ProfileDrafterAgent` | Auto-draft Profile for new unions | 5 POC unions get hand-authored Profiles |
| `BackfillAgent` | Process historical periods | POC focuses on latest period per union |

### 15.2 AgentCore sub-services (6 deferred)

| Service | Reason deferred |
|---|---|
| AgentCore Memory | Single-agent POC doesn't need cross-session learning |
| AgentCore Gateway | Direct Lambda invocation works for one agent's tool needs |
| AgentCore Identity | Cognito direct integration sufficient |
| AgentCore Policy (Cedar) | IAM least-privilege + Bedrock Guardrails sufficient for POC |
| AgentCore Evaluations | Manual fixture testing for POC; automated eval is v1.1 |
| AgentCore Registry | Skills catalog has value only with multiple agents |

### 15.3 Bedrock Knowledge Base + S3 Vectors

- "Advanced RAG" listed as out-of-scope in SOW (Page 7) — ambiguous against Bedrock managed KB
- POC handles CBA structural rules via hand-authored Profile YAMLs (one per union)
- v1.1: when scaling beyond 5 known unions, KB becomes necessary for new-union onboarding

### 15.4 Validation layer features

| Feature | Reason deferred |
|---|---|
| Year-over-year delta sanity | Needs published prior periods to compare; POC starts fresh |
| Article-20 uniformity awareness | Domain-specific knowledge baked into the validator; v1.1 |
| Cross-source agreement (Notice ↔ CBA-derived) | Adds complexity; checksums suffice for POC |
| AI sanity review of outliers | Couples to YoY which is deferred |

### 15.5 Per-cell provenance

- POC: JSON-level provenance only (each cell has `source` + basic citation), stored in Aurora `rate_cells.provenance` JSONB column
- Not in POC: Admin UI side-panel drilldown with PDF page rendering
- Not in POC: 6-source provenance taxonomy (just `direct` + `derived` + `manual` for v1)
- Articles output: defer auto-population for v1.1

### 15.6 Admin UI features deferred

- "Ask the CBA" Q&A chat → v1.1
- Year-over-year diff view → v1.1
- Profile editor (form-based) → v1.1 (POC: edit YAML directly in repo)
- Semantic memory of override patterns → v1.1
- Multi-tenant separation → v1.3 per the original roadmap

### 15.7 Operational features deferred

- IaC for everything except core stacks (per SOW schedule "Deployment without IaC")
- Cadence reminders (expected next-Notice notifications)
- Bulk backfill workflow
- Cross-region DR

### 15.8 What customer will see at UAT

**Will work:**
1. Upload a PDF Rate Notice via admin UI
2. Engine classifies, extracts (via ExtractorAgent on AgentCore), validates, produces rate sheet
3. Admin reviews side-by-side (PDF + extracted JSON)
4. Override low-confidence cells if needed
5. Publish → xlsx + CSV + canonical JSON in S3
6. LaborAid Calculator can fetch canonical JSON via API
7. Audit trail in Aurora + S3 for every published rate sheet

**Will NOT work (yet):**
- Asking the CBA arbitrary questions
- Comparing this period to prior periods automatically
- Auto-drafting a Profile for a brand-new union
- Semantic memory of past corrections
- Multi-agent orchestration showing agents talking to each other

**Customer communication:** explicitly frame the POC as proving feasibility of agentic extraction. Multi-agent orchestration, advanced validation, and admin UX features are the v1.1 expansion roadmap.
