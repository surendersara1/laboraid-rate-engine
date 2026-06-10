# LaborAid Rate-Sheet Engine — Design

**Date:** 2026-05-04
**Author:** NBS for LaborAid POC
**Status:** First design proposal — based on discovery findings (`../discovery/`)
**Audience:** LaborAid product + engineering leads, NBS engineering team

> **This is a design proposal**, not a build spec. It captures the engine architecture and how each major capability is realized. After review with the customer, we'll harden details into a build plan.

---

## Design philosophy

After studying 5 POC unions across thousands of rate-sheet cells (see `../discovery/11_Findings_for_Client.md` for the consolidated findings), we landed on **5 design principles**:

### 1. Hybrid deterministic + AI
Use deterministic parsing (regex, schema-driven table parsers, formulas) wherever it works — it's faster, cheaper, more reproducible, and easier to debug. Fall back to **Bedrock Claude** for the messy cases: free-text rule extraction from CBAs, ambiguous tables, low-OCR-confidence cells, and provenance/citation generation.

### 2. Profile-driven, not code-driven
Per-union variability lives in **YAML/JSON Profiles**, not in code. The engine is one codebase; each union is a config file. New unions don't require code deploys.

### 3. Two-source canonical pattern
Rate Notice provides **dollar values** ($-amounts). CBA provides **structural rules** (formulas, ladders, exclusions). The engine joins them via the Profile to produce the canonical JSON.

### 4. Per-cell provenance is mandatory
Every value in every published rate sheet links back to its source — a Rate Notice line, a CBA article, a derived formula, a convention rule, or a manual override. **Auditability is not optional.**

