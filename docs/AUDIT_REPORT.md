# POC Build Audit — 2026-06-02 (audit run)

Audit performed against commit `173da6a` on branch `feat/aws-strands-integration`.
Read-only audit — no code modified. All acceptance gates re-run from a clean checkout.

## Summary

- **8 BLOCKER findings** (must fix before push/deploy)
- **9 DRIFT findings** (docs say X, code does Y — pick one and align)
- **7 NICE-TO-HAVE findings** (cosmetic or v1.1+)

Headline: the build is structurally complete and every CI gate (synth / lint / type / unit tests / vitest / build / kernel run) is GREEN. The shortfalls are all semantic — the rate-sheet workflow Lambdas don't actually persist to Aurora or fire EventBridge events the spec requires; no API Lambda enforces the Cognito group authorization the spec mandates per route; and the publish-409 gate trusts a client-supplied `approval_state` instead of reading it from Aurora. Several smaller doc-vs-code drifts (stack count, two pages of authz, etc.) are easy fixes; the missing persistence and authz are the real work.

---

## BLOCKER findings

### B1. `ratesheet-publish` trusts a client-supplied `approval_state` (the 409 gate is bypassable)
- **Where:** `lambdas/api/ratesheet-publish/handler.py:44-52, 56-62`
- **What:** `publish_guard()` reads `approval_state` straight from the request body. A client can POST `{"approval_state": "approved"}` and the handler returns 200. The handler never queries Aurora `rate_periods.approval_state` for the target period; the API stack does grant `aurora` data-API access (`api_stack.py:75, 135-137`) but the handler never uses it. This makes the SOW-critical publish-409 contract a no-op in deployment.
- **Fix:** Look up `approval_state` from Aurora `rate_periods` for the `{local}/{period}` path params using the RDS Data API (the same pattern `ratesheet-list/handler.py:54-65` uses). Pass that value into `publish_guard`. Ignore the request body for state.

### B2. `ratesheet-approve` / `ratesheet-reject` / `ratesheet-unapprove` never write to Aurora and never fire EventBridge
- **Where:** `lambdas/api/ratesheet-approve/handler.py`, `lambdas/api/ratesheet-reject/handler.py`, `lambdas/api/ratesheet-unapprove/handler.py` (entire files)
- **What:** Each handler only computes a transition and returns the new state in the response body. None of them executes an `UPDATE rate_periods SET approval_state='...'` against Aurora, sets `approved_by`/`approved_at`/`rejected_by`/`rejected_at`/`rejection_reason`, or emits `laboraid.rate-sheet.approved` / `.rejected` to the EventBridge `engine` bus. Spec/09 §2.2 (lines 470-472) and §1.1 (lines 262-267) require all of these. Result: the business approval workflow is observable only in HTTP responses and never persisted, so a refresh on the Business UI shows the period still in `pending_review`.
- **Fix:** In each handler, after the transition is decided, (a) execute a parameterized `UPDATE rate_periods SET approval_state=..., approved_by=..., approved_at=NOW() WHERE union_id=... AND start_date=...` via the RDS Data API, then (b) `boto3.client('events').put_events(Entries=[{...}])` to the `laboraid-{env}-l3-eb-engine` bus with `DetailType` `laboraid.rate-sheet.approved` (or `.rejected`). Reject must also persist `rejection_reason` and `rejection_tags`. The API stack already grants Aurora data-API access; add `events:PutEvents` on the engine bus and an `ENGINE_BUS_NAME` env var.

