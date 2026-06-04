# End-to-End Architecture Flow

Visual companion to [`Learning_Lessons.md`](Learning_Lessons.md). One master end-to-end diagram, then per-lesson zoom-ins.

> **Two ways to view this:**
> - **GitHub** (this file) — Mermaid blocks render inline on github.com (this is the version with the syntax fixes that don't break the renderer)
> - **Browser SPA** ([`Architecture_Flow.html`](Architecture_Flow.html)) — open locally for the same diagrams with sticky nav + color-coded sections + sequence chart

Sections: [§0 Master](#0--the-whole-system-in-one-diagram) · [§1 Canonical (L1)](#1--lesson-1-zoom-in-the-canonical-layer) · [§2 Strands Agent (L2)](#2--lesson-2-zoom-in-the-strands-agent-on-agentcore) · [§3 Step Functions (L3)](#3--lesson-3-zoom-in-the-step-functions-orchestration) · [§4 Approval Gate (L4)](#4--lesson-4-zoom-in-the-human-approval-gate) · [§5 CDK Foundation (L5)](#5--lesson-5-zoom-in-the-cdk-foundation) · [§6 Storage Stack (L6)](#6--lesson-6-zoom-in-the-storage-stack) · [§7 React UI (L7)](#7--lesson-7-zoom-in-the-react-ui) · [§8 Full Sequence](#8--the-full-wire-end-to-end-sequence-diagram) · [Cheat sheet](#per-lesson-mapping-cheat-sheet)

---

## §0 — The whole system in one diagram

```mermaid
flowchart TB
    PDF["📄 PDF<br/>Customer uploads a CBA or Rate Notice"]
    Admin["👨‍💼 Admin / Operations<br/>(NBS + LaborAid ops)"]
    Business["👩‍💼 Business / SME<br/>(LaborAid + Union rep)"]
    Calc["📊 LaborAid Calculator<br/>(downstream)"]

    subgraph UI["UI Layer — Lesson 7"]
        AdminUI["/admin/* shell<br/>8 pages, Cognito gated"]
        BusinessUI["/business/* shell<br/>7 pages, Cognito gated"]
    end

    subgraph Engine["Engine Layer — Lessons 1 + 2"]
        Kernel["Kernel (deterministic)<br/>PDF → canonical → CSV"]
        Agent["Strands ExtractorAgent on AgentCore<br/>6 @tools + Bedrock fallback"]
    end

    subgraph Orchestration["Orchestration — Lesson 3"]
        SFN["Step Functions<br/>Classify → Gate → Extract → Validate → Render"]
    end

    subgraph Storage["Storage — Lesson 6"]
        S3["6 S3 buckets"]
        DDB["7 DynamoDB tables"]
        Aurora["Aurora Postgres<br/>rate_periods + approval_state"]
    end

    subgraph Approval["Approval Gate — Lesson 4"]
        Approve["ratesheet-approve (Business)"]
        Reject["ratesheet-reject (Business)"]
        Publish["ratesheet-publish (Admin/Ops)<br/>409 unless approved"]
    end

    CDK["CDK Foundation — Lesson 5<br/>Config + naming + tags + tagged constructs"]

    Admin -->|1. Upload PDF| AdminUI
    AdminUI -->|presigned URL| S3
    PDF -.->|lands in| S3
    S3 -->|2. ObjectCreated| SFN
    SFN -->|3a. Classify| SFN
    SFN -->|3b. Read agent-config| DDB
    SFN -->|3c. Extract| Agent
    Agent -->|calls @tool| Kernel
    Agent -->|Bedrock fallback| Engine
    Agent -->|results| SFN
    SFN -->|4. Validate| SFN
    SFN -->|5. Render → S3| S3
    SFN -->|6. INSERT pending_review| Aurora

    Aurora -->|shows in Inbox| BusinessUI
    Business -->|Open / Review| BusinessUI
    BusinessUI -->|Approve| Approve
    BusinessUI -->|Reject| Reject
    Approve -->|UPDATE state=approved| Aurora
    Reject -->|UPDATE state=rejected| Aurora

    Admin -->|click Publish| AdminUI
    AdminUI -->|POST /publish| Publish
    Publish -->|read state| Aurora
    Publish -->|UPDATE state=published| Aurora

    Aurora -->|GET rate-sheet| Calc

    CDK -.->|deploys via cdk deploy| Orchestration
    CDK -.->|deploys via cdk deploy| Storage
    CDK -.->|deploys via cdk deploy| Approval
    CDK -.->|deploys via cdk deploy| Engine
    CDK -.->|deploys via cdk deploy| UI

    classDef adminBlock fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef businessBlock fill:#fce7f3,stroke:#db2777,color:#831843
    classDef engineBlock fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef orchBlock fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef storageBlock fill:#dcfce7,stroke:#15803d,color:#14532d

    class AdminUI,Publish adminBlock
    class BusinessUI,Approve,Reject businessBlock
    class Kernel,Agent engineBlock
    class SFN orchBlock
    class S3,DDB,Aurora storageBlock
```

### Read the diagram with the lesson lens

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

**Where it fits:** inside the "Engine Layer" block. The canonical layer is the in-memory shape the kernel and agent both operate on.

```mermaid
flowchart LR
    PDF["📄 Union PDF<br/>(rate notice / CBA)"]
    Extractor["extract_704()<br/>per-union extractor"]
    Compute["compute.py<br/>derived columns<br/>wage × 1.5, P&G ×1.10/1.15/1.25"]
    Pivot["pivot.py<br/>canonical → wide CSV<br/>(matches groundtruth header)"]
    Output["📄 Output CSV<br/>data/&lt;union&gt;/ai_output/...csv"]

    Profile["profiles/704.yaml<br/>columns + multiplier_of + factor"]
    Fields["canonical/fields.yaml<br/>wage / health_welfare / pension /<br/>apprenticeship_training / ..."]

    subgraph CanonLayer["canonical/ (in-memory shapes)"]
        direction TB
        RC["RateCell<br/>━━━━━━<br/>zone: Building<br/>classification: Journeyman<br/>canonical_field: wage<br/>value: 54.70<br/>source_doc + locator<br/>confidence: 0.95"]
        CR["ClassificationRow<br/>━━━━━━<br/>cells: { wage, health_welfare,<br/>pension, sis, ... }"]
        R2["r2() half-up rounding<br/>83.505 → 83.51 (not 83.50)"]
        RC --> CR
    end

    PDF --> Extractor
    Extractor --> RC
    CR --> Compute
    Compute --> Pivot
    Pivot --> Output
    Profile --> Compute
    Profile --> Pivot
    Fields --> Extractor
    R2 -.-> Compute

    classDef io fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef code fill:#fef9c3,stroke:#a16207,color:#713f12
    classDef canon fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef cfg fill:#fae8ff,stroke:#a21caf,color:#581c87

    class PDF,Output io
    class Extractor,Compute,Pivot code
    class RC,CR,R2 canon
    class Profile,Fields cfg
```

> **Key takeaway:** three vocabularies — PDF native ("Wage", "H&W"), canonical internal (`wage`, `health_welfare`), output CSV header. RateCell carries provenance so every output value is auditable.

---

## §2 — Lesson 2 zoom-in: The Strands Agent on AgentCore

**Where it fits:** inside the "Engine Layer" block, on top of the kernel. The agent is what makes the kernel callable from AWS.

```mermaid
flowchart TB
    Input["Step Functions payload<br/>{ union, s3_prefix, job_id }"]

    subgraph AgentCore["AgentCore Runtime container"]
        direction TB
        Entry["app.py BedrockAgentCoreApp<br/>@entrypoint def invoke(payload)"]
        Brain["LLM brain reads system-prompt.md<br/>RFC-2119 7-step procedure"]
        Steering["ExtractorSteering<br/>blocks completion unless<br/>checksum_validated AND<br/>gaps escalated"]

        subgraph Tools["6 @tool functions"]
            direction TB
            T1["@tool stage_inputs_from_s3"]
            T2["@tool run_kernel_extractor<br/>→ k_extract.EXTRACTORS[union]()"]
            T3["@tool compute_derived_columns<br/>→ k_compute.resolve_row()"]
            T4["@tool pivot_to_ratesheet_csv<br/>→ k_pivot.write_csv()"]
            T5["@tool validate_total_package_checksum"]
            T6["@tool escalate_to_claude_multimodal<br/>(Bedrock Sonnet 4.6 + PDF)"]
        end

        Entry --> Brain
        Brain <--> Steering
        Brain --> Tools
    end

    subgraph Kernel["Kernel (Lesson 1)"]
        direction TB
        K1["pipeline/extract.py"]
        K2["pipeline/compute.py"]
        K3["pipeline/pivot.py"]
        K4["canonical/model.py — r2() etc."]
    end

    S3In["S3 inputs bucket"]
    S3Out["S3 outputs bucket"]
    Bedrock["AWS Bedrock<br/>Claude Sonnet 4.6 + Haiku 4.5<br/>+ PII Guardrail"]

    Input --> Entry
    T1 --> S3In
    T2 --> K1
    T3 --> K2
    T4 --> K3
    T4 --> S3Out
    T5 --> K4
    T6 --> Bedrock

    AgentCore -->|returns result| Output["Step Functions resumes"]

    classDef io fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef brain fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef steer fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef tool fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef fallback fill:#fae8ff,stroke:#a21caf,color:#581c87
    classDef kernel fill:#fef9c3,stroke:#a16207,color:#713f12
    classDef external fill:#fed7aa,stroke:#c2410c,color:#7c2d12

    class Input,Output io
    class Entry,Brain brain
    class Steering steer
    class T1,T2,T3,T4,T5 tool
    class T6 fallback
    class K1,K2,K3,K4 kernel
    class S3In,S3Out,Bedrock external
```

> **Key takeaway:** 5 of 6 tools just call the deterministic kernel. Only `escalate_to_claude_multimodal` hits an LLM, and only when the kernel can't read a cell. The SteeringHandler prevents the LLM brain from claiming "done" prematurely.

---

## §3 — Lesson 3 zoom-in: The Step Functions Orchestration

**Where it fits:** the "Orchestration Layer" block. The conductor that ties everything together.

```mermaid
flowchart TB
    S3["📦 S3 inputs bucket<br/>(EventBridge enabled)"]
    EB["EventBridge rule<br/>source=aws.s3<br/>detailType=Object Created"]

    subgraph SFN["Step Functions main pipeline"]
        direction TB
        Classify["Stage 1 — Classify Lambda<br/>filename regex → union/period"]
        GetCfg["Stage 1a — DynamoGetItem<br/>read agent-config"]
        AgentGate{Stage 1b<br/>AgentEnabled?}
        Extract["Stage 2 — ExtractorInvoker Lambda<br/>↓<br/>bedrock-agentcore:InvokeAgentRuntime"]
        Validate["Stage 3 — Validate Parallel<br/>checksum + range + confidence"]
        Gate{Stage 4<br/>All passed?}
        Render["Stage 5 — Render Parallel<br/>xlsx + csv + articles"]
        Review["RouteToReview Lambda<br/>writes to DDB review table"]
        Pub["Stage 6 — Published<br/>(Succeed)"]
        Await["AwaitingReview<br/>(Succeed)"]
        Fail["PipelineFailed<br/>(Fail)"]
    end

    AgentCore["🤖 AgentCore Runtime<br/>(Lesson 2)"]
    DDBAgent[("agent-config<br/>DDB table")]
    DDBReview[("review<br/>DDB table")]
    S3Out["📦 S3 outputs bucket"]
    AuroraIn["Aurora rate_periods<br/>INSERT state=pending_review"]
    SNS["📡 SNS failures topic<br/>email + Slack"]

    S3 --> EB --> Classify
    Classify --> GetCfg
    Classify -.->|on error| Fail
    GetCfg --> AgentGate
    DDBAgent -.-> GetCfg
    AgentGate -->|enabled=true| Extract
    AgentGate -->|enabled=false| Validate
    Extract <--> AgentCore
    Extract --> Validate
    Validate --> Gate
    Gate -->|passed| Render
    Gate -->|failed| Review
    Render --> Pub
    Render --> S3Out
    Render --> AuroraIn
    Review --> DDBReview
    Review --> Await
    Fail -.-> SNS

    classDef storage fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef orch fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef lambda fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef choice fill:#fce7f3,stroke:#db2777,color:#831843
    classDef parallel fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef agent fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef success fill:#bbf7d0,stroke:#15803d,color:#14532d
    classDef failure fill:#fecaca,stroke:#b91c1c,color:#7f1d1d
    classDef external fill:#fed7aa,stroke:#c2410c,color:#7c2d12

    class S3,DDBAgent,DDBReview,S3Out,AuroraIn storage
    class EB orch
    class Classify,Extract,Review lambda
    class AgentGate,Gate choice
    class Validate,Render parallel
    class AgentCore agent
    class Pub,Await success
    class Fail failure
    class SNS external
```

> **Key takeaway:** every Lambda has automatic retries (3× exp backoff). Choice states are where human-meaningful decisions live — the admin's enable/disable toggle, the validators' verdict. Low-confidence cells go to the review queue; pipeline failures go to SNS + ops.

---

## §4 — Lesson 4 zoom-in: The Human Approval Gate

**Where it fits:** the "Approval Gate" block — between "rate sheet produced" and "rate sheet consumed by Calculator."

### Approval state machine

```mermaid
stateDiagram-v2
    direction LR
    [*] --> pending_review : engine produces rate sheet
    pending_review --> approved : Business clicks Approve (queue empty)
    pending_review --> rejected : Business clicks Reject (reason required)
    rejected --> pending_review : engine re-runs after corrections
    approved --> pending_review : Business unapprove (within 24h)
    approved --> published : Admin/Ops Publish (409 unless approved)
    published --> [*] : Calculator pulls rate sheet
```

### The 4 endpoints + shared authz layer

```mermaid
flowchart TB
    User["Cognito JWT<br/>(cognito:groups claim)"]
    APIGW["API Gateway HTTP API<br/>Cognito JWT authorizer (auth only)"]

    subgraph Layer["Shared Lambda Layer (/opt/python/authz.py)"]
        Authz["authz.enforce_groups(event, ALLOWED_GROUPS)<br/>━━━━━━━━━━━━━<br/>returns 403 if<br/>cognito:groups ∩ ALLOWED = ∅"]
    end

    subgraph Lambdas["Approval-state Lambdas"]
        direction TB
        Pub["ratesheet-publish<br/>ALLOWED = Admins, Operations<br/>1. enforce_groups<br/>2. read_approval_state (Aurora)<br/>3. publish_guard — 409 unless approved<br/>4. stamp published_by"]
        App["ratesheet-approve<br/>ALLOWED = Business<br/>1. enforce_groups<br/>2. approve_transition — 422 if queue<br/>3. UPDATE state=approved<br/>4. put_events approved"]
        Rej["ratesheet-reject<br/>ALLOWED = Business<br/>1. enforce_groups<br/>2. reject_transition — 422 if no reason<br/>3. UPDATE with reason+tags<br/>4. put_events rejected"]
        Un["ratesheet-unapprove<br/>ALLOWED = Business<br/>original approver only<br/>within 24h, before publish"]
    end

    Aurora[("Aurora rate_periods<br/>approval_state<br/>approved_by, approved_at<br/>rejected_by, rejected_at<br/>rejection_reason, rejection_tags<br/>published_by, published_at")]
    EB["EventBridge engine bus<br/>laboraid.rate-sheet.approved/<br/>rejected/published"]

    User --> APIGW
    APIGW --> Authz
    Authz -->|passed| Pub
    Authz -->|passed| App
    Authz -->|passed| Rej
    Authz -->|passed| Un
    Authz -.->|denied 403| User

    Pub -->|SELECT| Aurora
    App -->|UPDATE| Aurora
    Rej -->|UPDATE| Aurora
    Un -->|UPDATE| Aurora

    App --> EB
    Rej --> EB
    Pub --> EB

    classDef inp fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef shared fill:#f3f4f6,stroke:#525252,color:#262626
    classDef admin fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef biz fill:#fce7f3,stroke:#db2777,color:#831843
    classDef orch fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef storage fill:#dcfce7,stroke:#15803d,color:#14532d

    class User inp
    class Authz,APIGW shared
    class Pub admin
    class App,Rej,Un biz
    class EB orch
    class Aurora storage
```

> **Key takeaway:** the 409 guard reads from Aurora (not request body — audit fix **B1**). Approve writes to Aurora AND fires EventBridge (audit fix **B2**). Every gated Lambda checks Cognito groups via the shared layer (audit fix **B3**).

---

## §5 — Lesson 5 zoom-in: The CDK Foundation

**Where it fits:** the dashed CDK Foundation block. Doesn't run at runtime — it's how everything else gets *described* and *deployed*.

```mermaid
flowchart TB
    Dev["👨‍💻 Developer / CI<br/>npx cdk synth<br/>npx cdk deploy --all"]

    subgraph Foundation["CDK Foundation — 5 patterns"]
        direction TB
        Config["1. Config dataclass<br/>env, account, region<br/>mandatory_tags (13)<br/>domain_name"]
        Naming["2. naming.name()<br/>laboraid-{env}-{layer}-{type}-{purpose}<br/>(kebab-case validated)"]
        Tags["3. MandatoryTagsAspect<br/>visits every L1 CfnResource<br/>stamps 13 tags (priority 100)"]
        Constructs["4. Tagged wrappers<br/>TaggedBucket: KMS + BPA + TLS + versioned<br/>TaggedLambda: Py3.12 ARM64 + Powertools<br/>SnsTopicWithSubs<br/>StrandsAgentRuntime"]
        AppPy["5. cdk/app.py entry<br/>read context (-c env=prod)<br/>instantiate 9 stacks<br/>add_dependency() ordering<br/>Aspects.of(app).add(...)<br/>app.synth()"]
    end

    subgraph Stacks["9 stacks (assembled by app.py)"]
        direction TB
        UI["UiStack"]
        Sec["SecurityStack"]
        Stor["StorageStack"]
        AI["AiStack"]
        Proc["ProcessingStack"]
        Val["ValidationStack"]
        Api["ApiStack"]
        Orch["OrchestrationStack"]
        Obs["ObservabilityStack"]
    end

    CFN["CloudFormation templates<br/>cdk.out/*.template.json"]
    AWS["☁️ AWS<br/>9 CloudFormation stacks<br/>(dependency order)"]

    Dev --> Foundation
    Foundation --> Stacks
    Stacks --> CFN
    CFN --> AWS

    Config -.-> Stacks
    Naming -.-> Stacks
    Tags -.-> Stacks
    Constructs -.-> Stacks
    AppPy -.-> Stacks

    classDef inp fill:#f0f9ff,stroke:#0369a1,color:#0c4a6e
    classDef pat fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef stack fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef out fill:#f0fdf4,stroke:#16a34a,color:#14532d

    class Dev inp
    class Config,Naming,Tags,Constructs,AppPy pat
    class UI,Sec,Stor,AI,Proc,Val,Api,Orch,Obs stack
    class CFN,AWS out
```

> **Key takeaway:** 5 patterns + 1 entry file = 9 stacks. Adding a 10th stack is the same recipe: Config in, name() everywhere, TaggedBucket/Lambda for resources, register in app.py, MandatoryTagsAspect tags everything.

---

## §6 — Lesson 6 zoom-in: The Storage Stack

**Where it fits:** the "Storage Layer" block. Every other stack depends on this one.

```mermaid
flowchart TB
    Sec["SecurityStack<br/>provides master_key KMS CMK"]

    subgraph Storage["StorageStack"]
        direction TB

        subgraph S3B["6 S3 buckets — TaggedBucket: KMS + BPA + TLS + versioned"]
            direction LR
            B0["audit<br/>(server-access-log target)"]
            B1["inputs ★<br/>EventBridge=ON<br/>fires SFN<br/>archive lifecycle"]
            B2["processed<br/>90-day expiry"]
            B3["outputs<br/>archive lifecycle<br/>Object Lock (prod)"]
            B4["profiles<br/>(ops-managed)"]
            B5["cba-corpus<br/>(KB deferred)"]
        end

        subgraph DDBT["7 DynamoDB tables — PAY_PER_REQUEST + KMS + PITR"]
            direction LR
            T1["files<br/>tenant#union / period#filename<br/>📡 stream"]
            T2["jobs<br/>job_id<br/>📡 stream"]
            T3["review<br/>tenant / created_at#cell_id"]
            T4["overrides"]
            T5["cadence"]
            T6["idempotency (TTL 24h)"]
            T7["agent-config ★<br/>(admin toggle)"]
        end

        subgraph AuroraB["Aurora Serverless v2"]
            direction TB
            Net["Minimal VPC<br/>max_azs=2, nat_gateways=0<br/>PRIVATE_ISOLATED"]
            DB[("Aurora cluster<br/>0.5-2 ACU autoscale<br/>enable_data_api=TRUE<br/>unions, rate_periods,<br/>rate_cells, audit_log")]
            SI["SchemaInitFn (Custom Resource)<br/>runs schema.sql via RDS Data API<br/>idempotent (IF NOT EXISTS)"]
            Sm[("Secrets Manager<br/>laboraid-{env}-l3-secret-aurora")]
            DB --- Sm
            DB --- Net
            SI --> DB
        end
    end

    DownStacks["Downstream stacks<br/>(Processing, API, Orchestration, ...)"]
    SFN["Step Functions<br/>(Lesson 3)"]
    Lambdas["Lesson 4 Lambdas<br/>(publish/approve/reject)"]

    Sec --> Storage
    Storage --> DownStacks
    B1 -->|EventBridge ObjectCreated| SFN
    T7 -->|DynamoGetItem in SFN| SFN
    DB -->|Data API HTTPS| Lambdas

    classDef ext fill:#f3f4f6,stroke:#525252,color:#262626
    classDef audit fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef inb fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef inter fill:#fef9c3,stroke:#a16207,color:#713f12
    classDef outb fill:#f0fdf4,stroke:#16a34a,color:#14532d
    classDef cfg fill:#fae8ff,stroke:#a21caf,color:#581c87
    classDef def fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef ddb fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef strm fill:#86efac,stroke:#15803d,color:#14532d
    classDef net fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef au fill:#fce7f3,stroke:#db2777,color:#831843
    classDef cr fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef sm fill:#fee2e2,stroke:#dc2626,color:#7f1d1d

    class Sec,DownStacks,SFN,Lambdas ext
    class B0 audit
    class B1 inb
    class B2 inter
    class B3 outb
    class B4 cfg
    class B5 def
    class T1,T2 strm
    class T3,T4,T5,T6,T7 ddb
    class Net net
    class DB au
    class SI cr
    class Sm sm
```

★ = referenced by name from the master flow. The `inputs` bucket fires SFN; the `agent-config` table powers the admin toggle.

> **Key takeaway:** Aurora lives in an isolated VPC (no NAT, no internet). Lambdas reach it via the RDS Data API over HTTPS — no VPC attachment needed. Schema is applied automatically by a CloudFormation custom resource on every deploy.

---

## §7 — Lesson 7 zoom-in: The React UI

**Where it fits:** the "UI Layer" block. Two personas, one Vite build, gated by Cognito groups.

```mermaid
flowchart TB
    Boot["main.tsx → App.tsx<br/>useEffect(getGroups, [])<br/>persona = personaForGroups(groups)<br/>landing based on persona"]
    Cog["Cognito<br/>fetchAuthSession()<br/>cognito:groups claim"]
    Store[("Zustand stores<br/>useUserStore + useOverrideStore")]

    subgraph Routing["routes.tsx + RouteGuard"]
        direction TB
        Const["ADMIN = [Admins, Operations]<br/>ADMINS_ONLY = [Admins]<br/>BUSINESS = [Business]"]
        Guard["RouteGuard<br/>if groups ∩ allowed = ∅<br/>→ Forbidden403"]
    end

    subgraph AdminTree["/admin/* — AdminLayout"]
        direction TB
        Dash["Dashboard"]
        Ups["Uploads (presigned URL)"]
        Jobs["Jobs ★ (usePolling 5s)"]
        JD["JobDetail"]
        Agents["Agents (AgentToggle PATCH)"]
        Prof["Profiles"]
        Aud["Audit"]
        Costs["Costs (Admins-only)"]
    end

    subgraph BizTree["/business/* — BusinessLayout"]
        direction TB
        Inbox["Inbox (pending_review)"]
        RSR["RateSheetReview ★★<br/>3-panel: PDF + table + provenance<br/>+ ApproveRejectBar"]
        ByU["ByUnion"]
        Ap["Approved"]
        Rj["Rejected"]
        RQ["ReviewQueue"]
        Me["Me"]
    end

    subgraph Libs["Shared libs"]
        direction LR
        Auth["lib/auth.ts<br/>getGroups, getJwt"]
        Api["lib/api.ts<br/>fetch with JWT<br/>get/post/put/patch"]
        Poll["lib/usePolling.ts<br/>5s gated on active"]
    end

    Bedrock["API Gateway → Lambdas<br/>(Lesson 4)"]

    Cog --> Auth --> Store
    Auth --> Boot
    Boot --> Guard
    Guard --> AdminTree
    Guard --> BizTree
    AdminTree --> Api
    BizTree --> Api
    Costs -.->|extra ADMINS_ONLY guard| Guard
    Api --> Bedrock

    classDef inp fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef ext fill:#f3f4f6,stroke:#525252,color:#262626
    classDef store fill:#fce7f3,stroke:#db2777,color:#831843
    classDef gd fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef page fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef lib fill:#ede9fe,stroke:#7c3aed,color:#4c1d95

    class Boot inp
    class Cog,Bedrock,Const ext
    class Store store
    class Guard gd
    class Dash,Ups,Jobs,JD,Agents,Prof,Aud,Costs,Inbox,RSR,ByU,Ap,Rj,RQ,Me page
    class Auth,Api,Poll lib
```

★ Jobs is the canonical CRUD pattern. ★★ RateSheetReview is the most complex page — its ApproveRejectBar is the line-by-line UI client for Lesson 4's approve/reject Lambdas.

> **Key takeaway:** the UI is the rendered version of the API. Every button click maps to one Lambda. There's no UI-only logic — defense in depth (UI disables buttons + Lambda returns 4xx), but the source of truth is always the Lambda.

---

## §8 — The full wire: end-to-end sequence diagram

This is the master flow as a time-ordered sequence chart — actor by actor, message by message, across all five stages.

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Admin/Ops
    actor Business as Business/SME
    participant AdminUI as Admin UI
    participant BusinessUI as Business UI
    participant API as API Gateway + Lambdas
    participant S3 as S3 inputs
    participant SFN as Step Functions
    participant DDB as agent-config DDB
    participant Agent as ExtractorAgent on AgentCore
    participant Kernel as Kernel (deterministic)
    participant Bedrock as Bedrock Claude (fallback)
    participant Aurora as Aurora rate_periods
    participant Calc as Calculator

    Note over Admin,Calc: Stage A — Upload (Lesson 7 + Lesson 6)
    Admin->>AdminUI: Click Uploads, drop PDF
    AdminUI->>API: GET /v1/uploads (presign)
    API-->>AdminUI: presigned URL
    AdminUI->>S3: PUT PDF
    S3-->>SFN: EventBridge Object Created

    Note over Admin,Calc: Stage B — Pipeline (Lesson 3 + Lesson 2)
    SFN->>SFN: Classify Lambda (regex)
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
    SFN->>SFN: Validate Parallel
    SFN->>S3: Render xlsx + csv + articles → outputs
    SFN->>Aurora: INSERT rate_periods (pending_review)

    Note over Admin,Calc: Stage C — Human Approval (Lesson 4 + Lesson 7)
    Business->>BusinessUI: Open Inbox, click rate sheet
    BusinessUI->>API: GET /v1/unions/704/rate-sheets/2026-01-01
    API-->>BusinessUI: canonical JSON
    Business->>BusinessUI: Review 3-panel, all OK
    Business->>BusinessUI: Click Approve
    BusinessUI->>API: POST .../approve
    API->>API: authz.enforce_groups(Business) OK
    API->>Aurora: UPDATE state=approved
    API-->>BusinessUI: state=approved

    Note over Admin,Calc: Stage D — Publish (Lesson 4)
    Admin->>AdminUI: Click Publish
    AdminUI->>API: POST .../publish
    API->>API: authz.enforce_groups(Admins,Operations) OK
    API->>Aurora: SELECT approval_state
    Aurora-->>API: approved
    API->>Aurora: UPDATE state=published
    API-->>AdminUI: state=published

    Note over Admin,Calc: Stage E — Consume
    Calc->>API: GET /v1/unions/704/rate-sheets/2026-01-01
    API->>Aurora: SELECT canonical_json
    Aurora-->>API: rate sheet
    API-->>Calc: canonical JSON (immutable)
```

---

## Per-lesson mapping (cheat sheet)

When you're looking at the master flow and asking "where does X come from?", use this:

| Block in §0 master flow | Lesson | What to open in repo |
|---|---|---|
| PDF upload via Admin UI | 7 (Pattern 4) | `ui/src/admin/Uploads.tsx` + `lib/api.ts` |
| S3 → EventBridge → SFN | 3 (Part 1) | `cdk/laboraid_cdk/stacks/orchestration_stack.py` |
| Classify Lambda | 3 (Stage 1) | `lambdas/processing/classifier/handler.py` |
| Agent enable/disable toggle | 3 (Stage 1a/1b) + 4 (agent-toggle) | `agent-config` DDB + Choice state in `sfn/main_pipeline.py` |
| Strands ExtractorAgent | 2 | `agents/extractor/agent.py` + `steering.py` + `system-prompt.md` |
| Kernel deterministic extraction | 1 + 2 | `kernel/pipeline/{extract,compute,pivot}.py` |
| Validators (3 parallel) | 3 (Stage 3) | `lambdas/validation/{checksum,range,confidence}/handler.py` |
| Render (3 parallel) | 3 (Stage 5) | `lambdas/rendering/{xlsx,csv,articles}-renderer/handler.py` |
| Review queue write | 3 (review path) | `lambdas/validation/review-router/handler.py` |
| Aurora `rate_periods` schema | 4 (Part 7) + 6 (Part 4) | `cdk/assets/schema_init/schema.sql` |
| Business Approve/Reject | 4 + 7 | `ratesheet-{approve,reject}/handler.py` + `ApproveRejectBar.tsx` |
| Publish 409 gate | 4 (Part 2) | `ratesheet-publish/handler.py` |
| Cognito group checks | 4 (Part 1) + 7 (Pattern 2) | `lambdas/api/_shared/python/authz.py` + `RouteGuard.tsx` |
| Calculator consumes published | 4 (state machine) | GET ratesheet Lambda + Aurora SELECT |
| All resources tagged + named | 5 | `cdk/laboraid_cdk/{util/naming,aspects/mandatory_tags,config}.py` |
| Stack composition + deploy order | 5 (Pattern 5) | `cdk/app.py` |

---

## Where to go next

- For depth on any lesson block → [`Learning_Lessons.md`](Learning_Lessons.md)
- For the read-order roadmap → [`Understanding.md`](Understanding.md)
- For the original spec → [`09_Technical_Implementation_Spec.md`](09_Technical_Implementation_Spec.md)
- For the management view → [`CTO_SUMMARY.md`](CTO_SUMMARY.md)
- For audit receipts → [`AUDIT_REPORT.md`](AUDIT_REPORT.md) + [`AUDIT_VERIFICATION.md`](AUDIT_VERIFICATION.md)
- For the same diagrams in a self-contained browser SPA → [`Architecture_Flow.html`](Architecture_Flow.html)
