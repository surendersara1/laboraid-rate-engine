# Decision 4 — **One orchestrator agent + tools** (single AgentCore runtime)

**Status:** ✅ DONE — decided and built (the `ImproverAgent`, live).
**Context:** Phase 2 improvement loop; agent topology / how many runtimes.

## Decision
Start with **one orchestrator agent** that owns the whole Improve run and calls **tools**
deterministically, on **a single AgentCore runtime** — *not* a fleet of specialized
runtimes. Splittable later if a stage needs independent scaling.

## Why
- Simpler to deploy, observe, and reason about: one ECR image, one runtime ARN, one IAM
  role, one log group. One thing to roll via CDK (image digest pinned in context).
- The work is naturally a single linear run per "Improve" click (load corrections → apply
  overrides → recompute derived → re-synthesize comments → write new version). No
  cross-agent fan-out justifies multiple runtimes yet.
- Determinism lives in **tools**, not the LLM: `rate_math` (overrides + derived recompute)
  is pure Python; only commented cells touch Bedrock. The agent orchestrates; tools decide
  values. Keeps the no-fabrication guarantee (see D5/guardrails) auditable.
- Cost/latency: one warm runtime vs N; ConcurrentToolExecutor handles per-cell parallelism
  inside the single agent.

## What changed (CDK-deployed)
- `agents/improver/` — `BedrockAgentCoreApp` + `@app.entrypoint improve(local, period,
  run_id)`; ARM64 Dockerfile bundling `lambdas/shared/rate_math.py`.
- `ProcessingStack`: `improver_repo` (ECR by name), `improver_role`
  (bedrock-agentcore principal; Bedrock Converse/InvokeModel/ApplyGuardrail + Aurora Data
  API + inputs bucket read + master key + repo pull + logs), and
  `improver_runtime = StrandsAgentRuntime(...)`, image pinned via `-c improver_image=`.

## Tools the single agent calls
- `rate_math.recompute_derived` (pure, deterministic) — overrides + derived columns.
- `_resynthesize` → Bedrock Converse over source PDF text (`_source_text`/pypdf) — only
  for commented cells; returns None ⇒ KEEP prior value (no fabrication).
- Aurora writes: `_write_new_version` (copy cells to v+1) + `improvement_changes`.

## Verified
Single runtime (digest `b86e44a1`) ran both override and comment paths end-to-end; one log
group carries the whole trace. No second runtime needed.
