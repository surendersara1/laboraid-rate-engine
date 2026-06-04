# End-to-End Architecture Flow

This document is the visual companion to [`Learning_Lessons.md`](Learning_Lessons.md). Each lesson maps to one or more blocks in the master flow below; the per-lesson diagrams zoom into each block. Read top-down: master picture first, then drill into each lesson's slice.

All diagrams are Mermaid — they render inline on GitHub.

---

## §0 — The whole system in one diagram

```mermaid
flowchart TB
    PDF["📄 PDF<br/>Customer uploads a CBA or Rate Notice"]:::input
    Admin["👨‍💼 Admin / Operations<br/>(NBS + LaborAid ops)"]:::adminActor
    Business["👩‍💼 Business / SME<br/>(LaborAid + Union rep)"]:::businessActor
    Calc["📊 LaborAid Calculator<br/>(downstream)"]:::output

    subgraph UI["UI Layer — Lesson 7"]
        AdminUI["/admin/* shell<br/>8 pages, Cognito gated"]:::adminBlock
        BusinessUI["/business/* shell<br/>7 pages, Cognito gated"]:::businessBlock
    end

    subgraph Engine["Engine Layer — Lessons 1 + 2"]
        Kernel["Kernel (deterministic)<br/>PDF → canonical → CSV<br/>~99.6% on known unions"]:::engineBlock
        Agent["Strands ExtractorAgent on AgentCore<br/>6 @tools wrapping kernel<br/>+ Bedrock fallback"]:::engineBlock
    end

    subgraph Orchestration["Orchestration Layer — Lesson 3"]
        SFN["Step Functions main pipeline<br/>Classify → Gate → Extract → Validate → Render"]:::orchBlock
    end

    subgraph Storage["Storage Layer — Lesson 6"]
        S3["6 S3 buckets<br/>(inputs / processed / outputs / etc.)"]:::storageBlock
        DDB["7 DynamoDB tables<br/>(files / jobs / review / agent-config / etc.)"]:::storageBlock
        Aurora["Aurora Postgres<br/>rate_periods + approval_state"]:::storageBlock
    end

    subgraph Approval["Approval Gate — Lesson 4"]
        Approve["ratesheet-approve<br/>(Business)"]:::businessBlock
        Reject["ratesheet-reject<br/>(Business)"]:::businessBlock
        Publish["ratesheet-publish<br/>(Admin/Ops) → 409 unless approved"]:::adminBlock
    end

    CDK["CDK Foundation — Lesson 5<br/>(Config + naming + tags Aspect + tagged constructs)"]:::foundationBlock

    %% --- The actual data flow ---
    Admin -- "1. Upload PDF" --> AdminUI
    AdminUI -- "presigned URL" --> S3
    PDF -. lands in .-> S3
    S3 -- "2. ObjectCreated event" --> SFN
    SFN -- "3a. Classify" --> SFN
    SFN -- "3b. Read agent-config" --> DDB
    SFN -- "3c. Extract (if enabled)" --> Agent
    Agent -- "calls @tool" --> Kernel
    Agent -- "Bedrock fallback (low confidence)" --> Engine
    Agent -- "results" --> SFN
    SFN -- "4. Validate (parallel)" --> SFN
    SFN -- "5. Render → S3" --> S3
    SFN -- "6. Insert with state=pending_review" --> Aurora

    Aurora -- "shows in Inbox" --> BusinessUI
    Business -- "Open / Review" --> BusinessUI
    BusinessUI -- "Approve" --> Approve
    BusinessUI -- "Reject + reason" --> Reject
    Approve -- "UPDATE state=approved" --> Aurora
    Reject -- "UPDATE state=rejected" --> Aurora

    Admin -- "click Publish (after approval)" --> AdminUI
    AdminUI -- "POST /publish" --> Publish
    Publish -- "read approval_state" --> Aurora
    Publish -- "if approved → UPDATE state=published" --> Aurora

    Aurora -- "GET rate-sheet (when published)" --> Calc

    CDK -. "deploys all of the above as<br/>9 CDK stacks via cdk deploy" .-> Orchestration
    CDK -. .-> Storage
    CDK -. .-> Approval
    CDK -. .-> Engine
    CDK -. .-> UI

    classDef input         fill:#f0f9ff,stroke:#0369a1,stroke-width:2px,color:#0c4a6e
    classDef output        fill:#f0fdf4,stroke:#16a34a,stroke-width:2px,color:#14532d
    classDef adminActor    fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f
    classDef businessActor fill:#fce7f3,stroke:#db2777,stroke-width:2px,color:#831843
    classDef adminBlock    fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef businessBlock fill:#fce7f3,stroke:#db2777,color:#831843
    classDef engineBlock   fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef orchBlock     fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef storageBlock  fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef foundationBlock fill:#f3f4f6,stroke:#525252,color:#262626,stroke-dasharray: 4 4
```

