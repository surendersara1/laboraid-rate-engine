# CDK Sync Fix — Detailed Plan (REVIEW BEFORE EXECUTION)

Reconcile the repo's CDK with the live AWS account on a **single shared env**,
safely. **Nothing in Phases 1–2 changes AWS.** Only Phase 3 touches AWS, and it's
gated to a maintenance window. Companion to `/DEPLOY_FREEZE.md`,
`cdk/RECONCILIATION.md`, captured specs in `cdk/reconciliation/*.json`.

**Legend:** ☐ todo · 🟢 safe (no AWS change) · 🟡 read-only AWS · 🔴 mutates AWS (gated)

---

## Phase 0 — Foundation  ✅ DONE
- [x] `DEPLOY_FREEZE.md` committed (no accidental deploy)
- [x] Drift detection on all 9 stacks (27 drifted resources mapped)
- [x] Live specs captured → `cdk/reconciliation/{sfn_definition,new_lambda_configs,iam_inline_policies}.json`
- [x] CloudFormation **resource scan COMPLETE** (IaC generator, 104 resource types)

---

## Phase 1 — Author CDK to match live  ✅ DONE (commit 9927d92, branch fix/cdk-reconcile — no AWS changes)

### 1A · Orchestration stack (highest risk — the SFN)
- [x] 1A.1 Rewrite `cdk/laboraid_cdk/sfn/main_pipeline.py` → `Plan → Synthesize → SynthPublish` (+ `Published`/`PipelineFailed`), matching retry/catch in `sfn_definition.json`
- [x] 1A.2 Update `stacks/orchestration_stack.py` — `build_definition` now takes `batch_planner`, `synthesizer`, `synth_publish` (drop classifier/checksum/range/confidence/render wiring)
- [x] 1A.3 Add MainPipelineRole grants: planner/synthesizer/synth-publish `grant_invoke`
- [x] 1A.4 Set the upload EventBridge rule `enabled=False` (matches live DISABLED)

### 1B · Processing stack (the 4 new Lambdas + IAM + deps)
- [x] 1B.1 Add `synthesizer` Lambda (config from `new_lambda_configs.json`: py3.12 arm64, 1024MB, 900s, powertools layer; env INPUTS/OUTPUTS_BUCKET, BEDROCK_GUARDRAIL_ID, SYNTH_MODEL_ID=opus-4-5, AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, PROFILE_BUILDER_FN, PROFILES_DIR)
- [x] 1B.2 Add `synth-publish` Lambda (Aurora env; role w/ RDS Data API)
- [x] 1B.3 Add `profile-builder` Lambda (Bedrock + S3 + RDS env)
- [x] 1B.4 Add `batch-planner` Lambda (invokes classifier)
- [x] 1B.5 **Declare runtime deps** `pypdf` + `openpyxl` — chosen approach: a shared **Lambda layer** built from `requirements.txt` (used by synthesizer + profile-builder). (Alt: per-function `BundlingOptions`.)
- [x] 1B.6 Bundle shared modules into the synth-deps layer: `master_data.py`, `pdf_utils.py`. (Profiles are NOT bundled — they live in Aurora `unions.profile_yaml`, loaded at runtime; unknown unions auto-onboard via profile-builder.)
- [x] 1B.7 IAM: `LlmExtractorServiceRole` += inline `rds-data-profiles` (ExecuteStatement/BatchExecuteStatement on cluster + GetSecretValue on secret) and `invoke-profile-builder` — verbatim from `iam_inline_policies.json`
- [x] 1B.8 Grants for new roles: `bedrock:InvokeModel` (+ guardrail), S3 read inputs / read-write outputs, RDS Data API on cluster + secret

### 1C · Api stack (batch endpoint + IAM)
- [x] 1C.1 Add `batch-process` Lambda + route `POST /v1/batches/process` (JWT authorizer, Business/Operations/Admins gate, `STATE_MACHINE_ARN` env, `states:StartExecution` grant)
- [x] 1C.2 IAM: `ProfileListServiceRole` + `ProfileUpdateServiceRole` += inline `rds-data-profiles`
- [x] 1C.3 Verify `ratesheet-get` / `job-status` env (presign expiry) + roles match live

