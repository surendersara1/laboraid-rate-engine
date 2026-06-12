# CDK ↔ Live Diff Review (Phase 2)

**Status:** read-only. No AWS resource was mutated to produce this. Generated from
`cdk diff` (synth template vs **deployed CloudFormation template**) + live AWS reads
(`lambda list-functions`, `get-function-configuration`).
**Account/region:** 908106425069 / us-east-2 · **Profile:** laboraid
**Branch:** `fix/cdk-reconcile` · **Date:** 2026-06-12
**Raw diffs:** `cdk/reconciliation/diffs/{orchestration,processing,api,ai,storage,validation}.diff`

---

## 0. The one fact that explains every diff

`cdk diff` compares **(1) CDK synth** against **(2) the last-deployed CloudFormation
template** — NOT against the live resource. Our changes were applied to the **live
resources via boto3**, outside CloudFormation. So:

- The **CDK source already describes the intended/live system** (Phase 1 did that).
- The **deployed CFN template is the stale party.**
- Therefore most diffs are **CFN catching up to reality** — deploying converges CFN's
  record to what boto3 already made live. The real-resource change is usually a no-op
  (e.g. CORS, guardrail) or a code re-push (asset hashes).

Two independent verifications of this:
- **Guardrail** `BLOCK → ANONYMIZE`: CDK source already says ANONYMIZE
  (`ai_stack.py:57`); live is already ANONYMIZE; only CFN's record says BLOCK.
- **InputsBucket** `+ CorsConfiguration`: CDK source already has `add_cors_rule`
  (`storage_stack.py:117`); live already has the CORS rule; only CFN's record lacks it.

---

## 1. Per-stack summary

### Orchestration — `[~]` in place, **no imports**, SAFE (order-sensitive)
- `[~]` **MainPipeline** SFN definition: `Classify→GetAgentConfig→AgentEnabled→
  ExtractViaAgent→PublishToAurora` → **`Plan→Synthesize→SynthPublish→Published`**;
  timeout `1800→3600`. In-place update (not replace).
- `[~]` **MainPipelineRole** policy: Classifier/Publisher/ExtractorInvoker refs →
  BatchPlanner/Synthesizer/SynthPublish; drops unused `dynamodb:GetItem`
  (AgentConfigTable) + `kms:Decrypt`.
- `[-]` **ExtractorInvoker** Lambda + its policy destroyed (old per-doc invoker; unused).
- `[~]` **OnInputUpload** rule `ENABLED → DISABLED` (batches start via the API).
- ⚠️ **Ordering:** the role references cross-stack ImportValues for
  BatchPlanner/Synthesizer/SynthPublish ARNs (exported by Processing). **Processing must
  be reconciled before Orchestration deploys**, or the ImportValue won't resolve.

### Processing — 6 `[+]` (ALL exist live → **IMPORT, don't create**) + 3 asset-hash `[~]`
- `[+]` **Synthesizer, SynthPublish, ProfileBuilder, BatchPlanner, OcrPreprocess**
  functions + **SynthDepsLayer** + their roles/policies/log groups.
  **All six functions already exist live** (confirmed via `list-functions`):
  `laboraid-dev-l4-fn-{synthesizer,synth-publish,profile-builder,batch-planner,ocr-preprocess}`.
  → In Phase 3 these are **`cdk import`** (adopt), **never create** — a plain deploy
  would fail `resource already exists`.
- `[~]` **Classifier, LlmExtractor, Publisher**: **asset-hash only** (deploy re-pushes
  repo code). Publisher policy also gains `s3:DeleteObject*`/`Abort*` (read→read-write).
- `[-]` Exports for **LlmExtractor / Classifier / Publisher** dropped — they're no longer
  referenced downstream. The functions themselves remain (orphaned; cleanup later).

### Api — 1 new endpoint `[+]` (exists live → **IMPORT**) + ~18 asset-hash `[~]`
- `[+]` **BatchProcess** function + role + policy + **ApiGatewayV2 Route + Integration +
  Permission** for `POST /v1/batches/process`. The function (`laboraid-dev-l2-fn-batch-process`)
  **and the route exist live** → **import**, don't create.
- `[~]` **~18 API Lambdas** (RatesheetGet, JobStatus, JobList, RatesheetApprove/Reject/
  Publish/Unapprove, ProfileList/Update, UploadPresign, …): **asset-hash only** — deploy
  re-pushes repo code. ⚠️ **This is risk R1 (below).**
- `[~]` **AuthzLayer** new version (`replace`) — dependent functions get the new ARN.
- `[~]` **ProfileList / ProfileUpdate** policies gain `rds-data:*` (the intended 1C.2 grant).

### Ai — `[~]` in place, SAFE
- `[~]` **PiiGuardrail** `BLOCK → ANONYMIZE`. Source already ANONYMIZE; converges CFN.

### Storage — `[~]` in place, SAFE
- `[~]` **InputsBucket** `+ CorsConfiguration` (cloudfront + localhost; GET/HEAD/PUT/POST).
  Source already has it; live already has it; converges CFN.
- `[~]` **SchemaInitFn**: asset-hash only.

### Validation — asset-hash `[~]` only, SAFE-ish
- `[~]` **Checksum, Range, Confidence, ReviewRouter, XlsxRenderer, CsvRenderer,
  ArticlesRenderer, SlackNotifier**: asset-hash only (deploy re-pushes repo code).
  Checksum/Range/Confidence are old validators unused by the new pipeline but still
  deployed; renderers still used by the API.

