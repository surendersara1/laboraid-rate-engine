# System Inventory — Agents, Lambdas, Kernel (2026-06-12)

Three compute layers. The "39" everyone counts is only layer 2.

| Layer | What | Count | Runs on |
|---|---|---|---|
| 1 | **Strands Agent** | 1 (ExtractorAgent) | Bedrock **AgentCore** (container, from ECR) |
| 2 | **Lambda functions** | 39 | AWS Lambda |
| 3 | **Kernel** (deterministic engine) | library (not deployed alone) | imported by the agent's tools + (legacy) llm-extractor |

---

## Layer 2 — the 39 Lambdas, by CDK stack

**Status legend:** ✅ active in today's flow · 🟥 legacy (nothing calls it) · ⚙️ infra (not request-path but required)

### Api stack — l2 — 21 functions (all ✅, all called by the React UI via API Gateway+JWT)
| Function | Route / caller |
|---|---|
| upload-presign | `POST /v1/uploads` |
| batch-process | `POST /v1/batches/process` → **starts Step Functions** |
| job-list / job-status / job-retry / job-abort | `/v1/jobs*` |
| agent-list / agent-toggle | `/v1/agents*` |
| profile-list / profile-update | `/v1/unions*/profile` |
| ratesheet-list / ratesheet-get / ratesheet-audit | `/v1/unions/{local}/rate-sheets*` |
| ratesheet-approve / reject / unapprove / publish / rework | dual-control routes |
| cell-override / cell-comment | `/v1/cells/{id}/*` |
| audit-list | `/v1/audit` |

### Processing stack — l4 — 8 functions
| Function | Status | Caller / role |
|---|---|---|
| batch-planner | ✅ | SFN state `Plan`; invokes **classifier** |
| classifier | ✅ | invoked by batch-planner |
| synthesizer | ✅ | SFN state `Synthesize`; invokes **profile-builder** (uses synth-deps layer) |
| profile-builder | ✅ | invoked by synthesizer (auto-onboard) (uses synth-deps layer) |
| synth-publish | ✅ | SFN state `SynthPublish` |
| llm-extractor | 🟥 | old per-doc extractor (→ replaced by synthesizer); calls **kernel** |
| ocr-preprocess | 🟥 | old Textract pre-step |
| publisher | 🟥 | old Aurora writer (→ replaced by synth-publish) |

> Processing stack ALSO holds the **AgentCore runtime** (ExtractorAgent) + ECR repo +
> agent-extractor IAM role — see Layer 1. Not a Lambda.

### Orchestration stack — l3 — 1 function
| Function | Status | Note |
|---|---|---|
| extractor-invoker | 🟥 | Lambda→AgentCore bridge for the old flow. **Removed from CDK source → deleted on Orchestration deploy.** |

### Storage stack — l3 — 1 function
| Function | Status | Note |
|---|---|---|
| schema-init | ⚙️ | one-shot Aurora DDL bootstrap. Keep. |

### Validation stack — l6 + l7 — 8 functions (all 🟥, none called by the new flow)
| Function | Was |
|---|---|
| checksum / confidence / range | pre-publish validators (wage+fringe=Total±$0.05, rollup, bounds) |
| review-router | confidence-based routing to human review |
| slack-notifier | notifications |
| xlsx-renderer / csv-renderer / articles-renderer | output renderers (synthesizer now emits CSV/XLSX itself) |

**Count check:** 21 + 8 + 1 + 1 + 8 = **39** = **26 active** (21 API + 5 pipeline) + **13 not-in-flow** (12 legacy 🟥 + 1 infra ⚙️ schema-init).

---

## Layer 1 — the Strands Agent (`agents/extractor/`)

- **ExtractorAgent** — a Strands `Agent` packaged as a container (`agents/extractor/Dockerfile`),
  pushed to ECR, run on **Bedrock AgentCore** (CDK `StrandsAgentRuntime` construct in the
  Processing stack). Invoked by the old `extractor-invoker` Lambda.
- **Status:** deployed, but **NOT in the active synthesizer flow** today. It is the
  **Strands-on-AgentCore foundation for the Phase 2 agentic reviewer** — keep/adapt.
- **Its 8 `@tool`s** (these are the agent's callable tools, several wrap the kernel):
  `stage_inputs_from_s3`, `run_kernel_extractor`, `extract_via_claude_only`,
  `compute_derived_columns`, `pivot_to_ratesheet_csv`, `kernel_extract_to_csv_s3`,
  `escalate_to_claude_multimodal`, `validate_total_package_checksum`.

---

## Layer 3 — the Kernel (`kernel/`, Bitbucket subtree — do not modify)

Deterministic Python engine the agent tools call (and the legacy llm-extractor used):
- `kernel/pipeline/` — ingest, ocr, extract, compute, pivot, critic, evaluate, run
- `kernel/canonical/` — the canonical data model (ClassificationRow / RateCell)
- `kernel/extract/` — per-union extractors (e.g. build_483, compare_483)
- `kernel/profiles/` — union profile defs

**Status:** not in the active synthesizer path (synthesizer reads PDFs via Bedrock + the
synth-deps modules, not the kernel). Used by Layer-1 agent tools. High-value deterministic
logic (pivot, compute, checksum) that Phase-2 agent tools will reuse — keep.

---

## Bottom line for the weekend CDK sync
Reconciliation **adopts all 39 Lambdas + the AgentCore runtime + kernel as-is** — nothing
is deleted except `extractor-invoker` (already removed from source). All keep/delete
decisions on the 🟥 legacy set + the agent + kernel are **deferred to Monday's Phase-2
design**, when group-C validators may become agent tools.
