# Engine Architecture

**Document:** 01 of 7 in `docs/`
**Read after:** `00_README.md`. This is the main architecture doc.

---

## TL;DR

The engine is a **6-stage pipeline** running on **AWS serverless infrastructure** with an **AI-native middle layer powered by Amazon Bedrock**. Inputs are PDFs (CBAs and Rate Notices); outputs are canonical JSON, rendered xlsx/CSV rate sheets, and a per-cell provenance manifest. Each stage is an independently scaling component with a strict input/output schema contract. The AI layer handles the messy parts that deterministic code can't: free-text rule extraction, scanned-image table parsing, semantic citation lookup, and confidence scoring.

> **Status update (2026-06-05) — see [`STATUS.md`](STATUS.md).** The deterministic kernel that backs this pipeline now runs **all 5 POC unions** (537/704/821/483/281) through a CI accuracy gate (≥99% sourced). A **completeness-coverage critic** (`kernel/pipeline/critic.py`) was added as an advisory final stage — it scans the CBA for classifications/zones/funds missing from the output, catching the *missing-breadth* failure mode that value-accuracy can't. Indenture cohorts (281/821) and a Decimal-multiply rounding fix landed in the kernel.

> **⭐ Updated agentic implementation in [doc 07](07_Strands_AgentCore_Agentic_Design.md):** The "AI middle layer" referenced in this doc is concretely implemented as **9 Strands Agents deployed on AWS Bedrock AgentCore Runtime**. Where this doc says "a Bedrock Agent does X", doc 07 names the specific Strands agent (e.g., `ExtractorAgent`, `CBAMinerAgent`), its tools, hooks, **steering** (the `SteeringHandler` pattern that gets Strands to 100% accuracy in their benchmark), and AgentCore service usage (Memory, Gateway, Identity, Policy, Evaluations, Registry). **Read doc 07 for the canonical agent architecture.**

---

