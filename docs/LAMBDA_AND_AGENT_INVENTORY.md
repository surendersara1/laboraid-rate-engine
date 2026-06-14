# Lambda & Agent Inventory

*Authoritative breakdown of every deployed Lambda function, the API routing, the
processing pipeline, and the AI agents (their tools, prompts, steering, and models).
Generated from the live AWS account `908106425069` (us-east-2) and the source tree.*

**40 Lambda functions** are deployed. Names follow a layer convention:

| Tag | Layer | What lives here |
|---|---|---|
| `l2` | **API** | One Lambda per HTTP route (behind API Gateway + Cognito) |
| `l3` | **Infra / read-model** | Schema init, the jobs read-model writer |
| `l4` | **Processing pipeline** | The Step Functions extraction stages + onboarding |
| `l6` | **Validation / notify** | Checksum/range/confidence validators, routing, Slack |
| `l7` | **Rendering** | CSV / Excel / articles output renderers |

> **Active vs. legacy.** The production extraction path today is **Plan → Synthesize →
> Publish** (3 Lambdas: `batch-planner`, `synthesizer`, `synth-publish`). The `l6`
> validators and `l7` renderers are from the original layered spec design; they remain
> deployed but are **not** on the current synthesizer pipeline. `extractor-invoker` and
> the per-union kernel extractors run inside the **ExtractorAgent** container, not as a
> standalone Lambda. These are flagged below.

---

## 1 · API Lambdas (`l2`) — one per HTTP route

Every route is behind API Gateway (HTTP API) and Cognito; the persona (Admin / Business)
is enforced per route. Source: `lambdas/api/<name>/handler.py`.

| Function | HTTP route | Purpose | Persona |
|---|---|---|---|
| `l2-fn-upload-presign` | `POST /v1/uploads` | Issue a presigned S3 URL to upload PDFs | Admin |
| `l2-fn-batch-process` | `POST /v1/batches/process` | Kick off processing for an uploaded batch | Admin |
| `l2-fn-job-list` | `GET /v1/jobs` | List pipeline runs (reads the **jobs DynamoDB read-model**) | Admin/Ops |
| `l2-fn-job-status` | `GET /v1/jobs/{id}` | One job's stage-by-stage timeline (read-model) | Admin/Ops |
| `l2-fn-job-retry` | `POST /v1/jobs/{id}/retry` | Re-run a failed execution | Admin/Ops |
| `l2-fn-job-abort` | `POST /v1/jobs/{id}/abort` | Cancel an in-flight execution | Admin |
| `l2-fn-agent-list` | `GET /v1/agents` | Read AI agent on/off config (agent-config DDB) | Admin/Ops |
| `l2-fn-agent-toggle` | `PATCH /v1/agents/{name}` | Enable/disable an agent, pin image version | Admin |
| `l2-fn-profile-list` | `GET /v1/unions`, `…/{local}/profile` | List unions / get a union's profile (schema) | Admin |
| `l2-fn-profile-update` | `PUT /v1/unions/{local}/profile` | Edit the union profile in Aurora (`unions.profile_yaml`) | Admin |
| `l2-fn-ratesheet-list` | `GET …/rate-sheets` | List rate periods by approval state | Business |
| `l2-fn-ratesheet-get` | `GET …/rate-sheets/{period}` | Canonical JSON + approval state + artifact URLs + job meta + **AI-improvement change log** | Business |
| `l2-fn-ratesheet-approve` | `POST …/approve` | Business sign-off (requires empty review queue) | Business (2nd person) |
| `l2-fn-ratesheet-reject` | `POST …/reject` | Reject with a required reason | Business |
| `l2-fn-ratesheet-unapprove` | `POST …/unapprove` | Original approver reverses, before publish | Business |
| `l2-fn-ratesheet-publish` | `POST …/publish` | **Gated** publish — 409 unless `approved` | Business |
| `l2-fn-ratesheet-audit` | `GET …/audit` | Full audit trail for a rate sheet | Business |
| `l2-fn-ratesheet-rework` | `POST …/rework` | Create a new version from edits (Tier-3 rework) | Business |
| `l2-fn-ratesheet-improve` | `POST …/improve` | **Phase 2** — records an improvement run over open corrections, async-dispatches the **ImproverAgent**, returns run id | Business |
| `l2-fn-cell-override` | `POST /v1/cells/{id}/override` | Save a human-corrected cell value (→ Aurora `cell_corrections` + audit) | Business |
| `l2-fn-cell-comment` | `POST /v1/cells/{id}/comment` | Save a reviewer comment on a cell (→ `cell_corrections` + audit) | Business |
| `l2-fn-audit-list` | `GET /v1/audit` | System-wide audit feed | Admin |

