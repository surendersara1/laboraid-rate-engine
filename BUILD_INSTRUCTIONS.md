# Build Instructions — Overnight Code-Gen Run

**Audience:** the overnight Claude CLI / code-generation library that will build this repo from templates and partials.
**Working branch:** `feat/aws-strands-integration`
**Mode:** unattended; **NO manual interruptions** between generations.
**Authority:** these instructions are operational. **Design rationale lives in `docs/09_Technical_Implementation_Spec.md`** (call it "Spec/09" below). When a build item needs context — read the referenced spec sections.

> The spec describes **what** the system looks like + why. This file specifies **what to generate, in what order, with what acceptance**.

---

## 0. Pre-flight (read this once at run start)

### 0.1 What's already in the repo

```
laboraid-rate-engine/             ← repo root (cwd for the build)
├── README.md
├── .gitignore
├── kernel/                       ← Ashwani's working extraction pipeline (DO NOT REWRITE)
│   ├── canonical/                ← RateCell + r2() rounding + fields.yaml
│   ├── pipeline/                 ← ingest, ocr, extract, compute, pivot, evaluate, run
│   ├── profiles/                 ← 537, 483, 704 YAMLs (281 + 821 to be added)
│   ├── data/                     ← per-union cba/ + ratesheet/ + ai_output/
│   ├── .claude/                  ← harness (planner/builder/evaluator)
│   ├── DESIGN.md, README.md, SETUP.md
│   └── pyproject.toml, uv.lock
├── cdk/.gitkeep                  ← empty — Track A will fill
├── agents/.gitkeep               ← empty — Track C will fill
├── lambdas/.gitkeep              ← empty — Track B will fill
├── containers/.gitkeep           ← empty — Track A or C will fill (extractor container)
├── ui/.gitkeep                   ← empty — Track E will fill
├── profiles/.gitkeep             ← empty — will host workspace-level profile symlinks
├── docs/.gitkeep                 ← empty — Track F will fill (runbook + onboarding)
├── scripts/.gitkeep              ← empty — bootstrap + deploy helpers
└── BUILD_INSTRUCTIONS.md         ← this file
```

### 0.2 Hard rules (never violate)

1. **DO NOT modify `kernel/`** — it's imported via `git subtree` from `git@bitbucket.org:northbay/labor_aid_poc.git`. To update it later we'll `git subtree pull`. Mutating it directly breaks that workflow.
2. **DO NOT regenerate files that exist with content** unless an instruction says "overwrite". Placeholder `.gitkeep` files can be deleted when their directory is populated.
3. **DO NOT add new top-level directories** beyond what's listed in §0.1 without a build-item authorizing it.
4. **DO NOT bypass the tagging strategy.** Every AWS resource gets the mandatory tag set from Spec/09 §2. Enforce via CDK Aspects.
5. **DO NOT use static AWS credentials anywhere.** All access via IAM roles, Cognito federation, or AssumeRole.
6. **DO NOT add Bedrock Knowledge Base, AgentCore Memory/Gateway/Identity/Policy/Registry, or any of the 8 deferred agents** — they're explicitly v1.1+ (Spec/09 §15). POC has ONE agent: `ExtractorAgent`.
7. **DO NOT recreate functionality that exists in the kernel.** PDF reading, OCR, per-union extraction (for 537/483/704), derived-column compute, half-up rounding, canonical model, pivot to CSV — all already done. Import as a Python library; don't reimplement.

### 0.3 Style + conventions — language split

The repo has **two** languages by design — Python for everything backend (including CDK), React for the UI:

| Layer | Language | Notes |
|---|---|---|
| **CDK (IaC)** | **Python** | `aws-cdk-lib` Python package — NOT TypeScript. App entry: `cdk/app.py`. Managed with `uv`. |
| **Lambdas, agents, kernel, scripts, tests** | **Python 3.12 ARM64** | Managed with `uv`. |
| **Admin UI / frontend** | **React** + TypeScript | Vite + React 18 + TS, Tailwind, React Router, Zustand. This is the ONLY place where TS/Node belong. Lives under `ui/`. |

**Python tooling** (CDK + every backend module): `uv` package manager · `ruff` lint · `black` format · `mypy --strict` types · `pytest` tests.

**UI tooling** (`ui/` only): `pnpm` package manager · ESLint + Prettier · TypeScript compiler · Vitest.

**Naming + tagging:** Spec/09 §1 (`laboraid-{env}-{layer}-{type}-{purpose}`) + Spec/09 §2 (13 mandatory tags applied via Python CDK Aspect).

**Hard rules:**
- CDK is Python — **anyone generating `.ts` CDK is wrong.**
- UI is React — **anyone swapping in Streamlit / any Python web framework is wrong.**
- `package.json` + `node_modules/` only exist under `ui/`. Nowhere else in the repo.
- TypeScript only exists under `ui/`. Nowhere else.

### 0.4 Build resumability

- After each numbered build item completes successfully, **commit it as a single logical change** with a message of the form: `[BUILD-NN] <title>` where NN is the item number.
- If a build item fails: leave the working tree as-is, write a note to `docs/BUILD_LOG.md` describing the failure + last successful item, and exit.
- A subsequent run reads `docs/BUILD_LOG.md` to resume from the last failed/incomplete item.

---

## 1. Build queue — sequenced

Build items run in this order. Items in the same letter group (e.g., A.1, A.2) can run **in parallel within the group**; new groups start only after the prior group's items are all committed.

### Group A — CDK foundation (do first; nothing else AWS-related works without it)