**Read the diagram with the lesson lens:**

| Block | Lesson | What it teaches |
|---|---|---|
| Engine Layer (Kernel + Agent) | 1 + 2 | PDF → canonical → CSV; agent wraps kernel as @tools |
| Orchestration Layer (Step Functions) | 3 | Who triggers what; the 6 stages; the agent enable/disable gate |
| Approval Gate (Lambdas + Aurora state) | 4 | publish 409 guard + Business approve/reject + audit trail |
| CDK Foundation (cross-cutting) | 5 | Patterns every stack reuses |
| Storage Layer | 6 | One concrete stack showing every pattern |
| UI Layer (two personas) | 7 | Where humans interact; how button clicks map to Lambdas |

---

## §1 — Lesson 1 zoom-in: The Canonical Layer

**Where it fits in the master diagram:** inside the "Engine Layer" block. The canonical layer is the *in-memory shape* the kernel and agent both operate on.

```mermaid
flowchart LR
    PDF["📄 Union PDF<br/>(rate notice / CBA)"]:::input
    UnionA["Union-specific labels<br/>'Wage', 'H&W', 'J&A Training 483'"]:::layer1
    Canon["Canonical names<br/>'wage', 'health_welfare',<br/>'apprenticeship_training'<br/><br/>(fields.yaml)"]:::layer2
    CSV["Customer's existing<br/>rate-sheet CSV/xlsx"]:::output

    PDF --> Extractor["extract_704()<br/>(per-union extractor)"]:::engine
    Extractor --> RC

    subgraph CanonicalLayer["canonical/ (in-memory shapes)"]
        direction TB
        RC["RateCell<br/>━━━━━━━━<br/>zone: 'Building'<br/>classification: 'Journeyman'<br/>canonical_field: 'wage'<br/>value: 54.70<br/>source_doc: 'Rate Notice.pdf'<br/>source_locator: 'page 2 row 3'<br/>confidence: 0.95"]:::canonical
        CR["ClassificationRow<br/>━━━━━━━━<br/>cells: {<br/> wage: RateCell,<br/> health_welfare: RateCell,<br/> pension: RateCell,<br/> ...<br/>}"]:::canonical
        R2["r2() — half-up rounding<br/>(83.505 → 83.51, NOT 83.50)"]:::canonical
        RC --> CR
    end

    UnionA -. "appears in PDF as" .-> PDF
    Canon -. "kernel uses internally" .-> CanonicalLayer
    CSV -. "header dictates" .-> Profile

    Profile["profiles/704.yaml<br/>━━━━━━━━<br/>columns: ['Wage', 'Wage 1.5x', ...]<br/>multiplier_of + factor → derived"]:::profile

    CR --> Compute["compute.py<br/>derived columns<br/>(wage × 1.5, P&G × 1.10/1.15/1.25)"]:::engine
    Compute --> Pivot["pivot.py<br/>canonical → wide CSV<br/>(matches groundtruth header)"]:::engine
    Pivot --> Output["📄 Output CSV<br/>data/&lt;union&gt;/ai_output/...csv"]:::output

    Profile --> Compute
    Profile --> Pivot
    R2 -. "used by" .-> Compute

    classDef input fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef output fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef layer1 fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef layer2 fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef canonical fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef engine fill:#fef9c3,stroke:#a16207,color:#713f12
    classDef profile fill:#fae8ff,stroke:#a21caf,color:#581c87
```

**Key takeaway:** three vocabularies (PDF native → canonical → output CSV) translated by the per-union profile YAML. RateCell carries provenance so every output value is auditable.

---

## §2 — Lesson 2 zoom-in: The Strands Agent

**Where it fits in the master diagram:** inside the "Engine Layer" block, on top of the kernel. The agent is what makes the kernel callable from AWS.

