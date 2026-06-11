# Design

Engineering specs, architecture, audits, and earlier design docs. These
inform implementation; the customer-facing material lives in
[../Runbooks/](../Runbooks/).

## Engine specs (numbered sequence, read in order)

| File | Topic |
|---|---|
| [00_README.md](00_README.md) | Series intro |
| [01_Engine_Architecture.md](01_Engine_Architecture.md) | Engine architecture |
| [02_Parser_Stages.md](02_Parser_Stages.md) | Parser pipeline stages |
| [03_Bedrock_AI_Layer.md](03_Bedrock_AI_Layer.md) | Bedrock / AI layer |
| [04_Schemas_and_DSL.md](04_Schemas_and_DSL.md) | Schemas + DSL |
| [05_Provenance_and_Citations.md](05_Provenance_and_Citations.md) | Cell-level provenance |
| [06_Implementation_Plan.md](06_Implementation_Plan.md) | Implementation plan |
| [07_Strands_AgentCore_Agentic_Design.md](07_Strands_AgentCore_Agentic_Design.md) | Strands + AgentCore Runtime |
| [08_Ground_Truth_and_LLM_Loop.md](08_Ground_Truth_and_LLM_Loop.md) | Ground truth + LLM accuracy loop |
| [09_Technical_Implementation_Spec.md](09_Technical_Implementation_Spec.md) | **Master spec — every layer, every resource** |

## Product presentation set (June 10, 2026 — primary deck)

The walkthrough trio now lives in [`../Runbooks/`](../Runbooks/) so it travels
with the rest of the customer-facing material; only the customer-input brief
remains under Design.

| File | Topic |
|---|---|
| [../Runbooks/PRODUCT_END_TO_END_FLOW.md](../Runbooks/PRODUCT_END_TO_END_FLOW.md) | **14-step flow** — every service, every Lambda, every Bedrock call, every error, every observability hook in sequence |
| [../Runbooks/PRODUCT_SERVICE_INVENTORY.md](../Runbooks/PRODUCT_SERVICE_INVENTORY.md) | Slide-deck appendix — every Lambda, every SFN state, every DDB table, cost model |
| [../Runbooks/PRODUCT_ERROR_AND_LOGGING_REFERENCE.md](../Runbooks/PRODUCT_ERROR_AND_LOGGING_REFERENCE.md) | SRE / on-call reference — HTTP codes, retry policies, log groups, replay playbook, SLOs |
| [client_brief_and_integration_plan.md](client_brief_and_integration_plan.md) | Customer-input synthesis + 6 architectural moves |

## System architecture (background)

| File | Topic |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | High-level architecture |
| [Architecture_Flow.md](Architecture_Flow.md) | Detailed flow walkthrough (pre-OCR) |
| [Architecture_Flow.html](Architecture_Flow.html) | Renderable HTML version |
| [Understanding.md](Understanding.md) | Project understanding doc |

## Audits + reports

| File | Topic |
|---|---|
| [AUDIT_REPORT.md](AUDIT_REPORT.md) | Initial audit (BLOCKER / DRIFT / NICE) |
| [AUDIT_VERIFICATION.md](AUDIT_VERIFICATION.md) | Independent re-check after fixes |
| [AUDIT_DECISIONS.md](AUDIT_DECISIONS.md) | Audit decision log |
| [AUDIT_FIX_PROMPT.md](AUDIT_FIX_PROMPT.md) | Original fix prompt |
| [AUDIT_NOTE_AGENTCORE_API.md](AUDIT_NOTE_AGENTCORE_API.md) | AgentCore API notes |
| [Overnight_Audit_Report.md](Overnight_Audit_Report.md) | Overnight audit results |
| [Overnight_Delivery_Report.md](Overnight_Delivery_Report.md) | Overnight delivery summary |

## Build + history

| File | Topic |
|---|---|
| [BUILD_LOG.md](BUILD_LOG.md) | Chronological per-commit build log |
| [BUILD_PROFILE_DRAFTER.md](BUILD_PROFILE_DRAFTER.md) | Kernel profile drafter design |
| [Learning_Lessons.md](Learning_Lessons.md) | Lessons captured during build |
| [CTO_SUMMARY.md](CTO_SUMMARY.md) | Layer-by-layer summary for CTO/management |
| [STATUS.md](STATUS.md) | Current state of the world |
| [PR_DESCRIPTION.md](PR_DESCRIPTION.md) | PR description template |

## Feature designs

| File | Topic |
|---|---|
| [design_multipdf_merge.md](design_multipdf_merge.md) | Pattern-C multi-PDF merge (Aurora cell-level) |
| [design_upload_grouping_and_idempotency.md](design_upload_grouping_and_idempotency.md) | Batch grouping + SHA256 content-hash idempotency |
| [feature_improvement_1_2026-06-09.md](feature_improvement_1_2026-06-09.md) | Feature improvement notes |

## Sample data

[samples/](samples/) — canonical JSON + YAML samples (e.g.
`canonical_sprinkler_704_2026-01.json`, `profile_sprinkler_704.yaml`).