### B3. No API Lambda enforces Cognito group-claim authorization
- **Where:** `cdk/laboraid_cdk/stacks/api_stack.py:8-11` (docstring claims it does); `lambdas/api/*/handler.py` (none check `cognito:groups`)
- **What:** Spec/09 §2.2 mandates per-route group gating (`Admins`-only on `agent-toggle`, `job-abort`, `profile-update`, `costs`; `Business` on approve/reject/unapprove/override/comment; `Admins+Operations` on uploads/jobs/etc). The API stack docstring (line 8-10) says "per-route group authorization is enforced inside each Lambda from the `cognito:groups` claim", but no handler ever reads `cognito:groups`. Grep `cognito:groups` in `lambdas/api/` returns zero hits. The Cognito JWT authorizer at the HTTP-API level enforces authentication only — any authenticated user can call any route.
- **Fix:** Add a shared helper (e.g. `lambdas/api/_shared/authz.py` or inline) that extracts `event['requestContext']['authorizer']['jwt']['claims']['cognito:groups']` (Cognito serializes it as a JSON-encoded list-string in HTTP API v2) and returns 403 if the caller is not in any of the allowed groups. Apply per handler. Per Spec/09 §2.2:
  - `Admins` only: `agent-toggle`, `job-abort`, `profile-update`
  - `Admins` + `Operations`: `upload-presign`, `job-list`, `job-status`, `job-retry`, `agent-list`, `audit-list`, `ratesheet-publish`
  - `Business`: `ratesheet-approve`, `ratesheet-reject`, `ratesheet-unapprove`, `cell-override`, `cell-comment`
  - any authenticated: `profile-list`, `ratesheet-list`, `ratesheet-get`, `ratesheet-audit`

### B4. Step Functions does not read `agent-config.enabled` before invoking the ExtractorAgent
- **Where:** `cdk/laboraid_cdk/sfn/main_pipeline.py:55-58` (Extract is a `sfn.Pass`); `cdk/laboraid_cdk/stacks/orchestration_stack.py:30-60` (no `agent_config_table` parameter)
- **What:** Spec/09 §3.2 line 580 (the `agent-config` table entry): "Step Functions reads `enabled` before invoking an agent and bypasses via Choice state when false." The current state machine is `Classify -> Pass(ExtractViaAgent) -> Validate -> ...`; there is no `GetItem` on `agent-config` and no Choice gating the agent invocation. The agent isn't even invoked (the Extract step is a no-op `sfn.Pass`).
- **Fix:** Inject `agent_config_table` from `app.py` into `OrchestrationStack.__init__`, then in `build_definition` add a `tasks.DynamoGetItem` reading `{"agent_name": "ExtractorAgent"}` and a `sfn.Choice(...).when(Condition.boolean_equals('$.agentCfg.Item.enabled.BOOL', True), <agent invoke task>).otherwise(<bypass: route directly to validation or to AwaitingReview>)`. Replace the placeholder `sfn.Pass` with a real invocation task once an AgentCore-Runtime SFN integration pattern is chosen.

### B5. `StrandsAgentRuntime` synthesizes a CloudFormation resource type that does not exist
- **Where:** `cdk/laboraid_cdk/constructs/strands_agent.py:34-46`
- **What:** The construct creates `CfnResource(type="AWS::BedrockAgentCore::Runtime", properties={...})`. There is no published `AWS::BedrockAgentCore::Runtime` CloudFormation type — AgentCore Runtime currently has no CFN/CDK L1 support (the runner notes this in `BUILD_LOG.md` lines 64-66 and is correct). `cdk synth` succeeds because raw `CfnResource` is not validated at synth time, but `cdk deploy` will fail with `ResourceTypeNotFound`. The property names (`AgentRuntimeName`, `RuntimeImageUri`, `Observability.OtelEndpoint='cloudwatch'`) are speculative.
- **Fix:** Either (a) drop the CFN resource and deploy the agent via the AgentCore CLI as the BUILD doc allows (`agents/extractor/` already has the Dockerfile + agent.py), exporting the runtime ARN as a stack parameter the orchestration stack consumes, or (b) replace the CFN call with a `BootstraplessCustomResource`/`AwsCustomResource` that calls `bedrock-agentcore:CreateAgentRuntime` directly via SDK. Document the chosen path in the construct docstring.