CDK is **Python**, not TypeScript. Package is `aws-cdk-lib` (Python). App entry point is `cdk/app.py`. Project managed via `uv` (consistent with the rest of the repo).

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| A.1 | CDK app bootstrap | `cdk/app.py`, `cdk/cdk.json`, `cdk/pyproject.toml`, `cdk/uv.lock`, `cdk/.gitignore` | Spec/09 §0, §3, §11 | `cd cdk && uv sync && uv run cdk synth` exits 0 |
| A.2 | Mandatory tags Aspect | `cdk/laboraid_cdk/aspects/mandatory_tags.py` | Spec/09 §2 | Implements `jsii.implements(IAspect)`; importable by every stack; applies 13 mandatory tags |
| A.3 | Config (env-specific) | `cdk/laboraid_cdk/config/dev.py`, `cdk/laboraid_cdk/config/prod.py`, `cdk/laboraid_cdk/config/__init__.py` | Spec/09 §0 | Each exports a `Config` dataclass with `env`, `account`, `region`, etc. |
| A.4 | Naming helper | `cdk/laboraid_cdk/util/naming.py` | Spec/09 §1 | Pure function `name(env, layer, type, purpose) -> str` |
| A.5 | Tagged construct wrappers | `cdk/laboraid_cdk/constructs/tagged_bucket.py`, `tagged_lambda.py`, `sns_topic_with_subs.py` | Spec/09 §2 (mandatory tags) + per-layer specs in §4 | Each subclasses the L2 construct and applies mandatory tags + defaults (KMS, ARM64 for Lambdas, etc.) |
| A.6 | Strands agent custom construct | `cdk/laboraid_cdk/constructs/strands_agent.py` | Spec/09 §5 | Wraps `CfnResource` for `AWS::BedrockAgentCore::Runtime`; takes ECR URI, IAM role, env vars |

### Group B — Storage & security stacks (no compute yet)

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| B.1 | Security stack | `cdk/laboraid_cdk/stacks/security_stack.py` | Spec/09 §3 | KMS CMK + per-Lambda IAM roles + Cognito user pool with 4 groups |
| B.2 | Storage stack | `cdk/laboraid_cdk/stacks/storage_stack.py` | Spec/09 §4 L3 (§3.1-3.5) | 6 S3 buckets (inputs/processed/outputs/profiles/audit/cba-corpus), 7 DynamoDB tables (incl. agent-config), Aurora Serverless v2 cluster with schema-init custom resource |

### Group C — Processing + AI stacks (depend on B)

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| C.1 | ExtractorAgent container | `agents/extractor/Dockerfile`, `agents/extractor/agent.py`, `agents/extractor/pyproject.toml`, `agents/extractor/system-prompt.md` | Spec/09 §5 (§5.3 code snippet is the reference implementation) | `docker build` produces an ARM64 image that imports `kernel.pipeline` and runs |
| C.2 | Processing stack (Lambdas + ECR + AgentCore Runtime) | `cdk/laboraid_cdk/stacks/processing_stack.py` | Spec/09 §4 L4 + L5 | Classifier Lambda + ECR repo for ExtractorAgent + AgentCore Runtime CFN resource |
| C.3 | AI stack (Bedrock Guardrails) | `cdk/laboraid_cdk/stacks/ai_stack.py` | Spec/09 §5 (§5.6 Bedrock Guardrails) | PII Guardrail + KMS key for Bedrock |

### Group D — Validation + rendering Lambdas

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| D.1 | Validator Lambdas (4) | `lambdas/validation/checksum/`, `lambdas/validation/range/`, `lambdas/validation/confidence/`, `lambdas/validation/review-router/` | Spec/09 §4 L6 | Each: `handler.py` + `pyproject.toml` + `tests/`; reads canonical JSON, returns pass/fail + reason |
| D.2 | Renderer Lambdas | `lambdas/rendering/xlsx-renderer/`, `lambdas/rendering/csv-renderer/`, `lambdas/rendering/articles-renderer/` | Spec/09 §4 L7 | xlsx Lambda imports `kernel.pipeline.pivot` (CSV) + converts via `openpyxl`; CSV Lambda uses kernel directly; articles renderer extracts from kernel's `gaps.md` |
| D.3 | Validation stack | `cdk/laboraid_cdk/stacks/validation_stack.py` | Spec/09 §4 L6 + §6 | 3 SNS topics (failures/successes/review-needed) + EventBridge bus + DLQ pattern + SES config + Slack-notifier Lambda |

### Group E — API + UI stacks

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| E.1 | API Lambdas (admin) | `lambdas/api/{upload-presign,job-list,job-status,job-retry,job-abort,agent-list,agent-toggle,profile-list,profile-update,audit-list}/` | Spec/09 §4 L2 (§2.2) | Each: `handler.py` + `pyproject.toml` + tests; uses AWS Lambda Powertools (Python). Admin/Operations-authorized. |
| E.2 | API Lambdas (business + shared) | `lambdas/api/{ratesheet-list,ratesheet-get,ratesheet-approve,ratesheet-reject,ratesheet-unapprove,ratesheet-publish,ratesheet-audit,cell-override,cell-comment}/` | Spec/09 §4 L2 (§2.2) | Each: `handler.py` + tests. Business-authorized except `ratesheet-publish` (Admins/Operations). `ratesheet-publish` MUST return HTTP 409 if `rate_periods.approval_state != 'approved'`. |
| E.3 | API stack | `cdk/laboraid_cdk/stacks/api_stack.py` | Spec/09 §4 L2 | HTTP API Gateway + Cognito authorizer + WAF + all Lambdas from E.1/E.2 wired with per-route group claims |
| E.4 | React SPA — Admin shell | `ui/package.json`, `ui/vite.config.ts`, `ui/src/main.tsx`, `ui/src/App.tsx`, `ui/src/layouts/AdminLayout.tsx`, `ui/src/admin/{Dashboard,Uploads,Jobs,JobDetail,Agents,Profiles,Audit,Costs}.tsx`, `ui/src/components/{RouteGuard,PersonaChooser,AgentToggle}.tsx`, `ui/src/lib/{api,auth,store}.ts` | Spec/09 §4 L1 §1.4 | 8 admin pages; AgentToggle component writes to `PATCH /v1/agents/{name}` (Admins-only); 5s polling on Jobs + Agents while any job in_progress |
| E.5 | React SPA — Business shell | `ui/src/layouts/BusinessLayout.tsx`, `ui/src/business/{Inbox,RateSheetReview,ByUnion,Approved,Rejected,ReviewQueue,Me}.tsx`, `ui/src/components/{PdfViewer,ProvenancePanel,RateCellTable,CellOverrideModal,ApproveRejectBar}.tsx` | Spec/09 §4 L1 §1.5 | 7 business pages; ApproveRejectBar is disabled until review queue is empty for the rate sheet; reject requires a reason; comments per row |
| E.6 | UI hosting stack | `cdk/laboraid_cdk/stacks/ui_stack.py` | Spec/09 §4 L1 | **CDK is Python**, deploys the React SPA build artifact: private S3 bucket + CloudFront distribution + OAC + ACM cert + Route53 record + Cognito hosted UI domain. `cd ui && pnpm build` produces `ui/dist/`; CDK uploads via `BucketDeployment`. Single domain serves both `/admin/*` and `/business/*` routes. |