### 5. AI-native where it adds value, deterministic where it doesn't
- **Strands Agents SDK** ([strandsagents.com](https://strandsagents.com/)): open-source AWS-native agent framework with first-class `@tool`, hooks, and **steering** (`SteeringHandler` returning `Guide(reason=…)` or `Proceed(...)`)
- **AWS Bedrock AgentCore**: serverless runtime + Memory (semantic/summary/preference) + Gateway (Lambda→MCP) + Identity + Observability (OTEL) + Evaluations + Policy (Cedar guardrails) + Registry — all the platform plumbing for production agents
- **Bedrock Claude (multi-modal):** the model behind extraction-fallback and CBA rule mining
- **Bedrock Knowledge Bases + S3 Vectors:** semantic search across CBA corpus for citation lookup ("which article in this 35-page CBA defines the Foreman premium?")
- **9 specialist agents** orchestrated via Strands Agent-as-Tool — see doc 07 for the full agent topology
- **Step Functions + Lambda:** the deterministic backbone (file routing, DSL evaluation, validation math, rendering)
- **Aurora Postgres + DynamoDB:** state and audit log

---

## Folder map

| # | Document | Purpose |
|---|---|---|
| **00** | `00_README.md` (this file) | Design index + philosophy |
| **01** | `01_Engine_Architecture.md` | High-level architecture, components, end-to-end data flow with AI-native + agentic design |
| **02** | `02_Parser_Stages.md` | Detailed pipeline stages — ingest → classify → extract → resolve → validate → render |
| **03** | `03_Bedrock_AI_Layer.md` | Where and how Bedrock is used — Claude (multi-modal extraction), Agents (orchestration), Knowledge Base + S3 Vectors (CBA RAG + citation lookup) |
| **04** | `04_Schemas_and_DSL.md` | JSON schemas (canonical output, intermediate, Profile), formula DSL grammar with examples |
| **05** | `05_Provenance_and_Citations.md` | Per-cell provenance system — 6 tag types, citation generation pipeline, audit UX |
| **06** | `06_Implementation_Plan.md` | Week-by-week build plan with milestones, dependencies, and sample artifacts |
| **07** | `07_Strands_AgentCore_Agentic_Design.md` | ⭐ **Concrete agentic redesign** — replaces the abstract "Bedrock Agent" references in docs 01-06 with a 9-agent system built on Strands Agents SDK and deployed to AWS Bedrock AgentCore Runtime. Per-agent specs (role, tools, hooks, **steering**, memory, deployment), AgentCore service mapping (Runtime + Memory + Gateway + Identity + Observability + Evaluations + Policy + Registry), Skills catalog, and migration guide from docs 01-06. **Read this for the canonical agent architecture.** |
| **08** | `08_Ground_Truth_and_LLM_Loop.md` | ⭐ **The four critical questions answered end-to-end:** (1) How does the engine know what a rate sheet should contain? (2) How does a 30-page PDF reach the LLM (it usually doesn't — chunked KB retrieval)? (3) How does the agent verify its own output (4-layer defense: confidence + checksums + cross-source agreement + YoY sanity, enforced via Strands steering)? (4) What happens for brand-new unions we've never seen (ProfileDrafterAgent → human polish → backfill, ~3 days)? Cuts across stages — read for the operational truth of the engine. |

Plus reference samples in `samples/`:

```
docs/
├── 00_README.md                                  ← you are here
├── 01_Engine_Architecture.md                     ← high-level architecture
├── 02_Parser_Stages.md                           ← detailed pipeline stages
├── 03_Bedrock_AI_Layer.md                        ← Bedrock model integration (read alongside 07)
├── 04_Schemas_and_DSL.md                         ← JSON schemas + formula DSL
├── 05_Provenance_and_Citations.md                ← per-cell provenance system
├── 06_Implementation_Plan.md                     ← 8-week build plan
├── 07_Strands_AgentCore_Agentic_Design.md        ← ⭐ canonical agentic architecture — Strands + AgentCore
└── samples/
    ├── profile_sprinkler_704.yaml                ← example Union Rule Profile (704)
    └── canonical_sprinkler_704_2026-01.json      ← example CanonicalRateSheet output
```

> **Note on doc 03 vs doc 07:** Doc 03 was written before we adopted Strands Agents. It still describes the **model-level** capabilities (Claude prompts, model selection, KB) accurately — those don't change. Doc 07 is the **platform/topology** layer that wraps doc 03's model usage in 9 specialist Strands agents deployed on AgentCore Runtime. Where doc 03 says "Bedrock Agent", read doc 07 for the concrete agent that does it.

---

## Reference: where the Discovery findings drove design

| Discovery finding | Design response |
|---|---|
| 24 dimensions of variation across 5 unions | Profile schema with explicit fields for each dimension |
| Rate Notice format varies (1 page text, 13 pages with per-class detail, image-only, 4-file bundle) | Multi-format ingestion + Bedrock Claude as universal fallback parser |
| CBAs are 25-50 pages with structural rules across many articles | Bedrock Knowledge Base with S3 Vectors over CBA corpus; semantic search for rule extraction |
| Rate sheets must be auditable (customer's `Articles` sheet shows intent) | Per-cell provenance with 6 tag types, auto-populates Articles output |
| Calculation discrepancies between Notice and rate sheet (e.g., 704 OT formula) | Engine surfaces both values + chooses per Profile policy |
| Schema drifts (column renames, additions) | Canonical JSON output is flexible map; xlsx renderer projects to per-period column set |
| Y1/Class 1 apprentice exclusions vary by union | Profile `apprentice_exclusion` with `cutoff_unit` and `excluded_funds` parameters |
| Power & Gas markup base ambiguous in CBA | Profile fallback rule + Bedrock confidence flag for human review |
| Rate notices and CBAs sometimes contradict | Validator surfaces both, Profile chooses canonical, manual override available |

---

## What the engine is NOT

To be clear about scope:

- ❌ The engine is **not** LaborAid's payroll/remittance product. It produces rate data; the LaborAid product consumes it.
- ❌ The engine does **not** process per-worker payroll (no PII flowing through).
- ❌ The engine does **not** make payments to trustees (LaborAid's product handles payment).
- ❌ The engine is **not** an OCR research project. It uses standard tools (Textract, Tesseract, Bedrock Claude vision) and falls back to human review on hard cases.

The engine **is** the data foundation: PDF in → audited rate sheet out, with every cell traceable to source.

---

## Key reference docs from Discovery

If you haven't read the discovery work yet:

- **[`../discovery/11_Findings_for_Client.md`](../discovery/11_Findings_for_Client.md)** — single client-facing summary (rules, patterns, gaps, 30 open questions)
- **[`../discovery/08_Engine_Features.md`](../discovery/08_Engine_Features.md)** — full engine feature spec
- **[`../discovery/10_AWS_Architecture.md`](../discovery/10_AWS_Architecture.md)** — AWS architecture proposal (still relevant; this Design folder refines the AI layer)

This Design folder **builds on** those docs. We're not redoing them — we're translating their conclusions into a concrete engine design with first-cut sequence flows and component-level design decisions.
