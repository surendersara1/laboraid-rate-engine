# Audit Verification — 2026-06-03 08:16

Verified against commit `e9588c1` on branch `feat/aws-strands-integration`.
Independent re-audit of every B/D/N finding in `docs/AUDIT_REPORT.md`. All
assessments below were made by reading the actual current source (not commit
messages or BUILD_LOG entries).

## Summary
- BLOCKERS: 8 fixed, 0 still open, 0 partially fixed
- DRIFT: 8 fixed, 1 partially fixed (D1 — two stale "8 stacks" mentions remain in Spec §14)
- NICE-TO-HAVE: 0 addressed, 7 not addressed (all explicitly accepted as v1.1+)

---

## BLOCKERS

### B1. `ratesheet-publish` trusts a client-supplied `approval_state`
- **Status:** FIXED
- **Verified by:** `lambdas/api/ratesheet-publish/handler.py:63-114`
- **Evidence:** New `read_approval_state(local, period)` issues `SELECT rp.approval_state FROM rate_periods rp JOIN unions u ON rp.union_id = u.id WHERE u.local = :local AND rp.start_date = :period` via the RDS Data API; the handler's main flow calls `state = read_approval_state(local, period)` (line 108), then `publish_guard(state)` (line 111). The request body is never inspected for `approval_state`. Comment at line 107: "Authoritative state from Aurora — the request body is intentionally ignored."
- **Residual:** None.

### B2. `ratesheet-approve` / `ratesheet-reject` / `ratesheet-unapprove` never write to Aurora and never fire EventBridge
- **Status:** FIXED
- **Verified by:** `lambdas/api/ratesheet-approve/handler.py:66-102, 122-135`; `ratesheet-reject/handler.py:72-115, 122-157`; `ratesheet-unapprove/handler.py:68-103, 110-136`; wiring in `cdk/laboraid_cdk/stacks/api_stack.py:62-82, 120-158`
- **Evidence:** Approve: `UPDATE rate_periods SET approval_state='approved', approved_by=:by, approved_at=NOW() WHERE union_id = (SELECT id FROM unions WHERE local = :local) AND start_date = :period`, followed by `events.put_events(... DetailType="laboraid.rate-sheet.approved" ...)`. Reject: same shape with `rejection_reason`/`rejection_tags` (incl. `CAST(:tags AS TEXT[])`) + `laboraid.rate-sheet.rejected`. Unapprove: resets to `pending_review` + emits `laboraid.rate-sheet.unapproved`. The API stack adds `engine_bus.grant_put_events_to(fn)` (line 156-157) and sets `ENGINE_BUS_NAME` env var (line 127), plus `aurora.grant_data_api_access(fn)` for all three.
- **Residual:** None.

### B3. No API Lambda enforces Cognito group-claim authorization
- **Status:** FIXED
- **Verified by:** `lambdas/api/_shared/python/authz.py` (whole file); every gated handler imports `authz` and calls `denied = authz.enforce_groups(event, ALLOWED_GROUPS)` at the top of `handler(...)`; `cdk/laboraid_cdk/stacks/api_stack.py:108-118, 142` attaches the shared layer to every Lambda.
- **Evidence:** `extract_groups` parses `requestContext.authorizer.jwt.claims['cognito:groups']` in all the shapes Cognito surfaces (list, JSON string, bracketed space/comma list). `enforce_groups` returns 403 when no allowed group matches. ALLOWED_GROUPS surveyed across all 19 handlers matches Spec §2.2 exactly:
  - Admins-only: `agent-toggle`, `job-abort`, `profile-update`
  - Admins+Operations: `upload-presign`, `job-list`, `job-status`, `job-retry`, `agent-list`, `audit-list`, `ratesheet-publish`
  - Business: `ratesheet-approve`, `ratesheet-reject`, `ratesheet-unapprove`, `cell-override`, `cell-comment`
  - Any authenticated (correctly no gate): `profile-list`, `ratesheet-list`, `ratesheet-get`, `ratesheet-audit`
- **Residual:** None.

