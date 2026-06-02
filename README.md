# LaborAid Rate Engine

AWS-deployed POC for the LaborAid Rate-Sheet pipeline — converts union Collective Bargaining Agreement (CBA) PDFs into structured ratesheets, with per-cell provenance and human-in-the-loop review.

**Status:** active POC build. Design rationale + per-union discovery + spec all live in [`docs/`](docs/). Source PDFs + SOW remain in the parent project at `E:\NBS_LaborAid\` (read-only).

---

## What's in this repo

```
laboraid-rate-engine/
├── kernel/              # Ashwani's deterministic extraction pipeline (subtree import)
│                        # Provenance-tagged extraction, OCR, canonical model, per-union profiles.
│                        # Imported from: git@bitbucket.org:northbay/labor_aid_poc.git
├── cdk/                 # AWS CDK v2 — PYTHON (aws-cdk-lib Python) — 8-stack deployment
├── agents/              # Strands agent containers — Python (ExtractorAgent on AgentCore Runtime)
├── lambdas/             # Python Lambdas — API, validation, rendering, classification
├── ui/                  # React admin SPA — Vite + React 18 + TypeScript (ONLY non-Python area)
├── containers/          # Custom container images (Docling, OCR helpers)
├── profiles/            # Symlinks/copies of per-union YAML profiles from kernel/
├── docs/                # Architecture, runbook, onboarding, UAT report
├── scripts/             # Deploy + bootstrap helpers (Python)
└── pyproject.toml       # Workspace-level Python deps (top-level)
```

**Language split:** every layer is Python EXCEPT `ui/` (React + TypeScript). CDK is Python, not TS.

---

## Quick orientation

| You want to… | Look at |
|---|---|
| Understand what the engine does | `kernel/README.md` + `kernel/DESIGN.md` |
| Run the extraction pipeline locally | `kernel/pipeline/run.py` (per kernel README) |
| Understand the AWS deployment | `cdk/` + [`docs/09_Technical_Implementation_Spec.md`](docs/09_Technical_Implementation_Spec.md) |
| Understand the agent layer | `agents/extractor/` + [`docs/07_Strands_AgentCore_Agentic_Design.md`](docs/07_Strands_AgentCore_Agentic_Design.md) |
| See the SOW | Parent project `LaborAid - POC SOW.docx.pdf` |
| See discovery findings | Parent project `E:\NBS_LaborAid\discovery\11_Findings_for_Client.md` (not yet mirrored into this repo) |

---

## Architecture (one-liner)

```
PDF upload → S3 → Step Functions →
   Classifier (Lambda) →
   ExtractorAgent (Strands on AgentCore Runtime, wraps kernel/pipeline/extract) →
   Validator Lambdas (checksum, range) →
   Renderer Lambdas (xlsx, CSV) →
   Aurora + S3 outputs →
   LaborAid Calculator (consumes via API)
```

The deterministic extraction kernel (`kernel/`) is wrapped in a Strands agent (`agents/extractor/`) that runs on AWS Bedrock AgentCore Runtime. The agent satisfies the SOW's "AI Agentic" commitment; the kernel delivers the proven extraction accuracy.

---

## Provenance

This repo is the **AWS-deployable monorepo** for the LaborAid POC. The deterministic extraction kernel under `kernel/` was independently developed by **Ashwani / NBS** at `git@bitbucket.org:northbay/labor_aid_poc.git` and is imported here via `git subtree`. Per its measured accuracy (kernel/.claude/harness/evaluation-log.md): **704 = 99.6%, 483 Building = 100%, 537 = 67.4% (sub-100% are confirmed-absent source docs).**

The AWS wrapping (CDK, agents, Lambdas, UI) is built on top of the kernel per the design in [`docs/`](docs/) (formerly `Design/` in the parent project; mirrored into this repo on 2026-06-02 as the single source of truth).

---

## Branches

- `main` — protected; merged code only
- `feat/aws-strands-integration` — active development for the AWS deployment + Strands agent wrapping + missing union extractors (281, 821)

---

## Build & run

See `docs/SETUP.md` (to be created) for full setup. Quick start:

```bash
# 1. Kernel — extraction only, no AWS needed
cd kernel
uv sync
uv run python pipeline/run.py --all

# 2. Full AWS stack — Python CDK
cd cdk
uv sync
uv run cdk synth
uv run cdk deploy --all

# 3. React admin UI (only TS in the repo)
cd ui
pnpm install
pnpm build           # outputs ui/dist/, deployed by CDK UiStack
```

---

## License

Internal NBS use only. No external distribution without permission.
