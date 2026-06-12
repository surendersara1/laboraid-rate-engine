# Phase 3 — Execution Runbook (Option 2: keep the shared synth-deps layer)

**Goal:** bring the live system under CDK management and reach `cdk diff` empty / drift
`IN_SYNC`, without breaking the live product. Run in a **maintenance window** (uploads
paused) over the weekend. Branch `fix/cdk-reconcile`. Rollback snapshot in
`cdk/reconciliation/rollback/`.

> **Decision recap:** R2 = roles mirror live (done). Packaging = **Option 2, keep the
> shared layer**. Only **synthesizer + profile-builder** use that layer; they are the only
> 2 functions whose package changes.

---

## Two CloudFormation constraints this runbook solves

1. **Layer-create vs import.** A CFN *import* change set can only import existing
   resources — it can't also *create* the new `synth-deps` layer that synthesizer/
   profile-builder reference. → We don't try to import those two; see mechanism below.
2. **Export-in-use.** `Laboraid-dev-Orchestration` currently imports the old
   `Classifier` / `Publisher` / `LlmExtractor` ARN exports from Processing (verified via
   `list-imports`). The new template drops them, but CFN **blocks deleting an export
   that's in use**. → Retain them with `stack.exportValue(...)` until the new
   Orchestration (which doesn't use them) is deployed; drop later in the cleanup PR.

---

## Code prep (NON-mutating — do now, re-synth, re-diff, commit)

- [ ] **P-1** In `processing_stack.py`, retain the 3 soon-to-be-orphaned exports so the
  Orchestration deploy doesn't deadlock:
  ```python
  self.export_value(self.classifier.function_arn)
  self.export_value(self.publisher.function_arn)
  self.export_value(self.llm_extractor.function_arn)
  ```
  (Also the `Ref` export for LlmExtractor if `cdk diff` still shows it dropped.)
- [ ] **P-2** `cdk synth` clean; `cdk diff Processing` no longer shows those exports as
  removed. Commit on the branch.

---

## Execution (MUTATING — only on your explicit "deploy now", step by step)

### Step 0 — Pre-flight (read-only)
- [ ] Confirm no Step Functions executions running.
- [ ] Baseline: run a 281 batch on the **current** live system → capture the good output
  (so we can compare after).
- [ ] Refresh the rollback snapshot.

### Step 1 — Processing (the core; biggest step)
**Mechanism (recommended): recreate the 6 functions.** They were boto3-created (not in
CFN), so CDK can't adopt them without either import (blocked by the layer for 2 of them)
or recreate. In a window the brief gap is harmless, and the result is 100% CDK-managed.
- [ ] 1a. Delete the 6 boto3 functions: `synthesizer, synth-publish, profile-builder,
  batch-planner, ocr-preprocess` (l4) + `batch-process` (l2). *(Names are stable, so the
  recreated ARNs match — SFN/API references stay valid.)*
- [ ] 1b. `cdk deploy Laboraid-dev-Processing` — creates the 6 + `synth-deps` layer,
  updates Classifier/LlmExtractor/Publisher code + the shared roles (Option A), retains
  the old exports (P-1).
- [ ] 1c. **Smoke:** invoke `synthesizer` directly on the 281 inputs → expect 15/15 rows,
  180/180 cells; invoke `profile-builder` on a CBA → profile written to Aurora.
- 🔙 Rollback: if 1b fails, CFN rolls back the stack; re-create the 6 from
  `rollback/fn_*.json` via `create-function`, restore.

> **Zero-downtime alternative to 1a/1b** (more moving parts): `cdk import` the 4 layer-free
> functions (batch-planner, synth-publish, ocr-preprocess, batch-process) using a hand-
> authored `--resource-mapping`, then recreate only synthesizer + profile-builder. Use
> only if the window can't tolerate the brief gap.

### Step 2 — Orchestration (the SFN)
- [ ] 2a. `cdk deploy Laboraid-dev-Orchestration` — new `Plan→Synthesize→SynthPublish`
  definition (now resolves the new Processing exports), rule stays `DISABLED`, drops the
  ExtractorInvoker. After this, the old Classifier/Publisher/LlmExtractor exports are
  unused.
- [ ] 2b. **Smoke:** `StartExecution` on a 704 batch payload → runs to `Published`; check
  Aurora rows + S3 CSV/XLSX.
- 🔙 Rollback: restore the SFN definition from `rollback/sfn_main.json`
  (`update-state-machine`).

### Step 3 — Api
- [ ] 3a. `cdk deploy Laboraid-dev-Api` — batch-process is already recreated under CDK in
  Step 1; this converges the route/integration + the ~asset-hash code re-push +
  ProfileList/Update RDS grants + AuthzLayer.
- [ ] 3b. **Smoke:** `ratesheet-get`, a review action (approve/reject), `POST
  /v1/batches/process` from the UI.

### Step 4 — Storage, Validation, Ai (converge-only, low risk)
- [ ] 4a. `cdk deploy Laboraid-dev-Storage` (CORS record), `…-Validation` (legacy code
  re-push), `…-Ai` (guardrail ANONYMIZE record). Smoke: upload presign works; guardrail
  intact.

### Step 5 — Verify IN_SYNC
- [ ] 5a. `cdk diff` all 9 stacks → **empty**.
- [ ] 5b. Drift detection on all 9 → **IN_SYNC**.

### Step 6 — Close out
- [ ] 6a. Remove `DEPLOY_FREEZE.md`.
- [ ] 6b. Merge `fix/cdk-reconcile` → main; tag `cdk-reconciled-v1`.

---

## Deferred to a separate cleanup PR (after IN_SYNC — NOT in this window)
- Remove the `export_value` retentions (P-1) once nothing imports them.
- Delete the Group-C legacy functions (llm-extractor, extractor-invoker, ocr-preprocess,
  publisher, validation/*, rendering articles+csv) — see DIFF_REVIEW R4.

## Smoke-test asset locations
- 281 / 704 batch inputs: (use the same S3 keys captured in the rollback baseline, Step 0).
- Expected 281 = 15 rows / 180 cells; 704 = 13 rows (per prior validation).