### B4. Step Functions does not read `agent-config.enabled`
- **Status:** FIXED
- **Verified by:** `cdk/laboraid_cdk/sfn/main_pipeline.py:54-73, 110-120`; `cdk/laboraid_cdk/stacks/orchestration_stack.py:49, 103, 139`
- **Evidence:** `tasks.DynamoGetItem` at `GetAgentConfig` reads `{"agent_name": "ExtractorAgent"}` against `agent_config_table` into `$.agentCfg` (line 67-73). A `Choice` state `AgentEnabled` then branches on `Condition.boolean_equals("$.agentCfg.Item.enabled.BOOL", True)` — when enabled go to `extract`, otherwise straight to `validate` (line 111-115). The chain is `classify -> get_agent_cfg -> agent_gate` (line 120). `agent_config_table` is plumbed from `app.py:139` into the orchestration stack constructor.
- **Residual:** None.

### B5. `StrandsAgentRuntime` synthesizes a CFN type that doesn't exist
- **Status:** FIXED
- **Verified by:** `cdk/laboraid_cdk/constructs/strands_agent.py:55-103`
- **Evidence:** The `CfnResource(type="AWS::BedrockAgentCore::Runtime", ...)` has been replaced with `cr.AwsCustomResource` calling `service="bedrock-agentcore"` actions `CreateAgentRuntime` / `UpdateAgentRuntime` / `DeleteAgentRuntime` (lines 58-84). Policy grants the matching bedrock-agentcore + `iam:PassRole` actions (lines 85-99). `install_latest_aws_sdk=True` (line 102) because the API is newer than Lambda's bundled SDK. The `runtime_arn` property surfaces `agentRuntimeArn` from the SDK response so downstream consumers stay unchanged.
- **Residual:** None. The `TODO(when AWS ships AWS::BedrockAgentCore::Runtime L1)` docstring note (line 14-17) is appropriate.

### B6. `ProcessingStack.extractor_runtime` not wired into the pipeline
- **Status:** FIXED
- **Verified by:** `cdk/laboraid_cdk/stacks/orchestration_stack.py:50, 58-105`; `cdk/app.py:140`; `lambdas/processing/extractor-invoker/handler.py` exists.
- **Evidence:** `OrchestrationStack.__init__` now takes `extractor_runtime_arn` (line 50). It creates an `ExtractorInvoker` Lambda with `bedrock-agentcore:InvokeAgentRuntime` permission (line 58-74), wraps it in a `tasks.LambdaInvoke` named `ExtractViaAgent` with retries (line 75-91), and passes it into `build_definition(extract_task=extract_task)` (line 104). The placeholder `sfn.Pass` is no longer the runtime path. `app.py` line 140 passes `processing.extractor_runtime.runtime_arn`.
- **Residual:** None.

### B7. `BedrockAgentCoreApp` never starts under container CMD
- **Status:** FIXED
- **Verified by:** `agents/extractor/agent.py:199-221`
- **Evidence:** `app.run()` is now called unconditionally at module top-level inside `try: from bedrock_agentcore.runtime import BedrockAgentCoreApp ... app.run()` (line 217). The `__name__ == "__main__"` guard is gone. The accompanying comment (line 212-216) explicitly documents this is required because AgentCore loads the module on container start (it does not run it as `__main__`). Dockerfile `CMD ["python", "agent.py"]` (Dockerfile last line) now correctly starts the invoke server.
- **Residual:** None.

### B8. `domain_name` is hardcoded to `admin-{env}.laboraid.app`
- **Status:** FIXED
- **Verified by:** `cdk/laboraid_cdk/config/dev.py:22`, `prod.py:22`; `cdk/laboraid_cdk/config/__init__.py:34-51`; `cdk/laboraid_cdk/stacks/ui_stack.py:66-120`; `cdk/laboraid_cdk/stacks/security_stack.py:38, 49-54, 98-99`; `cdk/app.py:36-38, 52-57`
- **Evidence:** Both env configs set `domain_name=None`. `Config.has_custom_domain` returns whether one is configured. `UiStack` only invokes `HostedZone.from_lookup` + ACM cert when `has_custom_domain` (lines 69-79); otherwise `app_url` falls back to `https://{distribution.distribution_domain_name}` (line 120). `SecurityStack` accepts `app_url` from UI and uses it for Cognito callback/logout URLs (line 98-99). `app.py:36-38` allows context override `-c domain_name=...`.
- **Residual:** None.

---

## DRIFT