```mermaid
flowchart TB
    Input["Step Functions payload<br/>{ union, s3_prefix, job_id }"]:::input

    subgraph AgentCore["AgentCore Runtime container"]
        direction TB
        Entry["app.py BedrockAgentCoreApp<br/>@entrypoint def invoke(payload)"]:::entry
        Brain["LLM brain<br/>(reads system-prompt.md)<br/>━━━━━━━━━━━━━━━━<br/>RFC-2119 7-step procedure"]:::brain
        Steering["ExtractorSteering<br/>━━━━━━━━━━━━<br/>blocks completion unless<br/>checksum_validated AND<br/>gaps escalated"]:::steering

        subgraph Tools["6 @tool functions"]
            direction TB
            T1["@tool stage_inputs_from_s3<br/>(downloads PDFs)"]:::tool
            T2["@tool run_kernel_extractor<br/>calls k_extract.EXTRACTORS[union]()"]:::tool
            T3["@tool compute_derived_columns<br/>calls k_compute.resolve_row()"]:::tool
            T4["@tool pivot_to_ratesheet_csv<br/>calls k_pivot.write_csv()"]:::tool
            T5["@tool validate_total_package_checksum"]:::tool
            T6["@tool escalate_to_claude_multimodal<br/>(Bedrock Sonnet 4.6 + PDF)"]:::fallback
        end

        Entry --> Brain
        Brain <--> Steering
        Brain --> Tools
    end

    subgraph Kernel["Kernel (Lesson 1)"]
        direction TB
        K1["pipeline/extract.py"]:::kernel
        K2["pipeline/compute.py"]:::kernel
        K3["pipeline/pivot.py"]:::kernel
        K4["canonical/model.py — r2() etc."]:::kernel
    end

    S3In["S3 inputs bucket"]:::storage
    S3Out["S3 outputs bucket"]:::storage
    Bedrock["AWS Bedrock<br/>Claude Sonnet 4.6 + Haiku 4.5<br/>+ PII Guardrail"]:::external

    Input --> Entry
    T1 --> S3In
    T2 --> K1
    T3 --> K2
    T4 --> K3
    T4 --> S3Out
    T5 --> K4
    T6 --> Bedrock

    AgentCore -- "returns result" --> Output["Step Functions resumes"]:::output

    classDef input fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef output fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef entry fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef brain fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef steering fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef tool fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef fallback fill:#fae8ff,stroke:#a21caf,color:#581c87
    classDef kernel fill:#fef9c3,stroke:#a16207,color:#713f12
    classDef storage fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef external fill:#fed7aa,stroke:#c2410c,color:#7c2d12
```

**Key takeaway:** the agent is mostly orchestration — 5 of 6 tools just call the deterministic kernel. Only `escalate_to_claude_multimodal` actually calls an LLM, and only for cells the kernel can't read. The SteeringHandler is the safety net that prevents the LLM brain from claiming "done" prematurely.

---

## §3 — Lesson 3 zoom-in: The Step Functions Orchestration

**Where it fits in the master diagram:** the "Orchestration Layer" block. This is the conductor that ties everything together.

```mermaid
flowchart TB
    S3["📦 S3 inputs bucket<br/>(EventBridge enabled)"]:::storage
    EB["EventBridge rule<br/>source=aws.s3, detailType=Object Created"]:::orch

    subgraph SFN["Step Functions main pipeline (Standard workflow)"]
        direction TB
        Classify["Stage 1: Classify Lambda<br/>filename regex → union/period"]:::lambda
        GetCfg["Stage 1a: DynamoGetItem<br/>read agent-config table"]:::ddb
        AgentGate{Stage 1b<br/>AgentEnabled?}:::choice
        Extract["Stage 2: ExtractorInvoker Lambda<br/>↓<br/>bedrock-agentcore:InvokeAgentRuntime"]:::lambda
        Validate["Stage 3: Validate Parallel<br/>━━━━━━━━━━━━━━━━<br/>checksum + range + confidence"]:::parallel
        Gate{Stage 4<br/>All passed?}:::choice
        Render["Stage 5: Render Parallel<br/>━━━━━━━━━━━━━━━━<br/>xlsx + csv + articles"]:::parallel
        Review["RouteToReview Lambda<br/>writes to DDB review table"]:::lambda
        Pub["Stage 6: Published<br/>(Succeed state)"]:::success
        Await["AwaitingReview<br/>(Succeed state)"]:::success
        Fail["PipelineFailed<br/>(Fail state)"]:::failure
    end

    AgentCore["🤖 AgentCore Runtime<br/>(Lesson 2)"]:::agent
    DDBAgent[("agent-config<br/>DDB table")]:::storage
    DDBReview[("review<br/>DDB table")]:::storage
    S3Out["📦 S3 outputs bucket"]:::storage
    AuroraIn["Aurora rate_periods<br/>INSERT state=pending_review"]:::storage
    SNS["📡 SNS failures topic<br/>→ email + Slack"]:::external

    S3 --> EB --> Classify
    Classify --> GetCfg
    Classify -. on error .-> Fail
    GetCfg --> AgentGate
    DDBAgent -.-> GetCfg
    AgentGate -- "enabled=true" --> Extract
    AgentGate -- "enabled=false" --> Validate
    Extract <--> AgentCore
    Extract --> Validate
    Validate --> Gate
    Gate -- "all passed=true" --> Render
    Gate -- "any failed" --> Review
    Render --> Pub
    Render --> S3Out
    Render --> AuroraIn
    Review --> DDBReview
    Review --> Await
    Fail -.-> SNS

    classDef storage fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef orch fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef lambda fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef ddb fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef choice fill:#fce7f3,stroke:#db2777,color:#831843
    classDef parallel fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef agent fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef success fill:#bbf7d0,stroke:#15803d,color:#14532d
    classDef failure fill:#fecaca,stroke:#b91c1c,color:#7f1d1d
    classDef external fill:#fed7aa,stroke:#c2410c,color:#7c2d12
```

