# Decision 3 — Improve runs **asynchronously**, with status surfacing

**Status:** ✅ DONE — decided and built into the Improve loop (live, us-east-2).
**Context:** Phase 2 improvement loop; how the "Improve" action executes & reports.

## Decision
The **"Improve with AI"** action is **async**. The API Lambda only *triggers* the run
and returns immediately (HTTP 202-style accept with a `run_id`); the actual work runs
on the AgentCore runtime. The UI **polls for the new version to appear** and then jumps
to it. Progress/status is surfaced the same way pipeline jobs are.

## Why
- LLM re-synthesis of commented cells takes **minutes** (PDF read + Bedrock Converse per
  cell) — well past API Gateway / Lambda sync response budgets.
- Mirrors the existing pipeline UX (jobs surface status), so reviewers already understand
  the pattern. No new mental model.
- Decouples the trigger (cheap, fast Lambda) from the worker (long-running, AgentCore),
  which is also what D4's topology and the 8h AgentCore session window want.

## What changed (CDK-deployed)
- `ratesheet-improve` Lambda: self-async pattern — synchronous leg records an
  `improvement_run` (status `running`) + dispatches a `lambda.invoke` Event; the async leg
  calls `invoke_agent_runtime(runtimeSessionId=run_id)`.
- `ImproveBar.tsx`: POSTs `/improve`, then `pollForNewVersion` (≤300s, backoff 2.5→5s) on
  `GET .../rate-sheets/{period}`; on a higher version number it calls `onImproved(v)`.
- Run lifecycle (`running`→`succeeded`/`failed`) recorded in `improvement_runs` (Aurora).

## Verified
Override + comment runs both completed async: button showed "Improving… (agent running)",
polled, then switched to v2 with the change-log panel. No sync timeout.
