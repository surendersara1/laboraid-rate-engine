# CDK Sync Fix — Plan

How we bring the CDK in the repo back into agreement with the live AWS account,
on a **single shared environment**, without risking the running system.
Companion to `/DEPLOY_FREEZE.md` and `cdk/RECONCILIATION.md`.

> **Current state:** deploys are frozen. Live specs are captured in
> `cdk/reconciliation/*.json`. A CloudFormation **resource scan is COMPLETE**
> (IaC generator) — the account's live resources are indexed and ready to turn
> into a template.

---

## 1. What "out of sync" actually means here

Two distinct problems — they need different tools:

**(a) Drifted, CDK-managed resources** — already in CloudFormation stacks, but
changed out-of-band via boto3. `cdk deploy` would *revert* them.
- Orchestration: Step Functions definition (`Plan→Synthesize→SynthPublish`),
  MainPipelineRole IAM, the upload EventBridge rule (now DISABLED).
- Processing / Api: IAM ServiceRole inline policies (rds-data + invoke grants).
- Ai: PiiGuardrail (ANONYMIZE — already in source, not yet deployed).
- Storage / Validation: S3 bucket + SNS topic config drift (to be reviewed — likely benign).

**(b) Unmanaged resources** — created by boto3, **not in any CloudFormation
stack** (so drift detection can't even see them):
- Lambdas: `synthesizer`, `synth-publish`, `profile-builder` (Processing);
  `batch-planner` (Processing); `batch-process` (Api) — and their IAM roles.
- API route `POST /v1/batches/process`.
- Runtime deps the new Lambdas bundle but the repo doesn't declare: **pypdf, openpyxl**.

## 2. Why not just `cdk migrate --from-scan` over the whole account

`cdk migrate --from-scan` / a full IaC-generator template would scaffold a **new**
low-level CDK app (raw `Cfn*` constructs) describing *everything*. We already have
a clean, hand-written, multi-stack CDK. Replacing it with a generated low-level
app would lose that structure and create a messy takeover of the existing stacks.

**So we use the AWS tools surgically:** the **resource scan + IaC generator** to
*capture* the unmanaged resources (a) as CloudFormation, and `cdk import` to
*adopt* them into the existing stacks — while the drifted managed resources (b)
are fixed by editing the existing CDK source to match the captured live specs.

## 3. The plan (phased; only Phase 3 touches AWS state, gated)

### Phase 0 — Done
- [x] Freeze deploys (`DEPLOY_FREEZE.md`).
- [x] Drift detection on all 9 stacks; live-spec capture (`cdk/reconciliation/`).
- [x] CloudFormation resource scan (IaC generator) — COMPLETE.

### Phase 1 — Author CDK to match live (SAFE — no AWS changes)
1. **Orchestration** — rewrite the SFN definition to `Plan→Synthesize→SynthPublish`
   (from `sfn_definition.json`); add the MainPipelineRole invoke grants; set the
   upload EventBridge rule to **disabled**.
2. **Processing** — add `synthesizer`, `synth-publish`, `profile-builder`,
   `batch-planner` Lambda constructs (config from `new_lambda_configs.json`);
   add the `rds-data` + invoke inline policies to the relevant roles; declare
   **pypdf + openpyxl** (a `requirements.txt` bundled into the function asset, or
   a shared Lambda layer).
3. **Api** — add `batch-process` Lambda + the `POST /v1/batches/process` route;
   add `rds-data-profiles` to ProfileList/ProfileUpdate roles.
4. **Ai** — confirm guardrail = ANONYMIZE (already in source).
5. **Storage / Validation** — inspect the S3/SNS drift; absorb if real, else note.
6. `cdk synth` after each stack — must template cleanly.

### Phase 2 — Validate against live (SAFE — read-only)
1. `cdk diff <stack>` per stack. For drifted-managed resources the diff should
   describe **exactly the live state** (proving our source reproduces it).
2. For the new Lambdas, `cdk diff` will say "will CREATE" — expected; they get
   **imported**, not created (Phase 3). Do **not** deploy yet.
3. Generate the IaC template for the unmanaged resources to confirm exact config:
   ```
   aws cloudformation create-generated-template \
     --generated-template-name laboraid-new-resources \
     --resources <from the completed resource scan>
   aws cloudformation get-generated-template \
     --generated-template-name laboraid-new-resources --format YAML
   ```

### Phase 3 — Adopt + converge (TOUCHES AWS — gated, in a window)
Run in a planned maintenance window, **not** within hours of a demo. Per stack,
smallest blast radius first:
1. **`cdk import <stack>`** — adopt the boto3-created Lambdas/roles into the stack
   (CloudFormation takes ownership of the *existing* physical resources — it does
   **not** recreate or replace them).
2. **`cdk deploy <stack>`** — converges the CloudFormation template to the CDK
   source (= the live state). Because the source now matches live, the change set
   should be minimal/no-op for the drifted resources.
3. After each stack: smoke-test (process 281/704 end to end) before the next.
4. When all stacks deploy clean and `cdk diff` is empty across the board, remove
   `DEPLOY_FREEZE.md`.

## 4. Safety & rollback

- **Phases 1–2 change nothing in AWS** — pure code + read-only diff. We can take
  these all the way to "validated, deployable" with zero risk to the live env.
- **`cdk import` is non-destructive** — it adopts existing resources; it does not
  delete or replace them.
- Before any `cdk deploy`, snapshot the live SFN definition and affected IAM
  policies (already captured); rollback = re-apply the boto3 snapshot.
- One stack at a time, smoke-test between — so a problem is contained and
  reversible, never a big-bang.

## 5. Recommended timing (72h to next demo, single env)

- **Now → next 48h:** do **Phases 1–2** (safe). End state: the repo's CDK
  reproduces the live system and is proven deployable via `cdk diff`. The
  "destruction footing" is gone — freeze guard + correct source.
- **After the next demo (or a quiet window):** do **Phase 3** (import + deploy,
  per stack, smoke-tested). Do **not** run Phase 3 in the 72h before the demo.

This gets us synced safely without betting the one environment on a mid-POC deploy.
