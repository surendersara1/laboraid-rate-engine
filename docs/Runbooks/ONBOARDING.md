# LaborAid Rate Engine — Onboarding

For engineers joining the POC build.

## Repo layout

```
cdk/            Python CDK — 9 stacks (aws-cdk-lib). App entry: cdk/app.py.
lambdas/        Python 3.12 Lambdas: api/ (19), processing/, validation/, rendering/.
agents/         ExtractorAgent (Strands) container.
kernel/         Ashwani's deterministic pipeline (git subtree — DO NOT hand-edit).
ui/             React 18 + TS SPA (Vite). The ONLY TypeScript in the repo.
docs/           Specs + this onboarding + RUNBOOK + ARCHITECTURE.
```

## Toolchain

| Layer | Tools |
|---|---|
| CDK + Lambdas + agents + kernel | `uv`, `ruff`, `black`, `mypy --strict`, `pytest` |
| UI (`ui/` only) | `pnpm` (via `corepack pnpm`), ESLint, Prettier, `tsc`, Vitest |

```bash
# Backend
cd cdk && uv sync && npx cdk synth
uv run ruff check . && uv run black --check . && uv run mypy --strict laboraid_cdk
uv run pytest
cd ../lambdas && (cd ../cdk && uv run pytest ../lambdas)   # importlib mode

# UI
cd ui && corepack pnpm install
corepack pnpm typecheck && corepack pnpm lint && corepack pnpm exec vitest run
corepack pnpm build
```

## Conventions you must follow

- **Language split:** CDK is Python, not TypeScript. No `.ts`/`package.json`
  outside `ui/`. UI is React, never Streamlit.
- **Naming:** use `laboraid_cdk.util.naming.name(env, layer, type_, purpose)` —
  no hardcoded resource names.
- **Tags:** 13 mandatory tags via `MandatoryTagsAspect` (tags L1 CfnResources).
- **IAM:** per-Lambda/agent roles live in their consuming stack (never grant a
  Security-stack role a downstream resource — it cycles with the CMK).
- **Lambdas:** Powertools imported under a `try/except ModuleNotFoundError` shim;
  pure logic in module-level functions; tests `importlib`-load the handler.
- **Kernel rule:** never fabricate a value; blank + flag in `<union>.gaps.md`.
  Never modify `kernel/` directly.

## Where things are wired

- `cdk/app.py` instantiates all stacks + applies the tag Aspect.
- The Step Functions pipeline definition is `cdk/laboraid_cdk/sfn/main_pipeline.py`.
- The two-persona routing is `ui/src/routes.tsx` + `ui/src/components/RouteGuard.tsx`.
- The publish 409 guard is `lambdas/api/ratesheet-publish/handler.py::publish_guard`.

## First task ideas

- Run `npx cdk synth` and read one stack's template to see the tags + naming.
- Add a union extractor (281/821) via the kernel harness (Group G).
- Trace the happy path in `docs/09 §5` end-to-end.

See [`BUILD_INSTRUCTIONS.md`](../BUILD_INSTRUCTIONS.md) for the full build queue
and [`docs/BUILD_LOG.md`](BUILD_LOG.md) for what's done / pending.
