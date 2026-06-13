# Phase 2 — Human-in-the-Loop Improvement Cycle (deep design)

> Status: **design for review (Sat AM)**, not built. This is the core Phase-2 flow —
> the agentic *improver* that acts on human review feedback. (Complements the passive
> pre-reviewer in `PHASE2_STRANDS_DESIGN.md`: that one flags *before* a human looks;
> this one *fixes* after the human gives feedback.)

## The loop
```
 v_n synthesized ─► Business reviews in UI: per-cell COMMENTS + OVERRIDES, does NOT approve
                          │  (feedback persisted to Aurora — legal audit)
                          ▼
                   [ "Improve" button ]
                          ▼
          Improvement agent(s) read feedback + sources + steering
            • OVERRIDE  → apply deterministically + recompute derived (kernel)
            • COMMENT   → targeted re-synthesis (LLM) with the comment as steering
            • untouched → left exactly as-is (surgical, not a blind re-run)
                          ▼
                   v_{n+1} (new version) + per-cell change log
                          ▼
          Business re-reviews v_{n+1} ─► approve  OR  more feedback → loop
```
Versioned, append-only, dual-control preserved. The agent produces a **candidate**;
a human still approves. The agent **never approves**.

## Two kinds of feedback → two very different behaviours
| Feedback | Human intent | Agent behaviour | Trust model |
|---|---|---|---|
| **Override** (gives a value) | "The correct value IS X." | **Apply deterministically.** Recompute derived columns (OT 1.5×/2×, differentials, P&G) via the **kernel**. Never re-guess. | Human-authoritative — the agent obeys, doesn't reason over it. |
| **Comment** (flags, no value) | "This looks wrong because Y — check the CBA." | **Targeted re-synthesis (LLM):** re-read the source PDFs for that cell/region with the comment injected as **steering**; emit a corrected value **+ source citation**. | Probabilistic — must cite a source; gaps stay gaps. |

This split is the crux: **deterministic for overrides, reasoned for comments.** Mixing
them (letting the LLM "reconsider" a human override) would be wrong for a legal system.

## Data model — Aurora child tables (legal/financial SoR)
Today comments live in `audit_log` (free text) and override *values* in a DynamoDB
table — fine for ops, **not** structured enough for a legal record. Promote to
relational child tables (FK to the cells), append-only, versioned:

- **`cell_corrections`** — one row per feedback item:
  `id, period_id (FK), version, cell_id (FK rate_cells), kind (comment|override),
   prior_value, new_value (null for comment), reason, actor, created_at,
   status (open|applied|superseded)`.
- **`improvement_runs`** — one row per "Improve" click (the AI's actions, for audit):
  `id, period_id, from_version, to_version, triggered_by, model, started_at,
   finished_at, status, summary`.
- **`improvement_changes`** — per-cell what-the-agent-did:
  `run_id (FK), cell_id, prior_value, new_value, source (override|resynth),
   provenance (PDF cite / "human override by X"), confidence`.

All immutable + versioned + who/what/when/before/after/why. This is what makes every
number defensible to an auditor or trustee. (Operational telemetry still → DynamoDB;
this legal trail → Aurora, consistent with `ARCHITECTURE.md`.)

## Agent design — one orchestrator, specialist tools (start), splittable later
The work has genuinely different competence/trust profiles, so decompose it — but
**start as ONE orchestrator agent with the workers as Strands tools** (agent-as-tool),
and only split into separate AgentCore runtimes if scale/latency demands.

- **Improvement Planner (orchestrator):** ingests the feedback set, classifies each
  item, builds the plan, dispatches workers, assembles `v_{n+1}` + the change log + a
  human summary. The "brain."
- **Override Applier (deterministic tool — NOT an LLM):** applies overrides verbatim,
  kernel-recomputes derived columns, runs the total-package checksum. Must be exact.
- **Re-Synthesizer (LLM agent):** re-extracts commented cells from source with
  steering (= comment + learned corrections + profile rules); cites provenance.
  Reuses the existing `ExtractorAgent` + `steering.py`.
- **Critic / Validator (LLM + deterministic):** independent check of `v_{n+1}` —
  checksums hold, overrides respected exactly, untouched cells truly untouched, no
  fabrication, gaps still flagged. Can bounce work back to the Re-Synthesizer (a
  bounded self-correction loop) before any human sees it.

Why multi-agent: separates deterministic from probabilistic, gives an adversarial
quality gate, and makes each actor's actions individually auditable.

## Steering — the mechanism that carries human intent into the LLM
`ExtractorSteering.steer_before_tool` already intercepts before tool calls. For the
improve loop, the steering payload for a re-synthesized cell is assembled from:
1. **the human's comment** on that cell ("first-6-months apprentices have $0 pension"),
2. **learned corrections** — past human overrides/rejections for the same
   union/fund/cohort/column (the accumulating "lessons"),
3. **the union profile rules** (canonical names, OT multipliers, cohort structure).
So a one-line human comment becomes precise, sourced LLM guidance — and the system
gets better per union over time.

## Versioning + dual-control
Each Improve → a new `rate_periods` version (the `parent_version` chain already exists
and `ratesheet-get` already returns a version list). Lineage v1→v2→v3 is fully
auditable. Approval always requires a human on the candidate version.

## Non-negotiables (legal/financial)
- **Traceability:** every value cites a source PDF *or* a human override attribution.
- **No fabrication:** gaps stay gaps; the agent never invents a value.
- **Human-authoritative overrides:** the agent applies them; it cannot overrule them.
- **Reproducibility:** `improvement_runs` stores inputs + model + trace → any AI change
  is explainable and replayable for an auditor.
- **Independent check:** the Critic vets the candidate before a human sees it.

## How it plugs in (CDK, tomorrow)
- Aurora child tables via `schema-init` DDL.
- `POST /v1/unions/{local}/rate-sheets/{period}/improve` → triggers the flow **async**
  (AgentCore + LLM = minutes; surface status like the pipeline). Likely a small SFN:
  `ApplyOverrides → ReSynthesize → Critic → PublishNewVersion`.
- Improvement agent(s) on **Bedrock AgentCore** (reuse the `StrandsAgentRuntime`
  construct + ECR + role).
- UI: "Improve" button + status in `RateSheetReview`; the existing version switcher
  shows v_{n+1}; a change-log panel explains what the agent did and why.

## Open questions to settle Sat AM
1. **Override storage:** promote to the structured Aurora `cell_corrections` table
   (recommended for legal SoR) vs keep the DynamoDB `overrides_table`?
2. **Re-synthesis granularity:** per-cell vs per-region vs full-sheet-with-steering?
   (Recommend region/cell — surgical, cheaper, more auditable.)
3. **Sync vs async** Improve, and how we surface progress.
4. **Agent topology:** one orchestrator + tools (recommended start) vs multi-runtime.
5. **Critic strictness** + auto-retry budget before handing back to the human.
6. **Learned-corrections store:** where the cross-period "lessons" live and how they
   feed steering (per-union record? derived from `cell_corrections` history?).
