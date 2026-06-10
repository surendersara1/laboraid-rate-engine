# LaborAid Rate Engine

AWS POC that converts union Collective Bargaining Agreement (CBA) PDFs into
structured rate sheets — with per-cell provenance, a deterministic extraction
kernel wrapped by **two Strands agents** on Bedrock AgentCore Runtime
(`ExtractorAgent` for runtime extraction, `ProfileDrafterAgent` for auto-authoring
new union extractors), three extraction paths (deterministic / per-cell LLM
fallback / full-sheet LLM), and a two-persona React review UI with a business
approval gate.

**Status:** see [`docs/STATUS.md`](docs/STATUS.md) for the current state of the
world (single source of truth).

- **All 5 POC unions** (537, 704, 821, 483, 281) now run end-to-end through the one
  kernel pipeline and pass a real regression gate (≥99% sourced accuracy). 281 &
  821 are fully wired (indenture cohorts, 4 zones).
- Pipeline **validated blind** against 4 customer rate sheets; correctness bugs
  found there (multiplier rounding, 537 wage source, evaluator tolerance) are
  fixed, plus a CI accuracy gate, kernel tests, agent hardening, and a
  completeness-coverage critic. See [`docs/Design/STATUS.md`](docs/Design/STATUS.md).
- POC build complete for Groups A–F + H (CDK infra, ExtractorAgent, Lambdas,
  two-persona SPA, orchestration, observability, CI, smoke).
- **Path C** (generic Claude extractor for unmapped unions) + **ProfileDrafterAgent**
  (auto-authors profile YAML + extractor for any new union) on
  `feat/path-c-and-drafter` — 87 tests passing, self-audit 31/31 PASS.
  See [`docs/Overnight_Delivery_Report.md`](docs/Overnight_Delivery_Report.md).
- **Before deploy:** `cd ui && pnpm build` (SPA bundle) + push the extractor ECR
  image. Details in [`docs/STATUS.md`](docs/STATUS.md).

## Documentation

| Audience / purpose | File |
|---|---|
Docs are organized into two folders:
- [`docs/Runbooks/`](docs/Runbooks/) — what the product produces (ops runbook, deploy, onboarding, client walkthrough, extraction log, per-period gap reports).
- [`docs/Design/`](docs/Design/) — engineering specs, architecture, audits, build log, earlier design docs.

| Audience / purpose | File |
|---|---|
| **Current state of the world** — coverage, accuracy, what changed, what's left | [`docs/Design/STATUS.md`](docs/Design/STATUS.md) |
| **CTO / management** — layer-by-layer summary, SOW match, risks, cost | [`docs/Design/CTO_SUMMARY.md`](docs/Design/CTO_SUMMARY.md) |
| **New developer** — clone-and-go setup | [`docs/Runbooks/ONBOARDING.md`](docs/Runbooks/ONBOARDING.md) |
| **Architects** — system design + decisions | [`docs/Design/ARCHITECTURE.md`](docs/Design/ARCHITECTURE.md) |
| **Full technical spec** — every layer, every resource, 6-pillar coverage | [`docs/Design/09_Technical_Implementation_Spec.md`](docs/Design/09_Technical_Implementation_Spec.md) |
| **Build queue** — how this repo was generated (Groups A–H) | [`BUILD_INSTRUCTIONS.md`](BUILD_INSTRUCTIONS.md) |
| **Build log** — chronological audit trail per commit | [`docs/Design/BUILD_LOG.md`](docs/Design/BUILD_LOG.md) |
| **Ops** — runbook, alarms, retry/abort, incidents, Q&A | [`docs/Runbooks/RUNBOOK.md`](docs/Runbooks/RUNBOOK.md) |
| **Deploy** — CDK + UI deploy procedure | [`docs/Runbooks/DEPLOY.md`](docs/Runbooks/DEPLOY.md) |
| **Client walkthrough** — end-to-end extraction flow | [`docs/Runbooks/extraction_flow_for_client.md`](docs/Runbooks/extraction_flow_for_client.md) |
| **Customer PDF extraction log** — running journal of what each customer batch produced | [`docs/Runbooks/customer_pdf_extraction_log.md`](docs/Runbooks/customer_pdf_extraction_log.md) |
| **Audit (initial)** — 8 BLOCKER + 9 DRIFT + 7 NICE-TO-HAVE findings | [`docs/Design/AUDIT_REPORT.md`](docs/Design/AUDIT_REPORT.md) |
| **Audit (verification)** — independent re-check after fix passes | [`docs/Design/AUDIT_VERIFICATION.md`](docs/Design/AUDIT_VERIFICATION.md) |
| **Earlier design docs** — discovery, schemas, DSL, provenance, ground truth | [`docs/Design/00_README.md`](docs/Design/00_README.md) through [`docs/Design/08_*.md`](docs/Design/) |