## High-level architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                            │
│  ┌──────────────────────┐                                                                 │
│  │ INGESTION SOURCES    │                                                                 │
│  │ • Web upload UI      │      ┌──────────────────────────────────────────────────────┐  │
│  │ • Email→S3 (SES)     │─────▶│  S3: laboraid-inputs/{tenant}/{trade}/{local}/...    │  │
│  │ • Direct API         │      │     (versioned, encrypted, immutable audit copy)     │  │
│  │ • Bulk backfill drop │      └────────────────┬─────────────────────────────────────┘  │
│  └──────────────────────┘                       │ S3 ObjectCreated event                  │
│                                                 ▼                                          │
│                          ┌────────────────────────────────────┐                           │
│                          │  STEP FUNCTIONS state machine       │                           │
│                          │  (one execution per ingested file)  │                           │
│                          └─────────────┬───────────────────────┘                          │
│                                        │                                                   │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                        STAGE 1: INGEST & CLASSIFY                                    │  │
│  │  • Detect file format (PDF text/scan, .doc, .xlsx, image)                           │  │
│  │  • Detect document type (CBA, Rate Notice, Wage Sheet, Reference)                   │  │
│  │  • Detect union, period, scope (Joint Agreement)                                    │  │
│  │  • Group multi-file bundles by effective date                                       │  │
│  │  • [DETERMINISTIC] filename heuristics + folder structure                           │  │
│  │  • [AI FALLBACK] Bedrock Claude when filename/folder doesn't reveal type           │  │
│  └─────────────────────────────┬──────────────────────────────────────────────────────┘  │
│                                │                                                           │
│                                ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                        STAGE 2: EXTRACT (PDF → STRUCTURED)                           │  │
│  │                                                                                      │  │
│  │  ┌────────────────┐    ┌────────────────┐    ┌─────────────────────────────────┐  │  │
│  │  │ Text-PDF path  │    │ Scan-PDF path  │    │ Multi-modal Claude path          │  │  │
│  │  │ • pdftotext    │    │ • Textract /   │    │ • Bedrock Claude reads PDF       │  │  │
│  │  │ • pdfplumber   │    │   Tesseract    │    │   directly (vision-capable)      │  │  │
│  │  │ • Camelot      │    │   for OCR +    │    │ • Structured JSON output         │  │  │
│  │  │   for tables   │    │   tables       │    │ • Used for messy or low-conf     │  │  │
│  │  └───────┬────────┘    └────────┬───────┘    └────────────┬────────────────────┘  │  │
│  │          │                      │                          │                       │  │
│  │          └──────────────────────┴──────────────────────────┘                       │  │
│  │                                 │                                                   │  │
│  │                                 ▼                                                   │  │
│  │  Structured "ExtractedDocument" JSON (one per input file)                          │  │
│  │  → Persisted to S3 outputs/{job_id}/extracted.json                                  │  │
│  └─────────────────────────────┬──────────────────────────────────────────────────────┘  │
│                                │                                                           │
│                                ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                STAGE 3: CBA RULE MINING (one-time per CBA, lazy)                     │  │
│  │  • Sync CBA into Bedrock Knowledge Base (S3 Vectors-backed)                         │  │
│  │  • Bedrock Agent extracts structured rules:                                         │  │
│  │      Foreman premium, Apprentice ladder, Pension exclusion, OT rules, Funds,       │  │
│  │      Rounding, Conditional rules ("As per Area Practice")                           │  │
│  │  • Cross-validates against Profile (if Profile exists)                              │  │
│  │  • Produces RuleManifest JSON with article-level citations                          │  │
│  │  • Cached so subsequent Rate Notices in same period don't re-mine                   │  │
│  └─────────────────────────────┬──────────────────────────────────────────────────────┘  │
│                                │                                                           │
│                                ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                STAGE 4: RULE RESOLUTION (apply Profile to extracted values)         │  │
│  │  • Load Union Rule Profile from S3                                                  │  │
│  │  • Load CBA RuleManifest from S3                                                    │  │
│  │  • For each (Zone × Package × Dimension) row:                                       │  │
│  │       1. Compute wage from formula DSL (e.g., "zone_jw + 4.50")                     │  │
│  │       2. Compute derived columns (Wage 1.5x, Temp Heat = Wage × 0.6, etc.)          │  │
│  │       3. Apply per-class fringe scaling                                             │  │
│  │       4. Apply alt-fund routing (e.g., Production Worker → H&W Metal)               │  │
│  │       5. Apply exclusion zero-outs (Y1 → Pension = 0)                               │  │
│  │       6. Apply rounding rule                                                        │  │
│  │  • Tag each cell with provenance (source + citation)                                │  │
│  │  • Output: CanonicalRateSheet JSON                                                  │  │
│  └─────────────────────────────┬──────────────────────────────────────────────────────┘  │
│                                │                                                           │
│                                ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                STAGE 5: VALIDATION (quality gate)                                    │  │
│  │  • Total package checksum (sum of components vs Notice's printed total)             │  │
│  │  • Apprentice % cross-check (computed vs published)                                 │  │
│  │  • Range checks (wages within plausible range)                                      │  │
│  │  • Year-over-year delta sanity (flag >20% changes unless Article-20 explained)      │  │
│  │  • Confidence scoring per cell                                                      │  │
│  │  • [AI ASSIST] Bedrock Claude reviews suspicious cells for sanity                   │  │
│  │  • Branch: HIGH-confidence → auto-publish; LOW-confidence → human review queue     │  │
│  └─────────────────────────────┬──────────────────────────────────────────────────────┘  │
│                                │                                                           │
│                                ▼                                                           │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                STAGE 6: RENDER & PUBLISH                                             │  │
│  │  • Render xlsx + CSV per Profile layout convention                                  │  │
│  │  • Auto-populate Articles sheet/file from provenance                                │  │
│  │  • Persist canonical JSON + rendered files to S3 outputs                            │  │
│  │  • Update Aurora rate_periods + rate_cells tables                                   │  │
│  │  • Emit EventBridge event: laboraid.rate-sheet.published                            │  │
│  │  • LaborAid auto-rate engine consumes via API                                       │  │
│  └─────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                            │
└──────────────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────────────────┐
│   CROSS-CUTTING SERVICES                                                                  │
│                                                                                           │
│   • DynamoDB:           job state, idempotency, review queue, cadence reminders          │
│   • Aurora Postgres:    Profiles, published rate sheets, audit log, provenance index     │
│   • Bedrock Claude:     extraction fallback, validation review, citation generation      │
│   • Bedrock Agents:     CBA rule mining, multi-step orchestration                        │
│   • Bedrock KB + S3 Vectors: CBA corpus semantic search                                  │
│   • CloudWatch / X-Ray: tracing, metrics, alarms                                         │
│   • CloudTrail:         immutable audit trail                                            │
│   • KMS:                encryption (CMK per env)                                         │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component breakdown

### A. Ingestion Layer

**Sources**
- **Web upload UI** (CloudFront + S3 + React SPA) — primary path for the rate-data ops admin
- **Email-to-S3** via SES — admin can forward union emails to a dedicated address; SES extracts attachments and writes to inputs bucket
- **Direct API** (API Gateway + Lambda) — for programmatic uploads (e.g., union representatives self-uploading via LaborAid product portal in v2)
- **Bulk backfill** — drop a folder of historical Notices into S3 prefix, engine processes all

**Storage**
- `s3://laboraid-inputs-{env}/{tenant}/{trade}/{local}/{period}/{filename}` — versioned, KMS-encrypted, lifecycle to Glacier after 1 year (immutable archive)

**Triggering**
- S3 ObjectCreated event → EventBridge rule → Step Functions execution

### B. Orchestration Layer

**Step Functions state machine** orchestrates the 6 stages. Why Step Functions:
- Visual debugging (each stage's input/output preserved in execution history)
- Built-in retry/backoff per state
- Error handlers and Catch blocks for graceful degradation
- Native X-Ray and CloudWatch integration
- Easy to add/remove stages without rewiring

**State machine type:** Standard (not Express) — pipelines run minutes to tens of minutes, not milliseconds. Standard is appropriate for long-running with high audit value.

**Idempotency:** Each input file's S3 key + content hash is the idempotency key. Re-running the same file produces the same output (deterministic engine).

### C. Storage Layer

| Service | Use |
|---|---|
| **S3 inputs** | Raw uploaded files |
| **S3 outputs** | Canonical JSON + rendered xlsx/CSV + Articles file |
| **S3 profiles** | Union Rule Profiles (versioned) |
| **S3 vectors** (Bedrock Knowledge Base storage) | Vector embeddings of CBA chunks for RAG |
| **S3 manifests** | Extracted document JSON, RuleManifests (intermediate artifacts) |
| **DynamoDB `laboraid-jobs`** | Step Function execution state, retry counters |
| **DynamoDB `laboraid-files`** | File metadata (type, status, hash, ingest time) |
| **DynamoDB `laboraid-review-queue`** | Cells awaiting human review |
| **DynamoDB `laboraid-cadence`** | Expected next-Notice dates per union |
| **DynamoDB `laboraid-overrides`** | Manual cell overrides log |
| **Aurora Postgres** | Profiles (relational queries, history), published rate sheets (canonical JSON in JSONB column + structured rate_cells table), audit log, provenance index for fast lookups |
| **Secrets Manager** | DB creds, API keys, OAuth secrets — auto-rotated |

### D. Compute Layer

| Service | Use case |
|---|---|
| **Lambda (Python, ARM64)** | Stage 1 (classify), Stage 4 (resolve), Stage 5 (validate), Stage 6 (render). Short-running, parallelizable, scale-to-zero. |
| **Fargate (ECS)** | Stage 2 (extract) for large/scanned PDFs that exceed Lambda's 15-minute limit. Stage 3 (CBA rule mining) for 35+ page CBAs. |
| **Step Functions Standard workflows** | Orchestration |
| **Bedrock model invocations** | Claude (text + vision), Claude Haiku for cheap classification |
| **Bedrock Agents** | Stage 3 CBA rule mining; multi-step extraction with tool use |
| **Bedrock Knowledge Base + S3 Vectors** | CBA corpus retrieval for citation lookup |

### E. AI Layer (Bedrock)

This is the layer where the design departs most from what we'd build before generative AI:

**Bedrock Claude (Sonnet for accuracy, Haiku for cheap pre-classification):**
- Universal fallback PDF parser (multi-modal: reads PDF directly, no OCR step needed)
- Free-text CBA rule extraction (e.g., parse "Foreman shall be paid a minimum of $2.50 over the Journeyman Rate" into `{type: foreman_premium, amount: 2.50, base: journeyman}`)
- Confidence scoring on ambiguous extractions
- Citation generation (given a value, find which CBA article supports it)
- Manual review assistance (suggest the most likely correct value when human is reviewing)

**Bedrock Agents:**
- Stage 3 CBA rule mining is an agentic workflow:
  1. Agent receives the Profile draft + freshly-extracted CBA text
  2. For each rule type the Profile expects, agent searches KB for relevant CBA passages
  3. Agent calls extraction tool (Lambda) to convert passage → structured rule
  4. Agent validates the rule against expected schema
  5. Agent loops until all rules extracted or max attempts reached
  6. Returns RuleManifest

**Bedrock Knowledge Bases + S3 Vectors:**
- One Knowledge Base per union (or one global with metadata filter on union_local)
- CBA chunks (page-level + section-level) embedded with Titan Embed v2 or Cohere Embed
- S3 Vectors as the storage backend (cheaper than OpenSearch Serverless for this workload)
- Used for:
  - Citation lookup: "find the CBA passage that justifies this $4.50 Foreman premium"
  - Profile authoring: "find the apprentice schedule article in this CBA"
  - Cross-CBA Q&A for ops admin (UI feature: "ask the CBA")

> **Why S3 Vectors over OpenSearch:** S3 Vectors targets sparse-query use cases at low cost. Our queries are sparse (a few hundred per day max) and our corpus is bounded (~500 CBAs at full scale). OpenSearch Serverless's minimum spend is overkill.

**Bedrock model selection per task:**

| Task | Model | Rationale |
|---|---|---|
| File classification | Claude Haiku (or Nova Lite) | Fast, cheap, simple categorization |
| Multi-modal PDF extraction | Claude Sonnet (4.x) | Best vision + structured-output combo |
| CBA rule extraction | Claude Sonnet | Reasoning over long context (35+ pages) |
| Citation generation | Claude Haiku | Quick semantic match given KB result |
| Confidence review of suspicious cells | Claude Sonnet | Reasoning task |
| Embedding generation for KB | Titan Embed Text v2 | Cost-effective; good for English technical text |

### F. API Layer

**API Gateway HTTP APIs** + Lambda authorizer (Cognito JWT):

| Endpoint | Method | Purpose |
|---|---|---|
| `/uploads` | POST | Generate presigned S3 URL |
| `/jobs/{id}` | GET | Job status + result |
| `/unions` | GET, POST | List + create unions |
| `/unions/{id}/profile` | GET, PUT | Read/update Profile YAML |
| `/unions/{id}/rate-sheets` | GET | List published periods |
| `/unions/{id}/rate-sheets/{period}` | GET | Canonical JSON for one period |
| `/unions/{id}/rate-sheets/{period}/publish` | POST | Approve and publish |
| `/cells/{cell_id}` | GET | Cell value + provenance |
| `/cells/{cell_id}/override` | POST | Manual override |
| `/cba/{union_id}/ask` | POST | Ask the CBA (Bedrock KB-backed Q&A) |

LaborAid product also calls these endpoints (via service-to-service IAM role) to consume canonical JSON.

### G. UX Layer

**Admin SPA** (React, hosted on S3 + CloudFront):
- File upload + status dashboard
- Per-period rate-sheet review (side-by-side: PDF + extracted values + provenance)
- Profile editor (form-based + raw YAML view)
- Review queue (low-confidence cells)
- Year-over-year diff view
- Audit log viewer

**Ask-the-CBA chat UI:** ops admin can type questions like *"What's the apprentice ratio rule in 821's CBA?"* and get answers with citations. Powered by Bedrock KB + Claude.

### H. Observability & Audit

- **CloudWatch Logs** — all Lambdas + Fargate, structured JSON
- **X-Ray** — distributed tracing across Step Function → Lambda → Bedrock invocations
- **CloudWatch Metrics** — custom metrics (`AutoExtractSuccessRate`, `MeanTimeToPublish`, `LowConfidenceCellsPercent`, `BedrockTokensSpent`)
- **CloudTrail** — admin actions, IAM access, KMS use
- **EventBridge custom bus** — pipeline events for downstream consumers (Slack notifications, audit warehouse)

---

## End-to-end data flow (concrete example)

**Scenario:** Ops admin uploads `2026.07.01.704 Rate Notice.pdf` (a hypothetical future Notice for Sprinkler Local 704).

### 1. Ingest (T+0s)
- Admin drags file into web UI
- UI calls `POST /uploads` → presigned URL → admin's browser PUTs file to `s3://laboraid-inputs-prod/laboraid/Sprinkler/704/2026-07-01/2026.07.01.704 Rate Notice.pdf`
- S3 event → EventBridge → Step Function execution starts

### 2. Classify (T+2s)
- Lambda `classify` runs
- Filename pattern matches `^(\d{4}\.\d{2}\.\d{2})\.(\d+) (Rate Notice|Wage Notice|Wage Rate Notice)\.pdf$`
- Identifies: union=704, period=2026-07-01, type=Rate Notice
- Folder structure confirms: trade=Sprinkler
- Profile lookup: 704 Profile exists in S3 → use it
- Status written to DynamoDB `laboraid-jobs`: `classified`

### 3. Extract (T+8s)
- Step Function chooses extraction path: this file is text-extractable PDF (~280 KB, 1 page)
- Lambda `extract_text_pdf` runs:
  - Calls `pdftotext -layout`
  - Parses labeled-money table with regex + column-aware parser
  - Produces ExtractedDocument JSON:
    ```json
    {
      "document_type": "rate_notice",
      "union_local": 704,
      "effective_start": "2026-07-01",
      "effective_end": null,
      "anchor_wages": { "Journeyman": 53.92 },
      "fringes": {
        "Health & Welfare": 13.95,
        "RESA": 1.35,
        "Pension": 7.45,
        "SIS": 11.50,
        ...
      },
      "deductions": { ... },
      "foreman_premium_text": "Foreman - $4.50 over -",
      "ot_rates": { "1.5x": 78.40, "2.0x": 102.85 },
      "extraction_confidence": 0.97
    }
    ```
- For comparison: if it had been an image-only PDF, would call Bedrock Claude with the PDF as a multi-modal input, getting the same JSON shape back. The downstream code doesn't care which path was used.

### 4. CBA Rule Mining (T+8s — **CACHE HIT**, skipped)
- Step Function checks: is there a current RuleManifest for 704's CBA?
- Aurora returns: yes, version 2026-04-15, hash matches the CBA file in S3
- Skip Stage 3, proceed to resolution

### 5. Resolve (T+10s)
- Lambda `resolve_rules` runs:
  - Loads `profile_704.yaml` from S3
  - Loads `rule_manifest_704.json` from Aurora
  - Reads ExtractedDocument from S3
  - Materializes (Zone × Package) row matrix:
    - 1 zone (Building) × 13 packages (GF, F, JW, App10..App1) = 13 rows
  - For each row, evaluates wage formula:
    - JW row: `wage = anchor.Journeyman = 53.92`
    - Foreman row: `wage = JW + 4.50 = 58.42`
    - GF row: `wage = Foreman + 2.00 = 60.42`
    - App10 row: `wage = JW × 0.85 = 45.83` (rounded)
    - ...
  - For each row, materializes derived columns (Wage 1.5x, Wage 2.0x, Wage Differential)
  - Applies per-class fringe scaling for S&E, Craft, Union Dues, Retiree Holiday
  - Applies Class 1 zero-out: Pension=0, SIS=0
  - Applies H&W split: column = Notice H&W − RESA = 13.95 − 1.35 = 12.60
  - Tags each cell with provenance (see doc 05)
  - Outputs CanonicalRateSheet JSON

### 6. Validate (T+12s)
- Lambda `validate` runs:
  - Total package checksum: sum of (wage + fringes) per JW row vs Notice's printed Total Package = ✓ matches
  - Apprentice % checks: 0.85 × 53.92 = 45.83 = rate sheet App10 ✓
  - Range check: JW $53.92 within plausible range ✓
  - Year-over-year delta: previous period (2026-01-01) had JW $52.32; new period $53.92 = +$1.60 (+3.1%) — within sanity threshold
  - All cell confidences > 0.95
  - Branch: AUTO-PUBLISH

### 7. Render & Publish (T+18s)
- Lambda `render` runs:
  - Looks up 704 Profile's output_layout: `multi_sheet_workbook`
  - Loads existing `2022-2027.704 Rate Sheet.xlsx` from outputs bucket
  - Adds new sheet `2026.12.31` (named by end-date per Profile convention)
  - Renders 13 rows × 24 columns from CanonicalRateSheet
  - Updates Articles sheet with new period's citations
  - Writes back to S3 outputs
  - Also writes:
    - `2026.07.01.704 Rate Sheet.csv` (CSV mirror)
    - `2026.07.01.704 canonical.json` (canonical JSON for LaborAid product)
- Aurora updated: new `rate_periods` row + 13 × 24 = 312 `rate_cells` rows with provenance
- EventBridge event emitted: `laboraid.rate-sheet.published`
- SES email to ops admin: "Local 704 rate sheet for 2026.07.01-2026.12.31 published"
- LaborAid product polls API and starts using new rates from effective date

**Total elapsed time: ~18 seconds for a clean text-PDF Notice.** Most of which is Lambda cold start + S3 round-trips. Bedrock not used in this happy path because the Profile + RuleManifest pre-existed and the Notice was clean.

---

## Variation: image-only Rate Notice (the 704-style scanned case)

Scenario: same file as above, but it's a 12-page scanned bundle (704's annual notice format).

### Stage 2 — Extract changes
- Step Function detects: text extraction yields <100 chars total → image PDF
- Branches to Bedrock Claude path
- Lambda `extract_with_claude` runs:
  - Reads PDF bytes from S3
  - Invokes Bedrock Claude Sonnet 4.x with multi-modal input
  - Prompt: structured JSON schema + the 12-page PDF bundle
  - Claude returns: anchor wage + fringes + per-class apprentice schedule (10 classes) + deductions + ot rates
  - Confidence on each field (Claude returns these too)

### Stage 5 — Validate changes
- Some confidence values are 0.85 (Claude was less sure on a few apprentice fund values)
- Profile threshold for auto-publish: 0.95
- 3 cells fall below → routed to human review queue
- Email + UI notification sent

### Human review (T+5min)
- Admin opens review UI
- Sees PDF page 7 (Class 5 detail) next to the 3 cells in question
- Confirms or corrects values
- Engine re-validates → all green → publish

**Variation is 100% in Stage 2 and Stage 5 — Stages 1, 3, 4, 6 don't change.**

---

## Why this architecture works

| Pressure | How design responds |
|---|---|
| Inputs are highly heterogeneous (5+ formats, multi-page bundles, image PDFs) | Hybrid extraction layer with Claude as universal fallback |
| Per-union variability (24 dimensions) | Profile-driven config; one engine, many configs |
| CBAs are long (35-50 pages) and rules are spread across them | Bedrock Knowledge Base + S3 Vectors for semantic retrieval |
| Audit/citation traceability is mandatory | Per-cell provenance baked into every stage's output schema |
| Schema drifts over time | Canonical JSON output is map-based; xlsx renderer projects |
| Errors must be caught before publish | Multi-layer validation + LLM sanity review for low-confidence cells |
| New unions onboard within 3 days | Profile authoring assisted by AI (CBA rule mining draft) |
| Cost matters at scale | Serverless, scale-to-zero; KB/Bedrock used only when deterministic fails |
| Compliance (SOC 2) | All AWS services chosen are compliant; encryption + audit log + immutable inputs |

---

## What's deliberately NOT in v1

To keep v1 focused:
- ❌ Multi-tenant federation (v1 is LaborAid single-tenant)
- ❌ Direct union/trustee uploads (v1 is internal LaborAid ops only)
- ❌ Real-time collaborative editing of Profiles
- ❌ Full Profile-from-scratch auto-generation (v1 has AI-assisted draft + human polish)
- ❌ Mobile UI (web admin only)
- ❌ Multi-language CBAs (v1 assumes English)

These are v2+ items.

---

## Next docs in this folder

- `02_Parser_Stages.md` — detailed stage internals, including format detection logic, OCR fallback, table parsing
- `03_Bedrock_AI_Layer.md` — deeper dive on Claude prompts, Agent design, KB ingestion pipeline
- `04_Schemas_and_DSL.md` — concrete JSON schemas for ExtractedDocument, RuleManifest, CanonicalRateSheet, Profile YAML
- `05_Provenance_and_Citations.md` — provenance tag spec + citation generation pipeline
- `06_Implementation_Plan.md` — 8-week build plan with tasks, owners, milestones
