# Phase 2 — Strands Agentic Reviewer (design)

**Goal:** an AI reviewer that sits between synthesis and the human business reviewer.
It pre-vets every freshly-synthesized rate sheet — running deterministic checks +
LLM reasoning + **patterns learned from past human feedback** — and produces
*review findings* (flagged cells, reasons, suggested corrections, a confidence). The
human reviewer then sees a pre-vetted sheet with the agent's concerns highlighted:
faster approvals, fewer errors slipping through. **It assists; it never auto-approves
— dual-control stays human.**

This is Phase 2; it builds on the existing Strands-on-AgentCore foundation
(`ExtractorAgent`, `ProfileDrafterAgent`, the AgentCore runtime + ECR + IAM role).

## Where it fits the pipeline
```
Synthesize ─► SynthPublish ─► [NEW] ReviewerAgent ─► findings (DynamoDB ops)
                                                          │
                                  Business RateSheetReview UI shows agent flags
                                  + suggested fixes alongside the cells (Aurora)
```
Trigger: an EventBridge event on publish (decoupled, same pattern as `job-writer`),
or a 4th SFN step. The agent runs on **Bedrock AgentCore** (reuse the ExtractorAgent
runtime pattern), Claude Opus 4.5, PII guardrail on.

## What the agent does (tools)
Strands `@tool`s — deterministic checks + retrieval + reasoning:
- `validate_total_package_checksum` — wage + fringes = printed Total Package (±$0.05). *(reuse kernel)*
- `range_check` / `confidence_rollup` — bounds + per-cell confidence. *(reuse the validators)*
- `compare_to_prior_period(union, period)` — diff vs the last published sheet (Aurora); flag implausible jumps.
- `query_learned_feedback(union, fund, cohort)` — pull **past human corrections** for similar cells (see Learning).
- `flag_cell(cell_id, reason, suggested_value, confidence)` — emit a finding.
- `summarize(findings)` — overall verdict + a human-readable summary.

## Learning from review feedback (the differentiator)
We already capture the labeled signal — this is the training corpus:
- **Rejections + reasons** (`ratesheet-reject` → audit log)
- **Cell overrides** (human-corrected values, `overrides` table)
- **Comments** (`cell-comment`)
- **Rework** events

`query_learned_feedback` retrieves the relevant slice (same union / fund / cohort /
column) and feeds it to the agent as context, so it learns "for Local 704 apprentice
Class 1, Pension is $0 in the first six months" from a *prior human correction* — and
flags or pre-fills it next time. Start with retrieval-as-context (no fine-tuning);
graduate to a compact per-union "lessons" record if needed.

## Data model (consistent with ARCHITECTURE.md)
- **Findings** = operations data → **DynamoDB** (`review_findings`, keyed by
  union#period, surfaced in the Business UI). Never written to Aurora.
- **Feedback corpus** = the existing `overrides` / audit / rejections (operations).
- **Rate-sheet cells** stay in **Aurora** (the artifact) — the agent *reads* them, never rewrites them; a suggestion only becomes real when a human accepts it (→ an override → which feeds learning).

## Phasing
- **2a — skeleton + deterministic pass:** ReviewerAgent on AgentCore; runs the
  checksum/range/confidence tools + prior-period diff → writes findings to DynamoDB;
  Business UI renders the flags. (No learning yet — proves the loop end-to-end.)
- **2b — feedback-aware:** add `query_learned_feedback`; agent reasons with past
  human corrections for the union/fund/cohort.
- **2c — closed loop:** agent suggestions are one-click acceptable in the UI →
  becomes an override → feeds the corpus. Measurable: % of findings the human accepts.

## Guardrails / principles
- **Assist only** — the agent flags + suggests; humans approve (dual-control intact).
- **No fabrication** — every suggestion cites a source (a PDF value, a checksum, or a
  prior human correction). Gaps stay gaps.
- **Decoupled** — triggered by an event, writes to its own DynamoDB findings table;
  zero changes to the synthesis or approval code paths.

## CDK footprint (all via CDK)
- New `review_findings` DynamoDB table (Storage) + GSI by union#period.
- ReviewerAgent container/runtime (Processing — reuse the `StrandsAgentRuntime`
  construct + ECR + role pattern) + Bedrock/guardrail grants.
- EventBridge rule on publish → reviewer; reviewer → findings table.
- API: `GET /v1/unions/{local}/rate-sheets/{period}/findings` reads the findings table.

## Open questions for kickoff
1. Trigger on **every** publish, or only when confidence/gaps exceed a threshold (cost)?
2. Findings surfaced inline in the cell grid, or a separate "AI review" panel?
3. 2b retrieval scope — per-union only, or cross-union for shared funds?