### 1D · Ai / Storage / Validation
- [x] 1D.1 Ai: confirm `PiiGuardrail` = ANONYMIZE (already in source — verify only)
- [x] 1D.2 Storage: inspect the 6 S3-bucket drifts (likely notification/policy config) — absorb if real, document if benign
- [x] 1D.3 Validation: inspect the 2 SNS-topic drifts — same treatment

### 1E · Build gate
- [x] 1E.1 `cdk synth` the whole app — **must template with no errors**
- [x] 1E.2 Commit Phase 1 on a branch `fix/cdk-reconcile` (not main) for review

**Phase 1 acceptance:** `cdk synth` clean; CDK source describes the live system.

---

## Phase 2 — Validate against live  ✅ DONE (read-only — see cdk/reconciliation/DIFF_REVIEW.md)

- [x] 2.1 `cdk diff Laboraid-dev-Orchestration` — diff should describe **exactly** the live SFN/IAM/rule change
- [x] 2.2 `cdk diff Laboraid-dev-Processing` — new Lambdas show as **CREATE** (will be *imported* in P3, not created); IAM matches
- [x] 2.3 `cdk diff Laboraid-dev-Api` — batch-process + route + IAM
- [x] 2.4 `cdk diff` Ai / Storage / Validation — minimal/expected only
- [x] 2.5 `aws cloudformation create-generated-template` + `get-generated-template` for the 5 unmanaged Lambdas+roles+route → confirm the exact resource set/config to import
- [x] 2.6 Write `cdk/reconciliation/DIFF_REVIEW.md` — per-stack summary of what each deploy would change

**Phase 2 acceptance:** every diff understood and expected. **🚦 GATE: you review `DIFF_REVIEW.md` and approve before Phase 3.**

---

## Phase 3 — Adopt + converge  🔴 (mutates AWS — gated, maintenance window)

> Run in a planned window, **never** in the 72h before a demo. One stack at a
> time, smallest blast radius first, smoke-test between each.

- [ ] 3.1 Re-snapshot live (fresh SFN def + IAM) → `cdk/reconciliation/rollback/`
- [ ] 3.2 `cdk import Laboraid-dev-Processing` — adopt synthesizer / synth-publish / profile-builder / batch-planner (map physical names; **non-destructive** — no recreate)
- [ ] 3.3 `cdk import Laboraid-dev-Api` — adopt batch-process
- [ ] 3.4 `cdk deploy Laboraid-dev-Storage` → smoke-test; then `Validation`, then `Ai`
- [ ] 3.5 `cdk deploy Laboraid-dev-Processing` → **smoke-test: process 281 end-to-end**
- [ ] 3.6 `cdk deploy Laboraid-dev-Api` → **smoke-test: ratesheet-get + review actions**
- [ ] 3.7 `cdk deploy Laboraid-dev-Orchestration` (the SFN — last/biggest) → **smoke-test: full 704 pipeline**
- [ ] 3.8 `cdk diff` all 9 stacks → **empty**; re-run drift detection → **IN_SYNC**
- [ ] 3.9 Remove `DEPLOY_FREEZE.md`; merge `fix/cdk-reconcile` → main; tag release

**Phase 3 acceptance:** all stacks `IN_SYNC`, all smoke tests pass, freeze lifted.

---

## Rollback (per step in Phase 3)
- `cdk import` fails → no change (import is atomic); fix the construct, retry.
- A `cdk deploy` regresses behavior → CloudFormation auto-rolls-back the stack; if
  partial, re-apply the boto3 snapshot (`rollback/`) for SFN/IAM and re-freeze.
- Each stack is independent + smoke-tested, so blast radius is one stack.

## Timing (72h to next demo, single env)
- **Now → +48h:** Phases 1 & 2 (zero AWS change). Outcome: CDK reproduces live,
  proven by `cdk diff`; destruction risk already removed by the freeze + correct source.
- **After the next demo / quiet window:** Phase 3, per the gate above.

## Open decisions for your review
1. **Deps packaging** — shared Lambda layer for pypdf+openpyxl (recommended) vs per-function bundling?
2. **synth-publish role** — reuse the Publisher role (has RDS) vs a dedicated role?
3. **Phase 3 window** — when? (Proposed: after the next demo.)
4. **Branch** — do Phases 1–2 on `fix/cdk-reconcile` and PR for review (recommended)?