### D1. Stack count: spec says 8, code has 9
- **Status:** PARTIAL
- **Verified by:** Spec/09 lines 214, 232, 1546 now say "9 stacks"; but lines 1720 and 1732 still say "8 stacks". Code: `cdk/laboraid_cdk/stacks/` has exactly 9 stack files (ai, api, observability, orchestration, processing, security, storage, ui, validation).
- **Evidence:** `docs/09_Technical_Implementation_Spec.md:214` "Single CDK app, **9 stacks** with cross-stack references"; line 232 "Net result is **9 stacks**"; line 1546 "9 stacks (NetworkStack rolled into Storage)". But line 1720 still reads "split into 8 stacks that can be deployed independently" and line 1732 "CDK deployment across 8 stacks".
- **Residual:** Update Spec/09 §14 lines 1720 and 1732 from "8 stacks" → "9 stacks" for full doc/code alignment.

### D2. Lambda count: spec lists 9, code has 19
- **Status:** FIXED
- **Verified by:** `docs/09_Technical_Implementation_Spec.md:447`
- **Evidence:** §2.1 asset table row now reads "API Lambdas (19) | `laboraid-{env}-l2-fn-<handler>` — **see the §2.2 route table for the authoritative list**". §2.2 (lines 454-477) defines the 20 routes / 19 handler dirs.
- **Residual:** None.

### D3. DDB table count: BUILD says 6, spec says 7, code has 7
- **Status:** FIXED
- **Verified by:** `BUILD_INSTRUCTIONS.md:101` ("7 DynamoDB tables (incl. agent-config)"); `cdk/laboraid_cdk/stacks/storage_stack.py:156-168` (7 `_table(...)` calls — files, jobs, review, overrides, cadence, idempotency, agent-config)
- **Evidence:** Wording matches. Code count matches.
- **Residual:** None.

### D4. RouteGuard silent redirect instead of 403
- **Status:** FIXED
- **Verified by:** `ui/src/components/RouteGuard.tsx:1-22`; `ui/src/components/Forbidden403.tsx:1-13`
- **Evidence:** `RouteGuard` now returns `<Forbidden403 />` on denial (line 19). `Forbidden403` renders an explicit "403 — Forbidden" page with subtext "Your account does not have access to this area."
- **Residual:** None.

### D5. `/admin/costs` allowed for Operations
- **Status:** FIXED
- **Verified by:** `ui/src/routes.tsx:21-23, 44-51`
- **Evidence:** `ADMINS_ONLY = ["Admins"]` constant added (line 22). The `costs` route is wrapped in its own inner `<RouteGuard groups={ADMINS_ONLY}>` block (lines 45-50), so Operations users hit the 403 even though the parent `/admin/*` gate admits them.
- **Residual:** None.

### D6. `/admin/agents` page for Operations even though toggle is Admins-only
- **Status:** FIXED (already conforming — audit confirmed in original report this was not a real drift)
- **Verified by:** `ui/src/components/AgentToggle.tsx:15-37`
- **Evidence:** Toggle button has `disabled={!isAdmin || busy}` (line 31); `isAdmin = groups.includes("Admins")` (line 16). Page in admin route tree is visible to Admins+Operations, but toggle gated to Admins.
- **Residual:** None.

### D7. ApproveRejectBar has no per-row comment affordance
- **Status:** FIXED
- **Verified by:** `ui/src/components/RateCellTable.tsx:9, 22, 33-48, 88-97`; `ui/src/components/CellCommentModal.tsx:1-55`
- **Evidence:** `RateCellTable` has a new display column with a "💬 Comment" button (lines 33-48). Clicking sets `commentCellId` state and renders `<CellCommentModal cellId={commentCellId} ...>`. The modal POSTs to `/v1/cells/${cellId}/comment` (line 20-23). `event.stopPropagation()` prevents the row-click from firing as well.
- **Residual:** None.

### D8. README "kernel reproduces 483 = 100%" misleading
- **Status:** FIXED
- **Verified by:** `README.md:75-80`
- **Evidence:** Line 77-78 now reads "**704 = 99.6%**, **483 = 100% on the Building zone (83.2% overall including 74 sourced blanks)**, **537 = 67.4%**". Section header is "Measured accuracy (kernel regression guard)".
- **Residual:** None.