**Key takeaway:** every Lambda has automatic retries (3x exp backoff). The Choice states are where the human-meaningful decisions happen — admin's enable/disable toggle (1b), validators' verdict (4). Failures branch to SNS + ops; low-confidence cells go to the review queue for humans.

---

## §4 — Lesson 4 zoom-in: The Human Approval Gate

**Where it fits in the master diagram:** the "Approval Gate" block — between "rate sheet produced" and "rate sheet consumed by Calculator."

```mermaid
stateDiagram-v2
    direction LR
    [*] --> pending_review: engine produces rate sheet
    pending_review --> approved: Business clicks Approve<br/>(review queue empty)
    pending_review --> rejected: Business clicks Reject<br/>(reason required)
    rejected --> pending_review: engine re-runs after corrections
    approved --> pending_review: Business unapprove<br/>(within 24h, original approver)
    approved --> published: Admin/Ops clicks Publish<br/>(API returns 409 unless approved)
    published --> [*]: Calculator pulls rate sheet<br/>(immutable from here)
```

**The 4 endpoints + shared authz layer:**

```mermaid
flowchart TB
    User["Cognito JWT<br/>(cognito:groups claim)"]:::input
    APIGW["API Gateway HTTP API<br/>Cognito JWT authorizer (auth only)"]:::orch

    subgraph Layer["Shared Lambda Layer (/opt/python/authz.py)"]
        Authz["authz.enforce_groups(event, ALLOWED_GROUPS)<br/>━━━━━━━━━━━━━━━━━━<br/>returns 403 if cognito:groups<br/>doesn't intersect ALLOWED_GROUPS"]:::shared
    end

    subgraph Lambdas["Approval-state Lambdas (Lesson 4)"]
        direction TB
        Pub["ratesheet-publish<br/>ALLOWED_GROUPS=Admins,Operations<br/>━━━━━━━━━━━━━━━━━━<br/>1. enforce_groups()<br/>2. read_approval_state (Aurora)<br/>3. publish_guard() — 409 unless approved<br/>4. stamp published_by"]:::adminLambda
        App["ratesheet-approve<br/>ALLOWED_GROUPS=Business<br/>━━━━━━━━━━━━━━━━━━<br/>1. enforce_groups()<br/>2. approve_transition() — 422 if queue not empty<br/>3. UPDATE rate_periods SET state='approved'<br/>4. put_events(rate-sheet.approved)"]:::businessLambda
        Rej["ratesheet-reject<br/>ALLOWED_GROUPS=Business<br/>━━━━━━━━━━━━━━━━━━<br/>1. enforce_groups()<br/>2. reject_transition() — 422 if no reason<br/>3. UPDATE with reason + tags<br/>4. put_events(rate-sheet.rejected)"]:::businessLambda
        Un["ratesheet-unapprove<br/>ALLOWED_GROUPS=Business<br/>━━━━━━━━━━━━━━━━━━<br/>only original approver,<br/>within 24h, before publish"]:::businessLambda
    end

    Aurora[("Aurora rate_periods<br/>━━━━━━━━━━<br/>approval_state<br/>approved_by, approved_at<br/>rejected_by, rejected_at<br/>rejection_reason, rejection_tags<br/>published_by, published_at")]:::storage
    EB["EventBridge engine bus<br/>laboraid.rate-sheet.{approved,rejected,published}"]:::orch

    User --> APIGW
    APIGW --> Authz
    Authz -- "passed" --> Pub
    Authz -- "passed" --> App
    Authz -- "passed" --> Rej
    Authz -- "passed" --> Un
    Authz -. "denied (403)" .-> User

    Pub -- "SELECT" --> Aurora
    App -- "UPDATE" --> Aurora
    Rej -- "UPDATE" --> Aurora
    Un -- "UPDATE" --> Aurora

    App --> EB
    Rej --> EB
    Pub --> EB

    classDef input fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef shared fill:#f3f4f6,stroke:#525252,color:#262626
    classDef adminLambda fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef businessLambda fill:#fce7f3,stroke:#db2777,color:#831843
    classDef orch fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef storage fill:#dcfce7,stroke:#15803d,color:#14532d
```