### Group F — Orchestration + observability

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| F.1 | Step Function state machine | `cdk/laboraid_cdk/stacks/orchestration_stack.py`, `cdk/laboraid_cdk/sfn/main_pipeline.py` | Spec/09 §4 L3 (§3.4) + §5 end-to-end flow | Standard workflow defined via CDK `aws_stepfunctions` (Python); wires Stages 1-6; retries + DLQ; S3 ObjectCreated trigger |
| F.2 | Observability stack | `cdk/laboraid_cdk/stacks/observability_stack.py` | Spec/09 §8 | 5 CloudWatch dashboards + 6 named alarms; X-Ray + CloudTrail enabled |
| F.3 | Operational docs | `docs/RUNBOOK.md`, `docs/ARCHITECTURE.md`, `docs/ONBOARDING.md` | Spec/09 §13 | Each follows the structure described in the spec |

### Group G — Missing kernel pieces (281 + 821 extractors)

These extend the kernel — done in `kernel/`, NOT in Lambda code. **Use the kernel's own `.claude/harness/` planner-builder-evaluator pattern.** The harness already encodes the read-only / never-fabricate rules and iterates against groundtruth.

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| G.1 | 281 Profile YAML | `kernel/profiles/sprinkler_fitters_281.yaml` | Spec/09 kernel-reuse matrix + `discovery/07_281_PDF_to_RateSheet_Study.md` | Matches the column list of `kernel/data/sprinkler_fitters_281/ratesheet/2026.01.01.281 Rate Sheet.csv` |
| G.2 | 281 extractor | `kernel/pipeline/extract.py` (add `extract_281` function, register in `EXTRACTORS` dict) | `discovery/07_281` for the rules + structure | `uv run python kernel/pipeline/run.py --union sprinkler_fitters_281` produces a CSV; evaluator reports ≥ 98% cell accuracy on documented cells; `gaps.md` lists everything else |
| G.3 | 821 Profile YAML | `kernel/profiles/sprinkler_fitters_821.yaml` | `discovery/04_821_PDF_to_RateSheet_Study.md` | Matches groundtruth columns |
| G.4 | 821 extractor | `kernel/pipeline/extract.py` (add `extract_821`, register) | `discovery/04_821` | Run produces CSV; eval ≥ 95% on documented cells (821 is the most complex; lower bar acceptable) |
| G.5 | Update kernel-source upstream (optional) | git subtree push back to `kernel-source/feat/cba-ratesheet-pipeline` | n/a | Only if Ashwani agrees; coordinate before pushing |

> **Note on Group G:** Use the kernel's harness for these. From within `kernel/`:
> ```
> # Activate the kernel's harness with Claude CLI
> # (the harness has planner/builder/evaluator agents already configured)
> /harness generate the ratesheet for sprinkler_fitters_281
> ```
> The harness loops build → evaluate until it passes `kernel/.claude/harness/criteria.md` thresholds. Stop after 4 iterations regardless.

### Group H — Integration + smoke test

Only after Groups A-G complete and all per-item acceptance passes.

| # | Build item | Output paths | Spec ref | Acceptance |
|---|---|---|---|---|
| H.1 | End-to-end smoke test | `tests/e2e/smoke-test.sh` + fixtures in `tests/e2e/fixtures/` | Spec/09 §5 happy-path flow | Upload a 537 Rate Notice PDF → engine produces matching xlsx in S3 + Aurora row inserted; <30s elapsed |
| H.2 | CI workflow | `.github/workflows/build-and-test.yml` | Spec/09 §13 | Runs unit tests + CDK synth + kernel evaluator on PR |
| H.3 | README update | `README.md` (overwrite) | n/a | Updated with deployment commands, links to dashboards, troubleshooting |

---

## 2. Per-track detail (build-item supplements)

For each track, the relevant spec sections + any clarifications beyond the spec.

### 2.1 Track A — CDK foundation (Python)

**Reference:** Spec/09 §3 + §11

**Key patterns:**
- Single CDK app (`cdk/app.py`) instantiates 8 stacks
- Stack dependency order: `Security → Storage → Processing → AI → Validation → API → UI → Observability`
- Use `stack.add_dependency(other_stack)` for explicit cross-stack ordering
- Each stack's `__init__` accepts a `Config` instance from `cdk/laboraid_cdk/config/{env}.py`
- Mandatory tagging Aspect applied at app level so every resource inherits via `Aspects.of(app).add(MandatoryTagsAspect(...))`