### D9. ObservabilityStack alarms reference wrong CloudWatch dimensions
- **Status:** FIXED (for API Gateway; Aurora left as-is per code-comment)
- **Verified by:** `cdk/laboraid_cdk/stacks/observability_stack.py:40, 54, 144`; `cdk/app.py:146-152`
- **Evidence:** `ObservabilityStack.__init__` now takes `api_id: str` (line 40). The API 5xx alarm sets `dimensions_map={"ApiId": api_dimension_id}` where `api_dimension_id = api_id` from the constructor (line 54, 144). `app.py:151` passes `api_id=api.http_api.api_id`. Aurora alarm still uses the naming-helper `aurora_id` — this is a true match because `storage_stack.py:188` sets `cluster_identifier=name(env, 'l3', 'aurora', 'cluster')`, so the dimension `DBClusterIdentifier` resolves correctly (the audit itself flagged Aurora as "happens to work").
- **Residual:** None.

---

## NICE-TO-HAVE

### N1. `cdk.json` could pin CLI version / pnpm cdk script
- **Status:** NOT ADDRESSED
- **Verified by:** `cdk/cdk.json` unchanged in commit log; no `package.json` in `cdk/`.
- **Residual:** Cosmetic — acceptable for POC.

### N2. Aurora schema-init via public RDS Data API
- **Status:** NOT ADDRESSED — but the audit already marked this "Acceptable" (no fix needed).
- **Verified by:** `cdk/laboraid_cdk/stacks/storage_stack.py` schema-init Lambda has no `vpc=` arg; uses `rds-data` over the public endpoint as before.
- **Residual:** None required.

### N3. `BedrockSpendAlarm` is misnamed
- **Status:** NOT ADDRESSED
- **Verified by:** `cdk/laboraid_cdk/stacks/observability_stack.py:94-95`
- **Evidence:** Still `cid="BedrockSpendAlarm"` and `alarm_name="laboraid-{env}-alarm-bedrock-spend"` while the metric is `AWS/Bedrock InvocationClientErrors`.
- **Residual:** Cosmetic; rename or replace with a Budget.

### N4. CI workflow uses placeholder `<acct>`
- **Status:** NOT ADDRESSED
- **Verified by:** `.github/workflows/build-and-test.yml:93` still reads `role-to-assume: arn:aws:iam::<acct>:role/laboraid-gha-deploy`. Block remains commented out so non-fatal.
- **Residual:** Replace with a repo variable when uncommented for deploy.

### N5. Bedrock model ID hardcoded
- **Status:** NOT ADDRESSED
- **Verified by:** `agents/extractor/agent.py:145` still `"modelId": "us.anthropic.claude-sonnet-4-6-v1:0"`. No Haiku fallback in `lambdas/processing/classifier/handler.py`.
- **Residual:** Spec §5.5 lists both models; classifier never uses Haiku.

### N6. Empty `scripts/`, `profiles/`, `containers/` dirs
- **Status:** NOT ADDRESSED
- **Verified by:** `ls scripts/ profiles/ containers/` returns three empty directories.
- **Residual:** Either remove or populate; acceptable POC trade-off.

### N7. Smoke test only exercises kernel
- **Status:** NOT ADDRESSED
- **Verified by:** `tests/e2e/smoke-test.sh` — `run_deployed` still ends with `"upload accepted (full assertion is a manual/UAT step)"`; no polling of outputs bucket, no Aurora-row check, no SNS assertion.
- **Residual:** Acceptable for POC (audit noted this) but BUILD §4.2 bullet list still not satisfied.

---

## Acceptance gates re-run

All gates run from a clean checkout on commit `e9588c1`.

| Gate | Status | Notes |
|---|---|---|
| `cd cdk && uv sync` | PASS | 30 packages resolved, 29 audited |
| `cd cdk && npx cdk synth` | PASS | 9 stacks synthesized to `cdk.out` ("Successfully synthesized") |
| `cd cdk && uv run ruff check .` | PASS | All checks passed |
| `cd cdk && uv run black --check .` | PASS | 31 files would be left unchanged |
| `cd cdk && uv run mypy --strict laboraid_cdk` | PASS | Success: no issues found in 25 source files |
| `cd cdk && uv run pytest` | PASS | 18 passed in 29.59s |
| `cd cdk && uv run pytest ../lambdas` | PASS | 71 passed in 0.40s (up from 30 at audit time — new tests for fixed handlers) |
| `cd ui && corepack pnpm install --frozen-lockfile` | PASS | Lockfile up to date |
| `cd ui && corepack pnpm typecheck` | PASS | tsc --noEmit clean |
| `cd ui && corepack pnpm lint` | PASS | eslint clean (--max-warnings 0) |
| `cd ui && corepack pnpm exec vitest run` | PASS | 4 tests passed |
| `cd ui && corepack pnpm build` | PASS | dist/index.html produced; 610 kB main JS warning is non-fatal |
| `cd kernel && uv sync` | PASS | 34 packages resolved |
| `cd kernel && uv run python pipeline/run.py --all` | PASS | 704 = 99.6% (1 wrong, 0 blank); 483 = 83.2% Overall / 100% Building (74 blank, 0 wrong); 537 = 67.4% — all above per-union BUILD §4.1 floors |