**Key takeaway:** the 409 guard reads from Aurora (not request body — audit fix B1). Approve writes to Aurora AND fires EventBridge (audit fix B2). Every gated Lambda checks Cognito groups via the shared layer (audit fix B3).

---

## §5 — Lesson 5 zoom-in: The CDK Foundation

**Where it fits in the master diagram:** the dashed CDK Foundation block at the bottom. Doesn't run at runtime — it's how everything else gets *described* and *deployed*.

```mermaid
flowchart TB
    Dev["👨‍💻 Developer / CI<br/>npx cdk synth<br/>npx cdk deploy --all"]:::input

    subgraph Foundation["CDK Foundation — 5 patterns"]
        direction TB
        Config["1. Config dataclass<br/>━━━━━━━━━━━━<br/>env, account, region<br/>mandatory_tags (13)<br/>domain_name<br/>(frozen, env-aware)"]:::pattern
        Naming["2. naming.name()<br/>━━━━━━━━━━━━<br/>laboraid-{env}-{layer}-{type}-{purpose}<br/>(validates env, layer, kebab-case)"]:::pattern
        Tags["3. MandatoryTagsAspect<br/>━━━━━━━━━━━━<br/>Aspect visits every L1 CfnResource<br/>stamps 13 tags (priority 100)<br/>per-resource overrides win"]:::pattern
        Constructs["4. Tagged wrappers<br/>━━━━━━━━━━━━<br/>TaggedBucket: KMS + BPA + TLS-only + versioned<br/>TaggedLambda: Py3.12 ARM64 + Powertools + log group<br/>SnsTopicWithSubs<br/>StrandsAgentRuntime"]:::pattern
        AppPy["5. cdk/app.py entry<br/>━━━━━━━━━━━━<br/>read CDK context (-c env=prod)<br/>instantiate 9 stacks<br/>add_dependency() explicit ordering<br/>Aspects.of(app).add(MandatoryTagsAspect)<br/>app.synth()"]:::pattern
    end

    subgraph Stacks["9 stacks (assembled by app.py)"]
        direction TB
        UI["UiStack"]:::stack
        Sec["SecurityStack"]:::stack
        Stor["StorageStack"]:::stack
        AI["AiStack"]:::stack
        Proc["ProcessingStack"]:::stack
        Val["ValidationStack"]:::stack
        Api["ApiStack"]:::stack
        Orch["OrchestrationStack"]:::stack
        Obs["ObservabilityStack"]:::stack
    end

    CFN["CloudFormation templates<br/>(cdk.out/*.template.json)"]:::output
    AWS["☁️ AWS<br/>9 CloudFormation stacks deployed<br/>(in dependency order)"]:::output

    Dev --> Foundation
    Foundation --> Stacks
    Stacks --> CFN
    CFN --> AWS

    Config -.-> Stacks
    Naming -.-> Stacks
    Tags -.-> Stacks
    Constructs -.-> Stacks
    AppPy -.-> Stacks

    classDef input fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef pattern fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef stack fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef output fill:#f0fdf4,stroke:#16a34a,color:#14532d
```

**Key takeaway:** five patterns + one entry file = 9 stacks. Adding a 10th stack is the same recipe: Config in, name() everywhere, TaggedBucket/Lambda for resources, register in app.py, MandatoryTagsAspect tags it.

---

## §6 — Lesson 6 zoom-in: The Storage Stack

**Where it fits in the master diagram:** the "Storage Layer" block. Every other stack depends on this one.

