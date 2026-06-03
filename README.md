# LaborAid Rate Engine

AWS POC that converts union Collective Bargaining Agreement (CBA) PDFs into
structured rate sheets — with per-cell provenance, a deterministic extraction
kernel wrapped by one Strands `ExtractorAgent` on Bedrock AgentCore Runtime, and a
two-persona React review UI with a business approval gate.

**Status:** POC build complete for Groups A–F + H (CDK infra, agent, Lambdas,
two-persona SPA, orchestration, observability, CI, smoke). Kernel extractors for
unions 281 + 821 (Group G) run through the kernel's own harness — see
[`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

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
[`docs/09_Technical_Implementation_Spec.md`](docs/09_Technical_Implementation_Spec.md).
See also [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

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

`kernel/pipeline/run.py --all` reproduces: **704 = 99.6%**, **483 = 100% on the
Building zone (83.2% overall including 74 sourced blanks)**, **537 = 67.4%**
(sub-100% are confirmed-absent source values, left blank per the never-fabricate
rule — the 483 overall figure counts a 74-cell apprentice/maintenance block the
kernel leaves blank). CI re-runs these on every PR.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cdk synth` "no credentials" | Stacks are env-agnostic; should not happen. If a VPC/Route53 lookup is added, set `CDK_DEFAULT_ACCOUNT`. |
| `uv run cdk` not found | Use `npx cdk` — the CDK CLI is Node, not a Python package. |
| `pnpm: command not found` | Use `corepack pnpm <cmd>` (or `corepack enable`). |
| Aspect "infinite loop" on synth | The tag aspect tags L1 CfnResources only — keep it that way. |
| Alarm/pipeline failures | See [`docs/RUNBOOK.md`](docs/RUNBOOK.md). |

## Provenance

The deterministic extraction kernel under `kernel/` was developed by **Ashwani /
NBS** (`git@bitbucket.org:northbay/labor_aid_poc.git`) and is imported via
`git subtree` — never hand-edit it; pull updates with `git subtree pull`. The AWS
wrapping (CDK, agent, Lambdas, UI) is built on top per [`docs/`](docs/).

## License

Internal NBS use only. No external distribution without permission.