### B6. `ProcessingStack.extractor_runtime` is created but never wired into the pipeline
- **Where:** `cdk/laboraid_cdk/stacks/processing_stack.py:117-130`; `cdk/app.py:99-115` (orchestration constructor)
- **What:** The runtime exists, but `OrchestrationStack` never receives `extractor_runtime` (or its ARN) and `main_pipeline.build_definition` has no agent-invocation task — Stage 2 is the placeholder `sfn.Pass(...)`. End-to-end, an uploaded PDF will trigger the pipeline, classify, then jump straight to validators with no extraction.
- **Fix:** Pass `extractor_runtime.runtime_arn` (and `agent_config_table` per B4) into `OrchestrationStack`. In `main_pipeline.py`, replace the `sfn.Pass` with a real task — for the POC, a `tasks.LambdaInvoke` of an `ExtractorInvoker` Lambda that calls `bedrock-agentcore:InvokeAgentRuntime` synchronously, or an `tasks.CallAwsService` if a service integration becomes available.

### B7. `BedrockAgentCoreApp` runs in module top-level on import; AgentCore container will not boot
- **Where:** `agents/extractor/agent.py:197-214`
- **What:** The AgentCore-Runtime entrypoint is wrapped in a top-level `try: from bedrock_agentcore.runtime import BedrockAgentCoreApp; app = BedrockAgentCoreApp(); @app.entrypoint def invoke(...): ...` block. Inside that `try`, `app.run()` is gated by `if __name__ == "__main__":` — so when AgentCore loads `agent.py` as a module (which is the normal runtime pattern, see `Dockerfile` `CMD ["agent.py"]`), the entrypoint is decorated but `app.run()` is never called. The container exits immediately.
- **Fix:** Either (a) at the bottom of the file, always call `app.run()` when the AgentCore SDK is importable (move it outside `if __name__ == "__main__":`), or (b) change `agents/extractor/Dockerfile` `CMD` to `["python", "-m", "agent"]` and keep the `if __name__ == "__main__":` guard. Verify with `docker run` once a build host is available.

### B8. `domain_name` is hardcoded to `admin-{env}.laboraid.app` with no Route53 zone provisioning
- **Where:** `cdk/laboraid_cdk/config/dev.py:17`, `cdk/laboraid_cdk/config/prod.py:17`, `cdk/laboraid_cdk/stacks/ui_stack.py:60-105`
- **What:** Both env configs hardcode `admin-dev.laboraid.app` and `admin.laboraid.app`. There is no `laboraid.app` registered domain (the build runner has no way to know) and no `HostedZone.from_lookup(...)` anywhere. `UiStack` accepts an optional `hosted_zone` but `app.py:97` instantiates it with no `hosted_zone` arg, so the cert + Route53 record are silently skipped — meaning `cdk deploy` produces an HTTPS distribution with no custom domain and no DNS pointing at it. SecurityStack's Cognito hosted-UI callback URLs (`security_stack.py:85-86`) point at `https://{config.domain_name}/` which will return DNS failure.
- **Fix:** Either (a) parameterize `domain_name` via CDK context (`-c domain_name=...`) and `HostedZone.from_lookup(...)` so the value comes from the deploying account's existing zone, or (b) make `domain_name` optional in `Config` and fall back to the CloudFront default `*.cloudfront.net` domain everywhere (including Cognito callback URLs) when no domain is supplied. Document the chosen contract in `Config`'s docstring.

---

## DRIFT findings