```mermaid
flowchart TB
    Sec["SecurityStack<br/>(provides master_key KMS CMK)"]:::external

    subgraph Storage["StorageStack — Lesson 6"]
        direction TB

        subgraph S3Buckets["6 S3 buckets (all via TaggedBucket: KMS + BPA + TLS-only + versioned)"]
            direction LR
            B0["audit<br/>(server-access-log sink)"]:::audit
            B1["inputs ★<br/>EventBridge=ON<br/>fires SFN pipeline<br/>archive lifecycle"]:::input
            B2["processed<br/>90-day expiry"]:::intermediate
            B3["outputs<br/>archive lifecycle<br/>Object Lock (prod)"]:::output
            B4["profiles<br/>(ops-managed YAMLs)"]:::config
            B5["cba-corpus<br/>(KB deferred)"]:::deferred
        end

        subgraph DDBTables["7 DynamoDB tables (PAY_PER_REQUEST, KMS, PITR)"]
            direction LR
            T1["files<br/>PK=tenant#union<br/>SK=period#filename<br/>📡 stream"]:::stream
            T2["jobs<br/>PK=job_id<br/>📡 stream"]:::stream
            T3["review<br/>PK=tenant<br/>SK=created_at#cell_id"]:::ddb
            T4["overrides<br/>PK=tenant#union#period"]:::ddb
            T5["cadence<br/>PK=tenant#union"]:::ddb
            T6["idempotency<br/>PK=request_hash<br/>TTL 24h"]:::ddb
            T7["agent-config ★<br/>PK=agent_name<br/>(admin enable toggle)"]:::ddb
        end

        subgraph AuroraBlock["Aurora Serverless v2 Postgres"]
            direction TB
            Net["Minimal VPC<br/>max_azs=2, nat_gateways=0<br/>PRIVATE_ISOLATED subnets"]:::network
            DB[("Aurora cluster<br/>0.5-2 ACU autoscale<br/>enable_data_api=TRUE<br/>━━━━━━━━━━━<br/>unions, rate_periods,<br/>rate_cells, audit_log")]:::aurora
            SI["SchemaInitFn (Custom Resource)<br/>━━━━━━━━━━━<br/>runs schema.sql via RDS Data API<br/>idempotent (IF NOT EXISTS)<br/>re-runs on schemaVersion bump"]:::custom
            Sec_mgr[("Secrets Manager<br/>laboraid-{env}-l3-secret-aurora")]:::secret
            DB --- Sec_mgr
            DB --- Net
            SI --> DB
        end
    end

    DownStacks["Downstream stacks<br/>(Processing, API, Orchestration, Validation, etc.)"]:::external

    Sec --> Storage
    B0 -.- "logs target for" .-> S3Buckets
    Storage --> DownStacks
    B1 -- "EventBridge ObjectCreated" --> SFN["Step Functions<br/>(Lesson 3)"]:::external
    T7 -- "DynamoGetItem in SFN" --> SFN
    DB -- "Data API (HTTPS)" --> Lambdas["Lesson 4 Lambdas<br/>(publish/approve/reject)"]:::external

    classDef external fill:#f3f4f6,stroke:#525252,color:#262626
    classDef audit fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef input fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef intermediate fill:#fef9c3,stroke:#a16207,color:#713f12
    classDef output fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef config fill:#fae8ff,stroke:#a21caf,color:#581c87
    classDef deferred fill:#e5e7eb,stroke:#6b7280,color:#374151,stroke-dasharray: 4 4
    classDef ddb fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef stream fill:#86efac,stroke:#15803d,color:#14532d
    classDef network fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef aurora fill:#fce7f3,stroke:#db2777,color:#831843
    classDef custom fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef secret fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
```

★ = referenced by name from the master diagram. The `inputs` bucket fires SFN; the `agent-config` table powers the admin toggle.

**Key takeaway:** Aurora lives in an isolated VPC (no NAT, no internet). Lambdas reach it via the RDS Data API over HTTPS — no VPC attachment needed. The schema is applied automatically by a CloudFormation custom resource on every deploy.

---

## §7 — Lesson 7 zoom-in: The React UI

**Where it fits in the master diagram:** the "UI Layer" block. Two personas, one Vite build, gated by Cognito groups.