---

## 2 · Infra / read-model Lambdas (`l3`)

| Function | Trigger | Purpose |
|---|---|---|
| `l3-fn-schema-init` | CloudFormation custom resource | Idempotently applies the Aurora DDL (tables incl. `cell_corrections`, `improvement_runs`, `improvement_changes`); re-runs on schema-version bump |
| `l3-fn-job-writer` | **EventBridge** (Step Functions status change) | Projects each execution's state into the `jobs` DynamoDB table — the dashboard/Jobs **read-model** (CQRS). Calls `GetExecutionHistory` once at write-time to build the per-stage timeline |

---

## 3 · Processing pipeline Lambdas (`l4`)

The live Step Functions state machine `l3-sfn-main` runs **Plan → Synthesize → Publish**:

```
batch-planner  ─►  synthesizer  ─►  synth-publish
   (Plan)          (Synthesize)        (Publish)
```

| Function | Stage | Purpose | Status |
|---|---|---|---|
| `l4-fn-batch-planner` | **Plan** | Classify + order the PDFs, resolve union + rate period | ✅ active |
| `l4-fn-synthesizer` | **Synthesize** | Core LLM step — Claude reads ALL docs against the union profile → the rate sheet (see §5.4) | ✅ active |
| `l4-fn-synth-publish` | **Publish** | Write synthesized rows to Aurora, store cohorts as dimensions, record source lineage, emit artifacts | ✅ active |
| `l4-fn-profile-builder` | onboarding | Build a union's profile (structure only) from its CBA — auto-onboard (see §5.3) | ✅ active |
| `l4-fn-classifier` | — | Document classifier (CBA vs rate notice) — original layered pipeline | legacy/spec |
| `l4-fn-ocr-preprocess` | — | OCR pre-processing of scanned PDFs — original layered pipeline | legacy/spec |
| `l4-fn-llm-extractor` | — | Per-cell LLM extractor — original layered pipeline | legacy/spec |
| `l4-fn-publisher` | — | Writes a kernel/agent extraction into Aurora — original layered pipeline | legacy/spec |

---

## 4 · Validation (`l6`) & Rendering (`l7`)

From the original layered spec design; deployed but not on the current synthesizer path
(the synthesizer + `synth-publish` enforce schema and emit artifacts directly).

| Function | Purpose |
|---|---|
| `l6-fn-validator-checksum` | Verify wage + fringes = printed Total Package (±$0.05) |
| `l6-fn-validator-range` | Flag values outside plausible ranges |
| `l6-fn-validator-confidence` | Roll up per-cell confidence; route low-confidence cells to review |
| `l6-fn-review-router` | Route a sheet to the human review queue |
| `l6-fn-slack-notify` | Slack notifications on pipeline events |
| `l7-fn-renderer-csv` | Render canonical CSV |
| `l7-fn-renderer-xlsx` | Render Excel in the client's standard layout (Dan's SOP §5) |
| `l7-fn-renderer-articles` | Render the "articles" view |

---

## 5 · The AI agents

Three AI components. Two are **Strands agents on Bedrock AgentCore** (long-running
containers); the synthesizer is an **LLM-in-Lambda**. Every one obeys the prime directive:
**never fabricate — extract from source or flag a gap.** All Bedrock calls pass through a
**PII guardrail** (`BEDROCK_GUARDRAIL_ID`) that masks personal data before the model sees it.

### 5.1 · ExtractorAgent — Strands on AgentCore *(runtime `laboraid_dev_l5_agent_extractor`, v11)*

Turns a union's Rate Notice + CBA PDFs into a canonical rate sheet by orchestrating a
deterministic **kernel** and escalating to a multi-modal LLM only for cells the kernel
can't read. Model: **Claude Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6`).

**Tools (Strands `@tool`):**
| Tool | Role |
|---|---|
| `kernel_extract_to_csv_s3` | Preferred fast-path — stage + extract + compute + pivot + upload + checksum in one call (unions with a kernel extractor: 704, 483, 537, 281, 821) |
| `stage_inputs_from_s3` | Download the union's PDFs into the kernel's expected layout |
| `run_kernel_extractor` | Run the union's deterministic extractor (pdfplumber → rapidocr) |
| `extract_via_claude_only` | Generic LLM extractor for unions **without** a kernel (Path C) |
| `compute_derived_columns` | Apply the kernel's half-up-rounded derived-column rules |
| `escalate_to_claude_multimodal` | Per-cell fallback — send the raw PDF to Bedrock for ONLY the missing fields (Path B) |
| `pivot_to_ratesheet_csv` | Write the final CSV matching the groundtruth header |
| `validate_total_package_checksum` | Verify wage + fringes = Total Package (±$0.05) |