---

## 2. Risks & open decisions (review before Phase 3)

### 🔴 R1 — Asset-hash re-push could roll back live code
A `cdk deploy` updates the **code** of ~30 live functions to the **repo's current
source**. Safe **only if repo == intended live code** for each. Any function whose live
code is newer than the repo (a boto3 hot-fix never back-ported) would be **reverted**.
- We *did* back-port the demo hot-fixes (ratesheet approve/reject/publish/unapprove
  stale-build, presign expiry).
- **DECISION (chosen): audit repo-vs-live before any P3 deploy.** Phase 3 gains a
  pre-step: download every live function zip and diff against `lambdas/` to catch any
  boto3 hot-fix not back-ported; reconcile into the repo before deploying. No surprise
  rollbacks. (Tracked as Phase-3 step 3.0 in `CDK_SYNC_FIX.md`.)

### ✅ R2 — Execution-role strategy — RESOLVED: Option A (mirror live), applied
**Decision:** mirror live. **Implemented** in `processing_stack.py` (commit on
`fix/cdk-reconcile`): synthesizer + profile-builder now pass `role=self.llm_extractor.role`,
synth-publish passes `role=self.publisher.role`. Re-diff confirms the dedicated
Synthesizer/SynthPublish/ProfileBuilder role-creates are **gone**; their grants now
accumulate onto `LlmExtractorServiceRole` / `PublisherServiceRole` (the `[~]` policies) —
exactly matching live. Those two roles are already CDK-managed, so **no role import needed**
for the reuse trio.

**Remaining (minor):** `batch-planner`, `batch-process`, `ocr-preprocess` have their own
dedicated boto3 roles live (`laboraid-dev-{l4,l2}-role-…`); CDK creates dedicated roles for
them too (`[+]`). On Phase-3 import the function is adopted and CDK creates a fresh
dedicated role, re-pointing the function — a small, controlled IAM change (the orphaned
boto3 roles are deletable afterward). Acceptable; no action now.

<details><summary>Original live-vs-source table (for reference)</summary>

| Function | **Live role** | **CDK source creates** |
|---|---|---|
| synthesizer | `LlmExtractorServiceRole` (reused) | new `SynthesizerServiceRole` |
| profile-builder | `LlmExtractorServiceRole` (reused) | new `ProfileBuilderServiceRole` |
| synth-publish | `PublisherServiceRole` (reused) | new `SynthPublishServiceRole` |
| batch-planner | `laboraid-dev-l4-role-batch-planner` (own) | new `BatchPlannerServiceRole` |
| batch-process | `laboraid-dev-l2-role-batch-process` (own) | new `BatchProcessServiceRole` |
| ocr-preprocess | `laboraid-dev-l4-role-ocr-preprocess` (own) | new `OcrPreprocessServiceRole` |

- **Option A — CDK mirrors live (faithful reconciliation):** change source so synthesizer
  + profile-builder reuse `self.llm_extractor.role` and synth-publish reuses
  `self.publisher.role`. Import becomes clean, **zero IAM change** on deploy. Lowest risk;
  keeps the (less clean) shared-role coupling that's live today. *Requires a small Phase-1
  code change before P3 import.*
- **Option B — keep CDK's dedicated roles (cleaner design):** on deploy, CDK creates 4–6
  new least-privilege roles and **re-points the functions** to them. Better separation,
  but mutates live IAM and the new roles must carry **every** permission the functions
  need today (bedrock, s3 r/w, rds-data, kms) — must be verified or functions break.
- **Recommendation:** **Option A for the sync** (get to IN_SYNC with minimal churn), then
  Option B as a separate, tested follow-up if we want per-function least privilege.

</details>

### 🟡 R3 — Import ordering (cross-stack exports)
Processing exports (BatchPlanner/Synthesizer/SynthPublish ARNs) are consumed by
Orchestration and the batch-process route. **Sequence:** import+reconcile **Processing
first**, then Orchestration, then Api. (Matches CDK_SYNC_FIX Phase 3 order.)

### 🟢 R4 — Orphaned resources (defer)
`LlmExtractor` + `Publisher` (and validators Checksum/Range/Confidence) are unused by the
new pipeline but remain deployed. Not destroyed by this reconciliation. Propose a separate
cleanup PR after IN_SYNC, not during it.

---

## 3. Import set for Phase 3 (physical IDs to adopt)

**Processing** (`cdk import Laboraid-dev-Processing`):
- `laboraid-dev-l4-fn-synthesizer`, `…-synth-publish`, `…-profile-builder`,
  `…-batch-planner`, `…-ocr-preprocess` (+ roles/log-groups per the R2 decision).

**Api** (`cdk import Laboraid-dev-Api`):
- `laboraid-dev-l2-fn-batch-process` + its role + the `POST /v1/batches/process`
  route/integration/permission on the existing HTTP API.

> The SynthDepsLayer + AuthzLayer new versions are **created fresh** on deploy (layers are
> immutable/versioned — not imported).

---

## 4. Post-reconciliation acceptance
- `cdk diff` all 9 stacks → empty.
- Re-run drift detection → all stacks `IN_SYNC`.
- Smoke: process 704 end-to-end (Plan→Synthesize→SynthPublish), ratesheet-get + review
  actions, batch-process route.