```mermaid
flowchart TB
    Boot["main.tsx → App.tsx<br/>━━━━━━━━━━━<br/>useEffect(getGroups, [])<br/>persona = personaForGroups(groups)<br/>landing based on persona"]:::entry

    Cog["Cognito<br/>fetchAuthSession()<br/>cognito:groups claim"]:::external
    Store[("Zustand stores<br/>useUserStore<br/>useOverrideStore")]:::store

    subgraph Routing["routes.tsx + RouteGuard"]
        direction TB
        ADMIN["const ADMIN = ['Admins', 'Operations']"]:::const
        ADMINONLY["const ADMINS_ONLY = ['Admins']"]:::const
        BUSINESS["const BUSINESS = ['Business']"]:::const
        Guard["RouteGuard<br/>━━━━━━━━<br/>if user groups ∩ allowed = ∅<br/>→ Forbidden403"]:::guard
    end

    subgraph AdminTree["/admin/* — AdminLayout"]
        direction TB
        Dash["Dashboard"]:::page
        Ups["Uploads<br/>(presigned URL)"]:::page
        Jobs["Jobs ★<br/>(usePolling 5s)"]:::page
        JD["JobDetail"]:::page
        Agents["Agents<br/>(AgentToggle → PATCH)"]:::page
        Prof["Profiles"]:::page
        Aud["Audit"]:::page
        Costs["Costs<br/>(Admins-only guard)"]:::page
    end

    subgraph BizTree["/business/* — BusinessLayout"]
        direction TB
        Inbox["Inbox<br/>(pending_review)"]:::page
        RSR["RateSheetReview ★★<br/>━━━━━━━━━━━<br/>3-panel: PDF + table + provenance<br/>+ ApproveRejectBar"]:::page
        ByU["ByUnion"]:::page
        Ap["Approved"]:::page
        Rj["Rejected"]:::page
        RQ["ReviewQueue"]:::page
        Me["Me"]:::page
    end

    subgraph Libs["Shared libs (every page uses these)"]
        direction LR
        Auth["lib/auth.ts<br/>getGroups, getJwt"]:::lib
        Api["lib/api.ts<br/>api.get/post/put/patch<br/>(injects JWT)"]:::lib
        Poll["lib/usePolling.ts<br/>5s gated on active"]:::lib
    end

    Bedrock["API Gateway → Lambdas<br/>(Lesson 4)"]:::external

    Cog --> Auth --> Store
    Auth --> Boot
    Boot --> Guard
    Guard --> AdminTree
    Guard --> BizTree
    AdminTree --> Api
    BizTree --> Api
    Costs -. "extra ADMINS_ONLY guard" .-> Guard
    Api --> Bedrock

    classDef entry fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef external fill:#f3f4f6,stroke:#525252,color:#262626
    classDef store fill:#fce7f3,stroke:#db2777,color:#831843
    classDef const fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef guard fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef page fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef lib fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
```

★ Jobs page is the canonical CRUD pattern (Lesson 7 Pattern 4).
★★ RateSheetReview is the most complex page (Lesson 7 Pattern 5) — its ApproveRejectBar is the line-by-line UI client for Lesson 4's approve/reject Lambdas.

**Key takeaway:** the UI is the rendered version of the API. Every button click maps to one Lambda. There's no UI-only logic — defense in depth (UI disables buttons + Lambda returns 4xx) but the source of truth is always the Lambda.

---

## §8 — The full wire: actor → UI → API → DB → SFN → agent → DB → UI (end to end)

This is the master diagram from §0, with all the cross-layer arrows drawn:

```mermaid
sequenceDiagram
    autonumber
    actor Admin as 👨‍💼 Admin/Ops
    actor Business as 👩‍💼 Business/SME
    participant AdminUI as Admin UI<br/>(/admin/*)
    participant BusinessUI as Business UI<br/>(/business/*)
    participant API as API Gateway<br/>+ Lambdas
    participant S3 as S3 inputs
    participant SFN as Step Functions
    participant DDB as agent-config<br/>DDB
    participant Agent as ExtractorAgent<br/>on AgentCore
    participant Kernel as Kernel<br/>(deterministic)
    participant Bedrock as Bedrock Claude<br/>(fallback)
    participant Aurora as Aurora<br/>rate_periods
    participant Calc as Calculator

    Note over Admin,Calc: Stage A — Upload (Lesson 7 UI + Lesson 6 Storage)
    Admin->>AdminUI: Click Uploads, drop PDF
    AdminUI->>API: GET /v1/uploads (presign)
    API-->>AdminUI: presigned URL
    AdminUI->>S3: PUT PDF
    S3-->>SFN: EventBridge: Object Created

    Note over Admin,Calc: Stage B — Pipeline (Lesson 3 Orchestration + Lesson 2 Agent)
    SFN->>SFN: Classify Lambda (regex → union, period)
    SFN->>DDB: GetItem agent-config
    DDB-->>SFN: enabled=true
    SFN->>Agent: ExtractorInvoker → InvokeAgentRuntime
    Agent->>Kernel: run_kernel_extractor (Lesson 1)
    Kernel-->>Agent: rows + gaps
    Note over Agent: SteeringHandler enforces checksum
    opt low-confidence cells
        Agent->>Bedrock: escalate_to_claude_multimodal
        Bedrock-->>Agent: missing cells
    end
    Agent-->>SFN: extraction complete
    SFN->>SFN: Validate Parallel (checksum + range + confidence)
    SFN->>S3: Render xlsx + csv + articles → outputs bucket
    SFN->>Aurora: INSERT rate_periods (approval_state='pending_review')

    Note over Admin,Calc: Stage C — Human Approval (Lesson 4 Lambdas + Lesson 7 UI)
    Business->>BusinessUI: Open Inbox, click rate sheet
    BusinessUI->>API: GET /v1/unions/704/rate-sheets/2026-01-01
    API-->>BusinessUI: canonical JSON
    Business->>BusinessUI: Review 3-panel, all cells OK
    Business->>BusinessUI: Click Approve in ApproveRejectBar
    BusinessUI->>API: POST .../approve
    API->>API: authz.enforce_groups(Business) ✓
    API->>Aurora: UPDATE state='approved' + approved_by/at
    API-->>BusinessUI: { state: 'approved', approved_by: ... }

    Note over Admin,Calc: Stage D — Publish (Lesson 4 Lambda + Lesson 6 Aurora)
    Admin->>AdminUI: Click Publish for release
    AdminUI->>API: POST .../publish
    API->>API: authz.enforce_groups(Admins,Operations) ✓
    API->>Aurora: SELECT approval_state (authoritative)
    Aurora-->>API: 'approved'
    API->>Aurora: UPDATE state='published' + published_by/at
    API-->>AdminUI: { state: 'published' }

    Note over Admin,Calc: Stage E — Consume
    Calc->>API: GET /v1/unions/704/rate-sheets/2026-01-01
    API->>Aurora: SELECT canonical_json WHERE state='published'
    Aurora-->>API: rate sheet
    API-->>Calc: canonical JSON (immutable)
```