### D1. Stack count: spec says 8, code has 9
- **Where:** `docs/09_Technical_Implementation_Spec.md:214` ("Single CDK app, **8 stacks**"), :1722 ("split into 8 stacks"), :1734, :1548-1558 (lists exactly 8); `cdk/laboraid_cdk/stacks/` (9 files: ai, api, observability, orchestration, processing, security, storage, ui, validation); `docs/BUILD_LOG.md:171`, `README.md:24`, `docs/PR_DESCRIPTION.md:13` all assert "9 stacks". `BUILD_INSTRUCTIONS.md:180` says "Single CDK app (`cdk/app.py`) instantiates 8 stacks".
- **What:** Spec lists `Network → Security → Storage → Processing → AI → Validation → API → UI → Observability` (8; no separate Orchestration stack — Step Functions sits in §3.4 inside Storage). Code has no Network stack but adds a separate `OrchestrationStack`. Net = 9.
- **Recommended resolution:** Align doc to code (keep `OrchestrationStack` as its own; it's a clean separation, and `NetworkStack` is unnecessary because storage stack creates the minimal VPC inline). Update Spec/09 §3 (lines 214-227) to list 9 stacks, and update §11 §1547-1558 stack list. Also add a one-line note in §3 explaining the Network stack was rolled into Storage.

### D2. Lambda count: spec lists 9 API Lambdas in §2.1, code has 19
- **Where:** `docs/09_Technical_Implementation_Spec.md:442-450` (9 Lambdas explicitly named: upload-presign, job-status, ratesheet-list, ratesheet-get, ratesheet-publish, cell-override, ask-cba, profile-list, profile-update); `lambdas/api/` (19 directories — see B3 list)
- **What:** Spec/09 §2.1's asset table predates the §2.2 route table. §2.2 (lines 454-478) actually defines 20 routes across 19 handler dirs (deferring `ask-cba`). BUILD §1 E.1+E.2 correctly enumerates 19. README/BUILD_LOG/PR_DESCRIPTION say "19 API Lambdas" matching code.
- **Recommended resolution:** Align doc to code. Replace §2.1 asset table (lines 442-450) with the full 19-Lambda list matching §2.2, or replace it with a "see §2.2" pointer.

### D3. DDB table count: BUILD says 6, spec says 7, code has 7
- **Where:** `BUILD_INSTRUCTIONS.md:101` ("6 DynamoDB tables"); `docs/09_Technical_Implementation_Spec.md:572-580` (7 tables including `agent-config`); `cdk/laboraid_cdk/stacks/storage_stack.py:156-168` (7 tables created)
- **What:** Spec defines 7 (the §3.2 table list includes `agent-config` at line 580). BUILD line 101 says "6" — predates the agent-config addition. Code matches the spec at 7. BUILD_LOG line 44-45 already flags this.
- **Recommended resolution:** Align BUILD to spec/code. Update `BUILD_INSTRUCTIONS.md:101` from "6 DynamoDB tables" to "7 DynamoDB tables".

### D4. RouteGuard returns silent redirect, not 403
- **Where:** `ui/src/components/RouteGuard.tsx:14-22`; `docs/09_Technical_Implementation_Spec.md:259-260` ("`/admin/*` returns 403 for `Business` users")
- **What:** Spec says denial should be a 403. The component silently `<Navigate to=...>` redirects. No 403 UI is ever shown.
- **Recommended resolution:** Align to spec — render a 403 component (`<div>403 Forbidden — your account does not have access to this area.</div>`) instead of redirecting silently. Keep the redirect-to-landing pattern only for `/` (root) ambiguity. Or align spec to code if the team prefers a frictionless UX — but pick one and document it.

### D5. `/admin/costs` is allowed for Operations users
- **Where:** `ui/src/routes.tsx:21,43` (`ADMIN = ["Admins", "Operations"]` gates the entire `/admin/*` tree including `costs`); `ui/src/admin/Costs.tsx:6` (text says "Admins-only"); `docs/09_Technical_Implementation_Spec.md:367` (Costs is `Admins` only per §1.4)
- **What:** Spec says only `Admins` can see Costs; code lets Operations see it.
- **Recommended resolution:** Align to spec. Wrap `<Costs />` in its own `<RouteGuard groups={["Admins"]}>` and remove from the shared `ADMIN` gate; remove "Admins-only" from the body since the gate enforces it.

### D6. `/admin/agents` shows Agents page to Operations even though only Admins can toggle
- **Where:** `ui/src/routes.tsx:21,40`; `ui/src/components/AgentToggle.tsx:16,32` (toggle disabled for non-Admins); `docs/09_Technical_Implementation_Spec.md:366` (Agents is `Admins, Operations` view; toggle is Admins-only)
- **What:** Spec actually agrees here (line 366: "Agents — agent registry + enable/disable; enable-toggle is **Admins-only**") — the page is admins+operations, the toggle is admins-only. Code matches spec. This is **not a drift** — flagging in DRIFT bucket only because audit dimension D asked us to confirm.
- **Recommended resolution:** None. Code conforms.

### D7. ApproveRejectBar does not show a 'comments per row' affordance
- **Where:** `ui/src/components/ApproveRejectBar.tsx:1-73`; `BUILD_INSTRUCTIONS.md:127` ("comments per row"); spec §1.5 page 7 "comment per row"
- **What:** The bar implements Approve/Reject (with reason) correctly. Per-row comments are spec'd as the `POST /v1/cells/{cell_id}/comment` API (already implemented), but no UI component appears to call it. `CellOverrideModal.tsx` and `RateCellTable.tsx` exist — likely the comment hook should sit on a row, but I did not find one wired up.
- **Recommended resolution:** Add a comment-button column to `RateCellTable.tsx` that opens a small modal and POSTs to `/v1/cells/{cell_id}/comment`. (Audit did not exhaustively read every UI component — verify before fixing.)

### D8. README assertion "kernel reproduces 483 = 100%" is misleading
- **Where:** `README.md:78-79`; `kernel/pipeline/run.py --all` actual run (483 reports OVERALL = 367/441 = 83.2% with 74 blanks)
- **What:** README says "**483 Building = 100%**" — that's the *Building zone* accuracy (Spec/09 §4.1, 1726). The 83.2% number is the whole-sheet figure including a 74-cell apprentice/maintenance block where 483 leaves blanks per kernel's never-fabricate rule. The phrasing in README is technically correct but easy to misread.
- **Recommended resolution:** Update README §"Measured accuracy" to "704 = 99.6%, 483 = 100% on Building zone (83.2% overall including 74 sourced blanks), 537 = 67.4%".

### D9. ObservabilityStack alarms reference wrong CloudWatch dimension values
- **Where:** `cdk/laboraid_cdk/stacks/observability_stack.py:50, 138-145`
- **What:** `api_name = name(env, 'l2', 'apigw', 'main')` is passed as `ApiId` dimension to the API Gateway `5xx` metric. The `ApiId` dimension is the API Gateway-assigned random ID (e.g. `abc123xyz`) — not the resource name. The alarm will never fire because no metric matches. Similarly `aurora_id` happens to work because storage stack sets `cluster_identifier=name(env, 'l3', 'aurora', 'cluster')`.
- **Recommended resolution:** Take a cross-stack ref. Either pass `api_stack.http_api.api_id` into the constructor, or move the API alarm into the API stack. Same idea for Aurora — pass `storage.aurora.cluster_identifier` to be safe.

---

## NICE-TO-HAVE findings

### N1. `cdk synth` requires `npx cdk` not `uv run cdk` — capture in `cdk.json`
- **Where:** `README.md:51,72`, `docs/BUILD_LOG.md:27-28`; `cdk/cdk.json`
- **What:** The Node `cdk` CLI is required (the Python `aws-cdk-lib` does not ship a CLI). README/BUILD_LOG already document this, but `cdk/cdk.json` could pin the CLI version (`"context": {"@aws-cdk/core:newStyleStackSynthesis": true}` etc.) and a `pnpm cdk` script could simplify invocation.

### N2. Aurora schema-init uses public RDS Data API but cluster is in PRIVATE_ISOLATED subnets
- **Where:** `cdk/laboraid_cdk/stacks/storage_stack.py:171-205, 214-249`
- **What:** The DDL Lambda calls `rds-data` (HTTPS) which requires either a VPC endpoint for `rds-data` in the isolated subnets, or the Lambda is in the VPC. The schema-init Lambda is NOT placed in a VPC (no `vpc=` arg) so its calls go over the public internet — fine if Data API is reachable from the public endpoint, which it is. Note for ops: this couples to `enable_data_api=True`. Acceptable.

### N3. `BedrockSpendAlarm` is misnamed — it alarms on InvocationClientErrors, not spend
- **Where:** `cdk/laboraid_cdk/stacks/observability_stack.py:89-99`
- **What:** The alarm cid says `BedrockSpendAlarm` and the purpose tag is `bedrock-spend`, but the metric is `AWS/Bedrock InvocationClientErrors`. Cost alarms should use AWS Budgets or `AWS/Billing EstimatedCharges` (us-east-1 only).
- **Fix:** Rename to `BedrockErrorAlarm` or replace metric with a Budget.

### N4. CI workflow references placeholder `<acct>` in commented deploy job
- **Where:** `.github/workflows/build-and-test.yml:93`
- **What:** `role-to-assume: arn:aws:iam::<acct>:role/laboraid-gha-deploy` — placeholder. Block is commented out so non-fatal, but it should pull the account from a GitHub repo variable when un-commented.

### N5. Bedrock model ID hardcoded; not env-driven
- **Where:** `agents/extractor/agent.py:143` (`modelId: us.anthropic.claude-sonnet-4-6-v1:0`)
- **What:** Sonnet model ID is hardcoded in the agent. Spec/09 §5.5 lists both Sonnet (`anthropic.claude-sonnet-4-6-v1:0`) and Haiku (`anthropic.claude-haiku-4-5-20251001-v1:0`); the agent only uses Sonnet (no Haiku for classification anywhere). The classifier Lambda gets the env var but does not call Bedrock at all (it only has the permission).
- **Fix:** Move model IDs to env vars `BEDROCK_MODEL_SONNET` / `BEDROCK_MODEL_HAIKU` set by the processing stack. Add a Haiku-driven fallback in `lambdas/processing/classifier/handler.py` for the classify-by-filename-regex tiebreaker (Spec/09 §4.2).

### N6. Empty `scripts/`, `profiles/`, `containers/` directories (only `.gitkeep`)
- **Where:** `scripts/.gitkeep`, `profiles/.gitkeep`, `containers/.gitkeep`
- **What:** Spec/09 §11 lists deploy.sh/seed-profiles.sh/invoke-pipeline.sh under `scripts/` and per-union profile YAMLs in `profiles/`. POC accepts that profiles live in `kernel/profiles/`. Acceptable; either remove the empty dirs or add at least one placeholder script.

### N7. Smoke test `tests/e2e/smoke-test.sh` only exercises the kernel (no AWS path)
- **Where:** `tests/e2e/smoke-test.sh:30-51` (`run_local` runs kernel only); BUILD §4.2 expects xlsx + Aurora row + SNS publish within 30s
- **What:** Local mode passes (it just runs the kernel evaluator on 704). The deployed mode is a curl-and-print, doesn't poll the outputs bucket, and never asserts the Aurora row or SNS event. Acceptable for POC as documented in BUILD_LOG, but BUILD §4.2's bullet list is not satisfied.

---

## Acceptance gate results

| Gate | Status | Details |
|---|---|---|
| `cd cdk && uv sync` | PASS | 30 packages resolved, 29 audited |
| `cd cdk && npx cdk synth` | PASS | 9 stacks synthesized to `cdk.out`; `Successfully synthesized` |
| `cd cdk && uv run ruff check .` | PASS | All checks passed |
| `cd cdk && uv run black --check .` | PASS | 31 files would be left unchanged |
| `cd cdk && uv run mypy --strict laboraid_cdk` | PASS | Success: no issues found in 25 source files |
| `cd cdk && uv run pytest` (cdk tests) | PASS | 18 passed in 23.56s |
| `cd cdk && uv run pytest ../lambdas` | PASS | 30 passed in 0.22s |
| `cd ui && corepack pnpm install --frozen-lockfile` | PASS | Lockfile up to date |
| `cd ui && corepack pnpm typecheck` | PASS | tsc --noEmit clean |
| `cd ui && corepack pnpm lint` | PASS | eslint clean (--max-warnings 0) |
| `cd ui && corepack pnpm exec vitest run` | PASS | 4 tests passed |
| `cd ui && corepack pnpm build` | PASS | dist/index.html produced (608 kB main JS warning — non-fatal) |
| `cd kernel && uv sync` | PASS | 34 packages resolved |
| `cd kernel && uv run python pipeline/run.py --all` | PASS | 483 = 83.2% (74 blanks, 0 wrong), 704 = 99.6%, 537 = 67.4% — all above per-union BUILD §4.1 floors |
| `bash tests/e2e/smoke-test.sh` | PASS | LOCAL kernel smoke: 704 = 99.6% (floor 99.0%) — but only exercises kernel, not AWS path (see N7) |

All gates green from a clean checkout.

---

## Hard-rule compliance

| Rule | Status | Details |
|---|---|---|
| `kernel/` untouched | PASS | `git log --oneline -- kernel/` shows only `fa452ee feat: import labor_aid_poc kernel via git subtree`; no post-import commits |
| No static AWS creds | PASS | grep `AKIA[0-9A-Z]{16}`, `aws_secret_access_key`, `password\s*=\s*"..."` returns no matches (only `aws_secretsmanager` import statements in stacks) |
| `MandatoryTagsAspect` applied | PASS | `cdk/app.py:128` adds the aspect at app level; tags assert in `cdk/tests/test_mandatory_tags.py` pass (verified `cdk synth` output via runner notes) |
| No `.ts`/`.tsx` outside `ui/` | PASS | `find . -name '*.ts' -o -name '*.tsx'` excluding `ui/`, `node_modules`, `.git` returns nothing |
| No `package.json` outside `ui/` | PASS | Same, no results |
| No `node_modules/` outside `ui/` | PASS | Same, no results |
| Naming convention `laboraid-{env}-l{N}-{type}-{purpose}` | PASS | grep `laboraid-[A-Z]` in `cdk/` returns nothing (no PascalCase); resource names go through `name()` helper |
| Mandatory tags Aspect tags every stack | PASS by construction | Applied at app level (`cdk/app.py:128`) so descends into all 9 stacks |

---

## Group G status (deferred to kernel harness, not a failure)

- 281 profile present (`kernel/profiles/sprinkler_fitters_281.yaml`): **no**
- 281 extractor registered (`extract_281` in `kernel/pipeline/extract.py` `EXTRACTORS` dict): **no**
- 821 profile present (`kernel/profiles/sprinkler_fitters_821.yaml`): **no**
- 821 extractor registered (`extract_821`): **no**

`kernel/profiles/` has only 537, 483, 704. `EXTRACTORS` dict (extract.py:482-486) lists only those three. This matches BUILD_LOG's expected deferral.

---

## Files inspected

- `BUILD_INSTRUCTIONS.md` (full)
- `README.md` (full)
- `docs/09_Technical_Implementation_Spec.md` (sections 0-4, 11-15)
- `docs/BUILD_LOG.md`, `docs/PR_DESCRIPTION.md` (full)
- `cdk/app.py`
- `cdk/laboraid_cdk/aspects/mandatory_tags.py`
- `cdk/laboraid_cdk/config/__init__.py`, `dev.py`, `prod.py`
- `cdk/laboraid_cdk/constructs/tagged_bucket.py`, `strands_agent.py`
- `cdk/laboraid_cdk/stacks/security_stack.py`, `storage_stack.py`, `processing_stack.py`, `ai_stack.py`, `validation_stack.py`, `api_stack.py`, `ui_stack.py`, `orchestration_stack.py`, `observability_stack.py`
- `cdk/laboraid_cdk/sfn/main_pipeline.py`
- `cdk/assets/schema_init/schema.sql`
- `lambdas/api/ratesheet-publish/handler.py`, `ratesheet-approve/handler.py`, `ratesheet-reject/handler.py`, `ratesheet-unapprove/handler.py`, `agent-toggle/handler.py`, `upload-presign/handler.py`, `job-abort/handler.py`, `cell-override/handler.py`, `ratesheet-list/handler.py`
- `agents/extractor/agent.py`, `steering.py`
- `kernel/pipeline/extract.py` (head + EXTRACTORS table), `kernel/pipeline/run.py` (head)
- `ui/src/App.tsx`, `routes.tsx`, `components/RouteGuard.tsx`, `ApproveRejectBar.tsx`, `AgentToggle.tsx`, `admin/Costs.tsx`
- `tests/e2e/smoke-test.sh`
- `.github/workflows/build-and-test.yml`