---

## Hard-rule re-check

| Rule | Status |
|---|---|
| `kernel/` untouched | PASS — `git log --oneline -- kernel/` shows only `fa452ee feat: import labor_aid_poc kernel via git subtree` |
| No static creds | PASS — grep for `AKIA[0-9A-Z]{16}` + `aws_secret_access_key` across `.py/.ts/.tsx/.yaml/.yml/.json/.md` matches only `docs/AUDIT_REPORT.md` (the regex literal itself, no actual creds) |
| Mandatory tags Aspect applied to all stacks | PASS — `cdk/app.py:158` applies `MandatoryTagsAspect` at app level, descending into all 9 stacks; test `tests/test_mandatory_tags.py` passes |
| Language-split boundary holds | PASS — no `.ts` / `.tsx` files outside `ui/`; no `package.json` outside `ui/`; no `node_modules/` outside `ui/` |
| API Lambdas check `cognito:groups` | PASS — every gated handler imports the shared `authz` layer and calls `enforce_groups(...)`; the 4 ungated handlers (`profile-list`, `ratesheet-list`, `ratesheet-get`, `ratesheet-audit`) are the ones Spec §2.2 says are "any authenticated" |
| `ratesheet-publish` queries Aurora before publish | PASS — `read_approval_state` SQL in `ratesheet-publish/handler.py:63-90`; result fed to `publish_guard` (line 111); request body intentionally ignored |
| Approve/reject/unapprove write to Aurora + fire EventBridge | PASS — `persist_approval` / `persist_rejection` / `persist_unapproval` execute `UPDATE rate_periods ...` via the RDS Data API; `emit_event` does `events.put_events(... DetailType="laboraid.rate-sheet.{approved,rejected,unapproved}" ...)` on the engine bus; API stack adds `engine_bus.grant_put_events_to(fn)` and an `ENGINE_BUS_NAME` env var |
| Step Functions reads `agent-config.enabled` | PASS — `tasks.DynamoGetItem` on the `agent-config` table feeds an `AgentEnabled` Choice state in `sfn/main_pipeline.py:67-115` |

---

## Verdict

- **Safe to push?** YES — all CI gates green, no static creds, no language-boundary violations, kernel untouched.
- **Safe to merge to main?** YES — every BLOCKER from the audit is fixed by reading the actual source; the single PARTIAL DRIFT item (D1: two stale "8 stacks" mentions in Spec §14 lines 1720, 1732) is documentation-only and not deploy-blocking. All NICE-TO-HAVE items are either explicitly accepted (N2) or POC trade-offs. PR description should call out the residual D1 typo so reviewers know about it.
- **Safe to deploy?** YES with the caveats baked into the design:
  1. Deploy requires `CDK_DEFAULT_ACCOUNT` (configs default to a placeholder for synth).
  2. `cdk deploy` will exercise the `bedrock-agentcore` AwsCustomResource — if the AgentCore Runtime API has changed shape since `install_latest_aws_sdk` was wired, the runtime create may fail (acceptable failure mode: stack rolls back; no data loss).
  3. The `ExtractorAgent` container's CMD has not been smoke-tested end-to-end on a real AgentCore Runtime — `B7` is fixed semantically (app.run() is now reachable) but the first real deploy is the first actual proof.
  4. Custom domain is opt-in via `-c domain_name=...`; deploys without it land on a CloudFront default `*.cloudfront.net` domain (Cognito callbacks follow).
  5. NICE-TO-HAVE N5 (Bedrock model env var) and N7 (deployed smoke test asserts end-to-end) are open; first deploy should be observed manually for the missing assertions.