**Project layout (Python CDK):**
```
cdk/
├── app.py                                # entrypoint: instantiates 8 stacks
├── cdk.json                              # { "app": "uv run python app.py" }
├── pyproject.toml                        # deps: aws-cdk-lib, constructs, jsii
├── uv.lock
├── .gitignore
└── laboraid_cdk/                         # python package
    ├── __init__.py
    ├── aspects/
    │   ├── __init__.py
    │   └── mandatory_tags.py             # @jsii.implements(IAspect)
    ├── config/
    │   ├── __init__.py
    │   ├── dev.py
    │   └── prod.py
    ├── util/
    │   ├── __init__.py
    │   └── naming.py                     # def name(env, layer, type_, purpose) -> str
    ├── constructs/
    │   ├── __init__.py
    │   ├── tagged_bucket.py
    │   ├── tagged_lambda.py
    │   ├── sns_topic_with_subs.py
    │   └── strands_agent.py
    └── stacks/
        ├── __init__.py
        ├── security_stack.py
        ├── storage_stack.py
        ├── processing_stack.py
        ├── ai_stack.py
        ├── validation_stack.py
        ├── api_stack.py
        ├── ui_stack.py
        ├── orchestration_stack.py
        └── observability_stack.py
```

**Code-gen prompt template (CDK Python):**
> Generate an AWS CDK v2 **Python** stack class named `{StackName}` in `cdk/laboraid_cdk/stacks/{module}.py`. The stack creates the resources listed in Spec/09 §4 Layer {N} §{sub}. Use the tagged-construct wrappers from `cdk.laboraid_cdk.constructs` (`TaggedBucket`, `TaggedLambda`, `SnsTopicWithSubs`). All resource names use the `name()` helper from `cdk.laboraid_cdk.util.naming`. Apply the `MandatoryTagsAspect` if not already done at app level. Export ARNs/IDs other stacks need via `CfnOutput`. Include a module-level docstring citing the spec section. Each major resource gets a comment explaining purpose. Type annotations everywhere; `mypy --strict` should pass.

**Example (CDK Python skeleton):**
```python
# cdk/laboraid_cdk/stacks/storage_stack.py
"""L3 Storage stack — buckets, DDB tables, Aurora.

Implements Spec/09 §4 Layer 3 (§3.1-3.5).
"""
from aws_cdk import Stack, RemovalPolicy, Duration
from aws_cdk import aws_s3 as s3, aws_dynamodb as ddb, aws_rds as rds, aws_kms as kms
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.constructs.tagged_bucket import TaggedBucket
from laboraid_cdk.util.naming import name


class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, config: Config,
                 master_key: kms.IKey, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.inputs_bucket = TaggedBucket(
            self, "InputsBucket",
            bucket_name=name(config.env, "l3", "bucket", "inputs"),
            encryption_key=master_key,
            object_lock_enabled=(config.env == "prod"),
        )
        # ... rest per spec
```

### 2.2 Track B — Lambda code (Python)

**Reference:** Spec/09 §4 L2 + L4 + L6 + L7

**Key patterns:**
- Each Lambda lives in its own directory under `lambdas/{layer}/{purpose}/`
- Directory contents: `handler.py`, `requirements.txt`, `__init__.py`, `tests/test_handler.py`
- Use AWS Lambda Powertools for Python: `Logger`, `Tracer`, `Metrics`
- Pydantic models for input/output validation
- Handlers ARE the API contract; CDK references them by path

**Standard handler skeleton:**
```python
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.utilities.parser import event_parser, BaseModel

logger = Logger()
tracer = Tracer()
metrics = Metrics()

class RequestModel(BaseModel):
    # ... per-Lambda fields
    pass

class ResponseModel(BaseModel):
    # ... per-Lambda fields
    pass

@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
@event_parser(model=RequestModel)
def handler(event: RequestModel, context) -> dict:
    # ... per-Lambda logic
    return ResponseModel(...).dict()
```

**For Lambdas that wrap the kernel:**
Install the kernel as an editable dependency:
```
# requirements.txt
-e ../../../kernel
pdfplumber
rapidocr-onnxruntime
# ...
```

Or package the kernel into a Lambda Layer (preferred for cold-start performance).

### 2.3 Track C — Strands ExtractorAgent

**Reference:** Spec/09 §4 L5 (§5.3 has the full Python skeleton)

**Container:**
```Dockerfile
FROM public.ecr.aws/lambda/python:3.12-arm64
# Install kernel + Strands SDK
COPY kernel/ /opt/kernel/
COPY agents/extractor/ /opt/agent/
RUN cd /opt/kernel && uv sync && cd /opt/agent && pip install -r requirements.txt
ENV PYTHONPATH=/opt/kernel:/opt/agent
CMD ["agent.py"]   # or AgentCore Runtime expected entrypoint
```

**System prompt** (`agents/extractor/system-prompt.md`):
- Use the EXTRACTOR_SYSTEM_PROMPT content described in Spec/09 §5.3
- Make the prompt explicit about: tools available, when to escalate to Bedrock fallback, how to use steering's checksum gate

**Steering policy** (`agents/extractor/steering.py`):
- Implements `ExtractorSteering(SteeringHandler)` per Spec/09 §5.3
- Blocks `return_extraction_complete` if checksum not validated
- Forces `escalate_to_claude_multimodal` when kernel reports unresolved gaps

**Deploy:**
The agent deploys via either:
- AgentCore CLI: `cd agents/extractor && agentcore deploy` (fast iteration)
- CDK CFN resource (`Strands_Agent_Runtime` custom construct from A.6)

For POC, use AgentCore CLI for the agent, CDK for everything around it.

### 2.4 Track D-bis — Missing extractors (kernel-side)

**Reference:** `discovery/04_821_PDF_to_RateSheet_Study.md` + `discovery/07_281_PDF_to_RateSheet_Study.md`