---

## Per-lesson mapping (cheat sheet)

When you're looking at the master flow and asking "where does X come from?", use this:

| Block in §0 master flow | Lesson | What to read in Lesson |
|---|---|---|
| PDF upload via Admin UI | Lesson 7 §Pattern 4 (Uploads page) | `ui/src/admin/Uploads.tsx` + `lib/api.ts` |
| S3 → EventBridge → SFN | Lesson 3 Part 1 | `cdk/laboraid_cdk/stacks/orchestration_stack.py` |
| Classify Lambda | Lesson 3 Stage 1 | `lambdas/processing/classifier/handler.py` |
| Agent enable/disable toggle | Lesson 3 Stage 1a/1b + Lesson 4 (agent-toggle) | `agent-config` DDB + Choice state in `sfn/main_pipeline.py` |
| Strands ExtractorAgent | Lesson 2 | `agents/extractor/agent.py` + `steering.py` + `system-prompt.md` |
| Kernel deterministic extraction | Lesson 1 + 2 | `kernel/pipeline/{extract,compute,pivot}.py` |
| Validators (3 parallel) | Lesson 3 Stage 3 | `lambdas/validation/{checksum,range,confidence}/handler.py` |
| Render (3 parallel) | Lesson 3 Stage 5 | `lambdas/rendering/{xlsx,csv,articles}-renderer/handler.py` |
| Review queue write | Lesson 3 review path | `lambdas/validation/review-router/handler.py` |
| Aurora `rate_periods` schema | Lesson 4 Part 7 + Lesson 6 Part 4 | `cdk/assets/schema_init/schema.sql` |
| Business Approve/Reject | Lesson 4 + Lesson 7 | `ratesheet-{approve,reject}/handler.py` + `ApproveRejectBar.tsx` |
| Publish 409 gate | Lesson 4 Part 2 | `ratesheet-publish/handler.py` |
| Cognito group checks | Lesson 4 Part 1 + Lesson 7 Pattern 2 | `lambdas/api/_shared/python/authz.py` + `RouteGuard.tsx` |
| Calculator consumes published | Lesson 4 state machine | `GET /v1/unions/.../rate-sheets/...` Lambda + Aurora SELECT |
| All resources tagged + named | Lesson 5 | `cdk/laboraid_cdk/{util/naming,aspects/mandatory_tags,config}.py` |
| Stack composition + deploy order | Lesson 5 Pattern 5 | `cdk/app.py` |

---

## Where to go next

- For depth on any lesson block → [`Learning_Lessons.md`](Learning_Lessons.md)
- For the read-order roadmap → [`Understanding.md`](Understanding.md)
- For the original spec → [`09_Technical_Implementation_Spec.md`](09_Technical_Implementation_Spec.md)
- For the management view → [`CTO_SUMMARY.md`](CTO_SUMMARY.md)
- For audit receipts → [`AUDIT_REPORT.md`](AUDIT_REPORT.md) + [`AUDIT_VERIFICATION.md`](AUDIT_VERIFICATION.md)