## Architecture

```
Admin UI ─▶ S3 inputs ─▶ EventBridge ─▶ Step Functions main pipeline
  classify (Lambda) ─▶ extract (ExtractorAgent / AgentCore, wraps kernel)
  ─▶ validate (checksum + range + confidence) ─▶ gate
       passed ─▶ render (xlsx/csv/articles) ─▶ publish (Aurora + S3 + SNS)
       else   ─▶ review queue
Business UI ── review ── Approve/Reject ──▶ Admin Publish (409 unless approved)
```

Nine Python CDK stacks (ARM64, `us-east-1`): Security · Storage · Ai · Processing
· Validation · Api · Ui · Orchestration · Observability. Full design:
[`docs/Design/09_Technical_Implementation_Spec.md`](docs/Design/09_Technical_Implementation_Spec.md).
See also [`docs/Design/ARCHITECTURE.md`](docs/Design/ARCHITECTURE.md),
[`docs/Runbooks/RUNBOOK.md`](docs/Runbooks/RUNBOOK.md),
[`docs/Runbooks/ONBOARDING.md`](docs/Runbooks/ONBOARDING.md).

## Layout

```
cdk/        Python CDK — 9 stacks (aws-cdk-lib). Entry: cdk/app.py.
lambdas/    Python 3.12 Lambdas: api/ (19), processing/, validation/, rendering/.
agents/     ExtractorAgent (Strands) container.
kernel/     Ashwani's deterministic pipeline (git subtree — never hand-edit).
ui/         React 18 + TS SPA (Vite). The only TypeScript in the repo.
tests/e2e/  Smoke test + fixtures.
docs/       Specs, runbook, architecture, onboarding, build log.
```

**Language split:** every layer is Python except `ui/` (React + TypeScript). CDK
is Python, not TypeScript.

## Build & deploy

```bash
# Backend (CDK + Lambdas) — Python via uv
cd cdk
uv sync
npx cdk synth                 # acceptance gate — exits 0 for all 9 stacks
uv run ruff check . && uv run black --check . && uv run mypy --strict laboraid_cdk
uv run pytest && uv run pytest ../lambdas

# UI — React via pnpm (corepack)
cd ../ui
corepack pnpm install
corepack pnpm typecheck && corepack pnpm lint && corepack pnpm exec vitest run
corepack pnpm build           # -> ui/dist (deployed by the Ui stack)

# Kernel — deterministic extraction, no AWS
cd ../kernel && uv sync && uv run python pipeline/run.py --all

# Deploy (human's call; needs AWS creds + Bedrock model access + AgentCore)
cd ../cdk && export CDK_DEFAULT_ACCOUNT=<acct> CDK_DEFAULT_REGION=us-east-1
npx cdk bootstrap && npx cdk deploy --all       # prod: add -c env=prod

# End-to-end smoke (local = kernel core; deployed = upload via API)
bash tests/e2e/smoke-test.sh
```

> `cdk` is the Node AWS CDK CLI — invoke via `npx cdk` (not `uv run cdk`).
> `pnpm` is reached via `corepack pnpm`.

## Measured accuracy (kernel regression guard)

`kernel/pipeline/run.py --all --min-accuracy 99.0` reproduces, on **sourced** cells
(intentional flagged-gap blanks excluded): **537 = 100%**, **281 = 100%**,
**704 = 99.6%**, **821 = 99.7%**, **483 = 100%** (74 sourced blanks where the
residential scale is absent from the docs — flagged, never fabricated). All five
pass the ≥99% gate. CI runs `pytest` (13 kernel tests) + the gate on every PR.
See [`docs/Design/STATUS.md`](docs/Design/STATUS.md) for the full table and the few remaining
sub-cent diffs (all documented doc-vs-groundtruth divergences).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cdk synth` "no credentials" | Stacks are env-agnostic; should not happen. If a VPC/Route53 lookup is added, set `CDK_DEFAULT_ACCOUNT`. |
| `uv run cdk` not found | Use `npx cdk` — the CDK CLI is Node, not a Python package. |
| `pnpm: command not found` | Use `corepack pnpm <cmd>` (or `corepack enable`). |
| Aspect "infinite loop" on synth | The tag aspect tags L1 CfnResources only — keep it that way. |
| Alarm/pipeline failures | See [`docs/Runbooks/RUNBOOK.md`](docs/Runbooks/RUNBOOK.md). |

## Provenance

The deterministic extraction kernel under `kernel/` was developed by **Ashwani /
NBS** (`git@bitbucket.org:northbay/labor_aid_poc.git`) and is imported via
`git subtree` — never hand-edit it; pull updates with `git subtree pull`. The AWS
wrapping (CDK, agent, Lambdas, UI) is built on top per [`docs/`](docs/).

## License

Internal NBS use only. No external distribution without permission.