**Use the kernel's `.claude/harness/` pattern.** From `kernel/.claude/agents/builder.md`:

> *"You are a data-extraction engineer. You build a pipeline that reads a union's CBA documents and produces a CSV ratesheet that reproduces the human-made groundtruth, and you improve it in response to evaluation feedback. … Reuse the proven 483 kernel as reference. Hard rules: read-only on cba/, never fabricate."*

Run with the kernel's CLI harness:
```bash
cd kernel
# Activate Claude Code planner/builder/evaluator loop
# See kernel/.claude/commands/harness.md for invocation
```

Iterate until `kernel/.claude/harness/evaluation-log.md` records the new union at ≥ threshold accuracy (98% for documented cells; gaps reported in `data/<union>/ai_output/<union>.gaps.md`).

### 2.5 Track E — React admin SPA

**Reference:** Spec/09 §4 L1

**Stack (this is the ONLY non-Python area of the repo):**
- **Vite + React 18 + TypeScript** (`ui/` directory; this is where TS lives)
- **Auth:** Cognito via `aws-amplify/auth` (hosted UI flow → JWT in `localStorage`)
- **Routing:** React Router v6
- **State:** Zustand (POC-simple)
- **Styling:** Tailwind CSS
- **PDF rendering:** `react-pdf` (`pdfjs-dist` worker)
- **Data tables:** TanStack Table
- **Tooling:** ESLint + Prettier + TypeScript compiler + Vitest
- **Package manager:** `pnpm` (single `ui/pnpm-lock.yaml` committed)

**Two-persona model:** the SPA is ONE Vite/React build serving two shells under `/admin/*` and `/business/*`. Login route guards decide which shell to mount based on Cognito group. See Spec/09 §4 L1 §1.1 for the persona table and §1.4/§1.5 for full per-page feature lists.

**Project layout:**
```
ui/
├── package.json
├── pnpm-lock.yaml
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.ts
├── .eslintrc.cjs
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx                       # Router shell + Amplify config + auth gate + persona chooser
│   ├── routes.tsx                    # group-gated route definitions (delegates to admin/ + business/)
│   ├── layouts/
│   │   ├── AdminLayout.tsx           # Sidebar: Dashboard / Jobs / Agents / Profiles / Uploads / Audit / Costs
│   │   └── BusinessLayout.tsx        # Sidebar: Inbox / By Union / Approved / Rejected / Review Queue / My
│   ├── admin/
│   │   ├── Dashboard.tsx             # /admin/dashboard — 6-pillars snapshot, alarms
│   │   ├── Uploads.tsx               # /admin/uploads — presigned URL flow
│   │   ├── Jobs.tsx                  # /admin/jobs — list + bulk retry/abort
│   │   ├── JobDetail.tsx             # /admin/jobs/:id — per-stage logs, retry
│   │   ├── Agents.tsx                # /admin/agents — registry + enable/disable
│   │   ├── Profiles.tsx              # /admin/profiles — read-only list + diff
│   │   ├── Audit.tsx                 # /admin/audit — searchable audit_log
│   │   └── Costs.tsx                 # /admin/costs — Bedrock + S3 + Lambda spend
│   ├── business/
│   │   ├── Inbox.tsx                 # /business/inbox — pending_review rate sheets
│   │   ├── RateSheetReview.tsx       # /business/rate-sheets/:union/:period — 3-panel review, Approve/Reject
│   │   ├── ByUnion.tsx               # /business/by-union/:union — per-union history
│   │   ├── Approved.tsx              # /business/approved — approval history
│   │   ├── Rejected.tsx              # /business/rejected — rejection history with reasons
│   │   ├── ReviewQueue.tsx           # /business/queue — low-confidence cells
│   │   └── Me.tsx                    # /business/me — my recent activity
│   ├── components/
│   │   ├── PdfViewer.tsx
│   │   ├── ProvenancePanel.tsx
│   │   ├── RateCellTable.tsx
│   │   ├── CellOverrideModal.tsx
│   │   ├── ApproveRejectBar.tsx      # business-only top bar with Approve/Reject + reason field
│   │   ├── AgentToggle.tsx           # admin-only enable/disable agent control
│   │   ├── RouteGuard.tsx            # Cognito group check; redirects to allowed landing page
│   │   └── PersonaChooser.tsx        # for users in both Admins and Business
│   ├── lib/
│   │   ├── api.ts                    # fetch wrapper, injects Cognito JWT
│   │   ├── auth.ts                   # Amplify Auth wrappers, group lookup
│   │   └── store.ts                  # Zustand stores (per-persona slices)
│   └── types/
│       └── api.ts                    # types from OpenAPI / hand-written
└── public/
    └── favicon.svg
```

**Admin pages required** (Spec/09 §1.4):
1. `/admin/dashboard` — landing for Admins/Operations; 6-pillars snapshot + alarm banner
2. `/admin/uploads` — drag-drop PDF upload (presigned URL via `POST /v1/uploads`)
3. `/admin/jobs` — list + filters + bulk retry/abort (`GET /v1/jobs`, `POST /v1/jobs/:id/retry`, `POST /v1/jobs/:id/abort`)
4. `/admin/jobs/:id` — per-execution timeline, per-stage logs, CloudWatch deep-links, retry
5. `/admin/agents` — agent registry + enable/disable (`GET /v1/agents`, `PATCH /v1/agents/{name}`); enable-toggle is **Admins-only**
6. `/admin/profiles` — read-only list of Profile YAMLs + version diff (edit deferred per Spec/09 §15.6)
7. `/admin/audit` — searchable `audit_log` view (`GET /v1/audit`)
8. `/admin/costs` — Bedrock + S3 + Lambda spend rollups (Admins-only)