**Routing logic (Paths A/B/C):** A = deterministic kernel (preferred); B = kernel + per-cell
Bedrock escalation for unreadable cells; C = pure-LLM for unseen unions with no kernel.

**Steering policy** (`ExtractorSteering`): blocks the agent from declaring "complete" until
(a) the Total-Package checksum has been validated **and** (b) it has attempted the Bedrock
multi-modal fallback for any kernel gaps. Enforces the self-validation contract.

**System prompt (essence):** *"You MUST NOT invent, guess, or interpolate any rate value.
Every number MUST trace to a source… A blank-and-flagged cell is correct; a fabricated cell
is a defect. Prefer the kernel; escalate to Bedrock only for specific unreadable cells."*

### 5.2 · ImproverAgent — Strands on AgentCore *(runtime `laboraid_dev_l5_agent_improver`, v4)* — **Phase 2**

Applies a business reviewer's open corrections to a rate sheet and produces a **new version
(v+1)**. Model: **Claude Opus 4.5** (`us.anthropic.claude-opus-4-5`).

**Two correction paths, in one pass (`_process`):**
- **Override** (human-set value) → applied verbatim, then **all derived columns recomputed
  deterministically** in code (`rate_math.recompute_derived`) — no LLM. `source=override` /
  `recompute`.
- **Comment** (reviewer flagged a cell) → `_resynthesize`: sends *only that cell* + the
  reviewer's comment + the **source PDF text** to Bedrock, asking for strict JSON
  `{value, provenance, confidence}`, `temperature=0`. `source=resynth`.

**Anti-fabrication discipline:** the re-synthesis prompt says *"If the source does not
support a value, return null (do not invent one)."* If it returns null, the agent **keeps
the prior value**, lowers confidence, and attaches a note — it never nulls a present number
or invents one to satisfy a comment.

**Output:** writes v+1 cells to Aurora + one `improvement_changes` row per changed cell
(prior→new, source, provenance, confidence) — the **"what the agent changed" change log**.
New version lands `pending_review`; a human still approves.

### 5.3 · ProfileDrafter / profile-builder — onboarding LLM

Onboards an unseen union from its CBA. Model: **Claude Opus 4.5**. Extracts the rate-sheet
**structure only — never dollar values**: zones, classifications, **indenture cohorts**,
overtime/derived multipliers, and fund columns **mapped to the client's canonical names**.
Saves to Aurora (`unions.profile_yaml`). This is what lets a new union onboard with **no code
changes**. The synthesizer auto-invokes it on first sight of an unknown union.

**Objective prompt (essence):** *"From the attached CBA — and any rate notice — extract:
ZONES, CLASSIFICATIONS, INDENTURE COHORTS, FUND COLUMNS, and overtime/derived MULTIPLIERS.
Each fund MUST be the canonical name from the lists below, not your own descriptive label."*

### 5.4 · Synthesizer — LLM-in-Lambda (the production extraction core)

Not an agent container — a Lambda that calls Bedrock. Model: **Claude Sonnet 4.6** (default).
Loads the union **profile from Aurora** and renders it as the model's **exact TARGET SCHEMA**
(canonical fund/column names, packages, zones, cohorts, derived multipliers). Claude reads
**all documents together** against that schema in one reasoning pass — so it can reason about
precedence (a current rate notice supersedes the CBA), cohorts, and fund naming. **Derived /
overtime columns are then computed in code**, not by the model. If no profile exists, it
auto-onboards via `profile-builder` first.

---

## 6 · Models & guardrail at a glance

| Component | Model | Why |
|---|---|---|
| Synthesizer (pipeline) | Claude **Sonnet 4.6** | High-volume multi-doc extraction |
| ExtractorAgent | Claude **Sonnet 4.6** | Multi-modal per-cell fallback |
| ImproverAgent | Claude **Opus 4.5** | Careful single-cell re-synthesis / reasoning |
| ProfileDrafter | Claude **Opus 4.5** | Structural reasoning over a full CBA |
| **All Bedrock calls** | — | **PII guardrail** masks personal data before the model sees it |

## 7 · Trust invariants (enforced across all of the above)

- **Never fabricate** — every value traces to a source PDF, a deterministic rule, or a human
  override; unresolved cells are flagged as gaps, not guessed.
- **Determinism where it counts** — derived/overtime arithmetic is computed in code
  (`rate_math`, half-up rounding), never left to the model.
- **Two-person control** — review and approval require two different people; the database
  enforces it; publish is gated on `approved`.
- **Full provenance + audit** — every extraction, comment, override, AI improvement, review,
  approval, and publish is recorded in Aurora.