**Business pages required** (Spec/09 §1.5):
1. `/business/inbox` — landing for Business; `approval_state='pending_review'` rate sheets
2. `/business/rate-sheets/:union/:period` — three-panel review (PDF + table + provenance), cell override modal, comment per row, **Approve / Reject** buttons in top bar
3. `/business/by-union/:union` — all rate sheets for one union with status badges
4. `/business/approved` — approval history (who, when, published-yes/no)
5. `/business/rejected` — rejection history with reasons
6. `/business/queue` — low-confidence cells review queue (Approve button blocked until empty)
7. `/business/me` — current user's recent approvals/rejections/overrides/comments

**Cognito groups → route gates** (enforced in `<RouteGuard>`):
- `Admins` → all `/admin/*` (and `/business/*` only if also in `Business` group)
- `Operations` → `/admin/*` except agent enable/disable + costs
- `Business` → all `/business/*`; **denied** on `/admin/*` (403)
- `ServiceClients` → API only (no UI access)
- Users in BOTH `Admins` and `Business` see `<PersonaChooser>` at `/` and can switch personas via top-bar dropdown

**State management:**
- Zustand store for: current user + groups, active job polling, draft override form
- API client (`lib/api.ts`) wraps `fetch` and injects `Authorization: Bearer <Cognito JWT>` from `Amplify.Auth.fetchAuthSession()`
- Polling: `useEffect` + `setInterval(5000)` for any job in `in_progress` state
- Optimistic UI for override actions; reconcile on response

**Build → deploy:**
```bash
cd ui
pnpm install
pnpm typecheck   # tsc --noEmit
pnpm lint        # eslint
pnpm test        # vitest
pnpm build       # produces ui/dist/
```
The Python CDK `UiStack` (`cdk/laboraid_cdk/stacks/ui_stack.py`) picks up `ui/dist/` via `aws_s3_deployment.BucketDeployment`, hosts it from a private S3 bucket fronted by CloudFront + OAC, with ACM cert + Route53 record. **The deploy stack is Python; only the SPA source under `ui/` is TS/React.**

### 2.6 Cross-cutting requirements (apply to every track)

| Requirement | How to satisfy |
|---|---|
| Mandatory tags | Apply `MandatoryTagsAspect` at CDK app level |
| Naming convention | Use `naming.name()` Python helper exclusively; no hardcoded names |
| IAM least-privilege | One execution role per Lambda; only the specific S3 keys + DDB items + Bedrock model ARNs needed |
| KMS encryption | All S3 + DDB + Aurora + Secrets use the project CMK |
| Structured logging | AWS Lambda Powertools for Python (all Lambdas are Python — no Node Lambdas in this repo); JSON output to CloudWatch |
| Tracing | X-Ray enabled on every Lambda + Step Functions |
| TLS-only buckets | Bucket policy denies non-TLS access |
| No `pip install --user` | Always use `uv` for Python deps |
| Dependency lockfiles | `uv.lock` committed at every Python project root (`cdk/`, `lambdas/<name>/`, `agents/<name>/`, `kernel/`); `ui/pnpm-lock.yaml` committed for the React SPA |
| Error handling | Try/except at handler boundary; structured error to CloudWatch; DLQ for async; SNS for cross-layer notification |

---

## 3. Out-of-scope (do NOT generate)

Per Spec/09 §15:

- ❌ 8 of 9 agents in `docs/07_Strands_AgentCore_Agentic_Design.md` (only `ExtractorAgent` is in POC)
- ❌ AgentCore Memory / Gateway / Identity / Policy / Registry / Evaluations (only Runtime + Observability)
- ❌ Bedrock Knowledge Base + S3 Vectors
- ❌ Year-over-year delta validation + Article-20 awareness
- ❌ AI sanity review of validation outliers
- ❌ Per-cell provenance UI drilldown beyond what kernel provides
- ❌ 6-source provenance taxonomy (kernel's 3-source is sufficient for POC)
- ❌ "Ask the CBA" Q&A
- ❌ Profile editor UI (POC: edit YAML in repo directly)
- ❌ Semantic memory of override patterns
- ❌ Multi-tenant separation
- ❌ Cross-region DR
- ❌ Cadence reminders / bulk backfill / scheduling
- ❌ Docling / Textract / Tesseract (kernel handles document processing via pdfplumber + rapidocr — no additional OCR services needed)
- ❌ Custom RAG infrastructure

If an instruction below seems to call for one of these, **skip it** and add a note to `docs/BUILD_LOG.md`.

---

## 4. Acceptance criteria (final gate)

After all build items complete:

### 4.1 Repo-level checks

**Python side** (CDK + all backend):
- [ ] `cd cdk && uv run cdk synth` succeeds across all 8 stacks
- [ ] `uv run ruff check .` clean across `cdk/`, `lambdas/`, `agents/`, `kernel/`
- [ ] `uv run black --check .` clean across the same dirs
- [ ] `uv run mypy --strict cdk/laboraid_cdk lambdas agents` exits 0
- [ ] `uv run pytest` clean across `lambdas/`, `agents/`, `cdk/`, `kernel/`
- [ ] `kernel/pipeline/run.py --all` still reproduces measured accuracy: 704 ≥ 99.0%, 483 Building = 100%, 537 ≥ 67%

**UI side** (React SPA only):
- [ ] `cd ui && pnpm install --frozen-lockfile` exits 0
- [ ] `cd ui && pnpm typecheck` (i.e., `tsc --noEmit`) exits 0
- [ ] `cd ui && pnpm lint` exits 0
- [ ] `cd ui && pnpm test --run` exits 0
- [ ] `cd ui && pnpm build` produces `ui/dist/index.html`

**Boundary checks** (enforces the language split):
- [ ] No TypeScript outside `ui/`: `find . -name '*.ts' -not -path './ui/*' -not -path '*/node_modules/*' -not -path './.git/*'` returns 0 results
- [ ] No `package.json` outside `ui/`: `find . -name 'package.json' -not -path './ui/*' -not -path '*/node_modules/*' -not -path './.git/*'` returns 0 results
- [ ] No `node_modules/` outside `ui/`: `find . -type d -name 'node_modules' -not -path './ui/*' -not -path './.git/*'` returns 0 results

**Process:**
- [ ] Git history is clean — every numbered item has a `[BUILD-NN]` commit
- [ ] `docs/BUILD_LOG.md` shows all items succeeded (or only deferred items skipped)

### 4.2 Functional smoke (H.1)
1. Upload `kernel/data/sprinkler_fitters_704/cba/2026.01.01.704 Rate Notice.pdf` via the admin UI
2. Engine classifies → invokes `ExtractorAgent` on AgentCore Runtime
3. Agent calls `kernel.pipeline.extract.extract_704` as a Strands tool
4. Validator passes (checksum matches printed Total Package; range checks OK)
5. Renderer writes xlsx + CSV to `s3://laboraid-prod-l3-bucket-outputs/laboraid/Sprinkler/704/2026-01-01/`
6. Aurora has a `rate_periods` row + 13 `rate_cells` rows
7. SNS publishes `laboraid.rate-sheet.published`
8. Admin UI shows the rate sheet with provenance side panel
9. Total elapsed time < 30 seconds

### 4.3 Spec match
- [ ] All 7 layers (L1-L7) have at least one CDK resource named per Spec/09 §1 convention
- [ ] All 13 mandatory tags appear on every resource (sampled via `aws resourcegroupstaggingapi get-resources`)
- [ ] AWS Well-Architected 6 pillars: every layer in §4 of this doc maps to its pillar coverage in Spec/09 §9
- [ ] SNS topics for `failures` / `successes` / `review-needed` exist and have subscribers (email + Slack-notifier Lambda)

### 4.4 SOW contract match
- [ ] Strands Agent framework: `ExtractorAgent` is a Strands `Agent` with `@tool` + `SteeringHandler` ✅
- [ ] AWS AgentCore: agent deployed on AgentCore Runtime ✅
- [ ] AWS Bedrock: Claude Sonnet 4.x + Haiku invoked via `bedrock.invoke_model` ✅
- [ ] React SPA delivered (Vite + React 18 + TS, hosted on S3+CloudFront+OAC via Python CDK `UiStack`) ✅
- [ ] **Two-persona UI:** Admin shell at `/admin/*` (ops/jobs/agents/profiles/audit/costs — Spec/09 §4 L1 §1.4) and Business shell at `/business/*` (inbox/review/approve/reject/by-union — Spec/09 §4 L1 §1.5) ✅
- [ ] **Business approval workflow:** approve/reject/unapprove endpoints (Spec/09 §4 L2 §2.2); Aurora `rate_periods.approval_state` defaults to `pending_review`; publish endpoint returns 409 unless `approval_state='approved'` ✅
- [ ] **Admin agent control:** `agent-config` DDB table (Spec/09 §4 L3 §3.2) + `PATCH /v1/agents/{name}` enable-toggle; Step Functions reads `enabled` before invoking ✅
- [ ] S3 (shared and tenant-specific) ✅
- [ ] Document-Agnostic Processing (kernel's pdfplumber + rapidocr hybrid path) ✅
- [ ] LLM-Centric Extraction (agent uses Claude as Bedrock fallback) ✅
- [ ] Validation Layer (checksum + range + confidence Lambdas) ✅
- [ ] Human-in-the-Loop (review queue + Business approve/reject + cell override + comments) ✅
- [ ] Separation of Concerns (raw S3 / pipeline / Aurora / Calculator) ✅

---

## 5. Code-gen prompts (reusable templates)

When the code-gen library needs a template-specific prompt, use these:

### 5.1 CDK stack prompt (Python)
> Generate an AWS CDK v2 **Python** stack class in `cdk/laboraid_cdk/stacks/{module}.py`. The stack creates the resources listed in Spec/09 §{section}. Use the tagged-construct wrappers from `cdk.laboraid_cdk.constructs` (`TaggedBucket`, `TaggedLambda`, `SnsTopicWithSubs`). All resource names use the `name()` helper from `cdk.laboraid_cdk.util.naming`. Constructor takes a `Config` instance + any required cross-stack refs (e.g., `master_key: kms.IKey`). Export ARNs/IDs other stacks need via `CfnOutput`. Include a module-level docstring citing the spec section. Comment major resources. Type annotations everywhere; `mypy --strict` must pass.

### 5.2 Lambda handler prompt
> Generate a Python 3.12 Lambda handler at `{path}/handler.py` plus `requirements.txt` and `tests/test_handler.py`. The handler implements: {one-line description from Spec/09 §{section}}. Use AWS Lambda Powertools (Logger, Tracer, Metrics). Use Pydantic for input/output models. Wrap all logic in try/except at the handler boundary. For any Bedrock call, apply the PII Guardrail by ID from env var `BEDROCK_GUARDRAIL_ID`. Include unit tests with at least 3 cases: happy path, validation failure, downstream error.

### 5.3 Strands agent prompt
> Generate a Strands agent in `agents/{name}/`. The agent's role is: {one-line}. Tools (each is a Python function decorated with `@tool`): {list of tool functions referencing kernel module paths}. SteeringHandler enforces: {list of conditions}. System prompt content lives in `system-prompt.md`. The Dockerfile uses `public.ecr.aws/lambda/python:3.12-arm64` as base and installs the kernel via `uv pip install -e /opt/kernel`. The container's CMD invokes the agent via AgentCore Runtime conventions. Include OpenTelemetry trace attributes.

### 5.4 React page prompt
> Generate a React + TypeScript page component at `ui/src/pages/{Name}.tsx`. The page's purpose is: {one-line}. Use Tailwind CSS for styling, React Router v6 for navigation, Zustand for state, and the API client from `ui/src/lib/api.ts` (it already injects the Cognito JWT). Wrap the page in `<RouteGuard groups={[...]}>` restricting Cognito group access to: {list from §2.5}. Include loading + error states and an empty-state placeholder. If the page polls the API, poll every 5 seconds while any job is `in_progress` (`useEffect` + `setInterval`). Strict TypeScript — no `any`. Use the types from `ui/src/types/api.ts`. **CDK around this is Python; the SPA itself is the only TS in the repo.**

### 5.5 Profile YAML prompt
> Generate `kernel/profiles/{union}.yaml` matching the structure of `kernel/profiles/sprinkler_fitters_704.yaml`. The profile defines the union's output ratesheet schema. Column list MUST match the header of `kernel/data/{union}/ratesheet/{groundtruth-file}` exactly (same names, same order). Use `multiplier_of` + `factor` for derived columns. Use the canonical field dictionary in `kernel/canonical/fields.yaml` to map column names to canonical fields. Reference: `../../discovery/0X_{union}_PDF_to_RateSheet_Study.md` for the union's specific rules.

### 5.6 Extractor (Python) prompt
> Add a function `extract_{union_local}(union_dir)` to `kernel/pipeline/extract.py`. The function reads PDFs from `{union_dir}/cba/`, extracts wage + fringe values into canonical `ClassificationRow` objects, and returns `(rows, gaps)`. Pattern your implementation after `extract_704` or `extract_483`. Hard rules from the kernel's existing code: read-only on `{union_dir}/cba/`; never fabricate — values not in the PDFs go into `gaps` with `(zone, package, column, reason)`. Use the kernel's `r2()` for rounding and `RateCell` for cell construction with `source_doc` + `source_locator`. Register the function in the `EXTRACTORS` dict at the bottom of the file. After generation, run `uv run python kernel/pipeline/run.py --union sprinkler_fitters_{local}` and iterate against the evaluator until cell accuracy on documented cells is ≥ {threshold}%.

---

## 6. Build invariants (continuous verification)

After every item commit, the build runner verifies:

1. **`git status` clean** — no untracked files outside the item's expected outputs
2. **Repo doesn't grow in places it shouldn't** — `kernel/` byte count unchanged unless item is in Group G
3. **Naming convention adherence** — `grep -rE 'laboraid-(dev|prod)-l[0-9]-' cdk/` returns >0 matches but `grep -E 'laboraid-[A-Z]'` returns 0 (no PascalCase)
4. **Mandatory tags present** — `grep -c MandatoryTagsAspect cdk/laboraid_cdk/stacks/*.py` equals the stack count
5. **No secrets** — `grep -rE 'AKIA|aws_secret|password=' --exclude-dir=node_modules --exclude-dir=.git` returns 0
6. **Language-split boundary holds** — no `.ts`/`.tsx` outside `ui/`; no `package.json` outside `ui/`; no `.py` CDK escaping to `.ts` CDK

If any invariant fails, the build runner halts and records in `docs/BUILD_LOG.md`.

---

## 7. Final commit + handoff

After Group H completes successfully:

1. Commit final state with message: `[BUILD-FINAL] complete POC build — see docs/BUILD_LOG.md`
2. Open a PR `feat/aws-strands-integration` → `main` (via `gh pr create` if authenticated, or write the PR description to `docs/PR_DESCRIPTION.md` for manual creation)
3. PR description should include:
   - Summary of what was built (group-by-group)
   - Total commits made
   - Smoke test results
   - Known gaps (per `kernel/data/*/ai_output/*.gaps.md`)
   - Next steps for UAT
4. Push to remote: `git push origin feat/aws-strands-integration`

---

## 8. References

- **Architecture rationale:** `docs/09_Technical_Implementation_Spec.md` (Spec/09)
- **Agentic design:** `docs/07_Strands_AgentCore_Agentic_Design.md`
- **Per-cell provenance:** `docs/05_Provenance_and_Citations.md`
- **Ground truth / extraction philosophy:** `docs/08_Ground_Truth_and_LLM_Loop.md`
- **All design docs:** `docs/00_README.md` through `docs/09_Technical_Implementation_Spec.md`
- **Discovery findings per union** (still in parent project, not yet mirrored):
  - 537: `../discovery/01_537_PDF_to_RateSheet_Study.md`
  - 704: `../discovery/03_704_PDF_to_RateSheet_Study.md`
  - 821: `../discovery/04_821_PDF_to_RateSheet_Study.md`
  - 483: `../discovery/06_483_PDF_to_RateSheet_Study.md`
  - 281: `../discovery/07_281_PDF_to_RateSheet_Study.md`
- **Consolidated findings for client:** `../discovery/11_Findings_for_Client.md`
- **SOW:** `../LaborAid - POC SOW.docx.pdf`
- **SOW review:** `../SOW_Review.md`
- **Kernel attribution:** `../Ashwani_Repo_Assessment.md`

The `../` paths reference the parent project `E:\NBS_LaborAid\` (discovery + SOW are not in this repo). All design docs now live in `docs/` inside this repo.

---

## TL;DR for the code-gen runner

1. Read this file end-to-end.
2. Execute Groups A through H in order. Items within a group can parallelize.
3. After each item, commit with message `[BUILD-NN] <title>`.
4. Use `docs/09_Technical_Implementation_Spec.md` for design rationale when a spec reference is given.
5. Never modify `kernel/`. Never bypass tags/naming. Never use static creds.
6. Stop and log to `docs/BUILD_LOG.md` if anything fails.
7. Final acceptance gate is §4 of this file.

Go.
