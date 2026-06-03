# Audit Fix — Overnight Runner Prompt (Pass 2)

**Audience:** the same Claude CLI runner that executed `BUILD_INSTRUCTIONS.md`.
**Mode:** unattended; NO clarifying questions.
**Authority:** the audit at `docs/AUDIT_REPORT.md` is the source of truth for
what's wrong. The decisions at `docs/AUDIT_DECISIONS.md` lock the
architectural calls. This file specifies the **fix queue order** + per-item
workflow + acceptance gates that close the audit findings.

---

## 0. Pre-flight (read once at run start)

### 0.1 Required reads (in this order)

1. `docs/AUDIT_REPORT.md` — every finding, every fix recommendation, with file:line
2. `docs/AUDIT_DECISIONS.md` — the three architectural calls (B5, B7, B8)
3. `docs/BUILD_LOG.md` — record of what the first build pass committed; resume
   pointer if this fix pass is itself resumed
4. `BUILD_INSTRUCTIONS.md` §0.2 hard rules — still apply verbatim (kernel
   untouched, no static creds, tagging Aspect, no fabrication, no out-of-scope work)
5. `BUILD_INSTRUCTIONS.md` §0.3 language split — still applies
6. `docs/09_Technical_Implementation_Spec.md` §sections cited in each audit finding

The audit report's `Fix:` line per finding is operational. The audit decisions
override the runner's judgement on B5/B7/B8.

### 0.2 Hard rules carried from the original build (do NOT violate)

1. NEVER modify `kernel/`. If a fix requires a kernel change, log to `BUILD_LOG.md` and skip.
2. NEVER use static AWS credentials.
3. NEVER bypass the `MandatoryTagsAspect` — every new resource inherits the 13 tags.
4. NEVER fabricate behaviour — if Spec/09 doesn't define a contract, log and skip.
5. NEVER add out-of-scope features (see `BUILD_INSTRUCTIONS.md` §3 — no Bedrock KB,
   no 8 deferred agents, no YoY validation, etc.).
6. ALWAYS keep the language-split boundary intact (no `.ts` outside `ui/`).
7. ALWAYS run the §3 acceptance gates after each fix; commit ONLY if green.
8. ALWAYS preserve the per-item commit shape: one fix = one `[FIX-XX]` commit,
   stage only the files for that fix, append a line to `docs/BUILD_LOG.md`.

---

## 1. Fix queue — sequenced

Order matters. Items within a single block can run in parallel.
**Stop on first failure, log to `BUILD_LOG.md`, exit. Next run resumes.**

### Block 1 — Architectural-call fixes (do these first; downstream depends)

| # | Fix | Affected files | Decision source | Acceptance |
|---|-----|----------------|-----------------|-----------|
| **FIX-B5** | Replace `CfnResource(type="AWS::BedrockAgentCore::Runtime", ...)` with `AwsCustomResource` invoking `bedrock-agentcore:CreateAgentRuntime / Update / Delete` | `cdk/laboraid_cdk/constructs/strands_agent.py` (rewrite) | `AUDIT_DECISIONS.md` D-B5 | `cdk synth` green; `runtime_arn` is a token resolvable downstream |
| **FIX-B7** | Move `app.run()` out of `if __name__ == "__main__":` block; keep behind `try/except ImportError` | `agents/extractor/agent.py` (5-line edit) | `AUDIT_DECISIONS.md` D-B7 | `python -c "import agents.extractor.agent"` does not exit immediately; unit tests still pass |
| **FIX-B8** | Make `Config.domain_name` optional (default None). Skip ACM/Route53/custom-callback wiring when None. Cognito callbacks computed from `ui_stack.app_url` | `cdk/laboraid_cdk/config/__init__.py`, `dev.py`, `prod.py`, `cdk/laboraid_cdk/stacks/ui_stack.py`, `cdk/laboraid_cdk/stacks/security_stack.py`, `cdk/app.py` (wiring) | `AUDIT_DECISIONS.md` D-B8 | `cdk synth` green with `domain_name=None`; also green with `-c domain_name=...` (test both) |

### Block 2 — Workflow persistence + authz fixes (the SOW-critical block)

| # | Fix | Affected files | Acceptance |
|---|-----|----------------|-----------|
| **FIX-B1** | `ratesheet-publish` reads `approval_state` from Aurora `rate_periods` via RDS Data API (use the pattern in `ratesheet-list/handler.py:54-65`); ignore request body | `lambdas/api/ratesheet-publish/handler.py` (rewrite read path) | New unit test: POST with `body={"approval_state":"approved"}` against a row where Aurora has `pending_review` MUST return 409 |
| **FIX-B2** | `ratesheet-approve` / `ratesheet-reject` / `ratesheet-unapprove` (a) execute parameterised `UPDATE rate_periods SET approval_state=..., approved_by=..., approved_at=NOW() WHERE ...` via RDS Data API, (b) `PutEvents` to the `laboraid-{env}-l3-eb-engine` bus with `DetailType=laboraid.rate-sheet.approved` / `.rejected`; reject persists `rejection_reason` + `rejection_tags`. Add `events:PutEvents` to the API stack IAM + `ENGINE_BUS_NAME` env var. | `lambdas/api/ratesheet-approve/handler.py`, `lambdas/api/ratesheet-reject/handler.py`, `lambdas/api/ratesheet-unapprove/handler.py`, `cdk/laboraid_cdk/stacks/api_stack.py` (IAM + env) | Unit tests assert Aurora UPDATE issued + EventBridge PutEvents called with correct DetailType per handler |
| **FIX-B3** | Add `lambdas/api/_shared/authz.py` exposing `enforce_groups(event, allowed: list[str]) -> None` that extracts `event['requestContext']['authorizer']['jwt']['claims']['cognito:groups']` (HTTP API v2 serialises as JSON-encoded list-string; parse it). Returns 403 if no overlap. Apply to every handler per the per-route map in `AUDIT_REPORT.md` B3. | `lambdas/api/_shared/authz.py` (new), every `lambdas/api/*/handler.py` (1 line each) | New unit test: every handler returns 403 for a JWT with an empty or wrong-group claim |

### Block 3 — Step Functions wiring (depends on FIX-B5)

| # | Fix | Affected files | Acceptance |
|---|-----|----------------|-----------|
| **FIX-B4** | Inject `agent_config_table` into `OrchestrationStack`. In `build_definition`, add `tasks.DynamoGetItem` reading `{"agent_name": "ExtractorAgent"}` then a `sfn.Choice` gating the agent invoke on `$.agentCfg.Item.enabled.BOOL == True`. Default branch routes around the agent (placeholder pass-through is fine for POC; document in SFN comment). | `cdk/app.py` (pass table), `cdk/laboraid_cdk/stacks/orchestration_stack.py` (constructor + wiring), `cdk/laboraid_cdk/sfn/main_pipeline.py` (Choice gate) | SFN definition JSON contains a `Choice` state immediately before the agent invoke; `cdk pytest` adds an assertion that the Choice condition is present |
| **FIX-B6** | Pass `extractor_runtime.runtime_arn` from `ProcessingStack` into `OrchestrationStack`. Replace `sfn.Pass(ExtractViaAgent)` with a real `tasks.LambdaInvoke` of a new `ExtractorInvoker` Lambda (under `lambdas/processing/extractor-invoker/`) that calls `bedrock-agentcore:InvokeAgentRuntime` synchronously with the classified document + run context. | `cdk/app.py`, `cdk/laboraid_cdk/stacks/orchestration_stack.py`, `cdk/laboraid_cdk/sfn/main_pipeline.py`, `lambdas/processing/extractor-invoker/` (new dir with handler + tests) | SFN ExtractViaAgent is a LambdaInvoke task; new Lambda has unit test mocking `bedrock-agentcore:InvokeAgentRuntime`; `cdk synth` green; smoke test exercises Extract step |

### Block 4 — Drift fixes (doc-vs-code alignment)

Each is single-file or near-single-file. Group as one big commit per finding.

| # | Fix | Files |
|---|-----|-------|
| **FIX-D1** | Update Spec/09 §3 lines 214-227 + §11 lines 1547-1558 to list **9 stacks** (incl. `OrchestrationStack`; drop `NetworkStack` mention). Add one-line note: "Network rolled into Storage." | `docs/09_Technical_Implementation_Spec.md` |
| **FIX-D2** | Replace Spec/09 §2.1 asset table (lines 442-450) with the 19-Lambda list (matching §2.2) OR a "see §2.2" pointer. | `docs/09_Technical_Implementation_Spec.md` |
| **FIX-D3** | Update `BUILD_INSTRUCTIONS.md:101` from "6 DynamoDB tables" → "7 DynamoDB tables (incl. agent-config)". | `BUILD_INSTRUCTIONS.md` |
| **FIX-D4** | `RouteGuard` renders a `<Forbidden403 />` component (new) instead of silent `<Navigate>` on denial. Keep redirect only for the `/` root ambiguity. | `ui/src/components/RouteGuard.tsx`, `ui/src/components/Forbidden403.tsx` (new) |
| **FIX-D5** | `/admin/costs` wrapped in its own `<RouteGuard groups={["Admins"]}>`; remove "Admins-only" body text. | `ui/src/routes.tsx`, `ui/src/admin/Costs.tsx` |
| **FIX-D6** | (No code change — D6 is a verification that confirms conformance.) Log to BUILD_LOG: "D6: no action — verified conformant." | n/a |
| **FIX-D7** | Add comment-button column to `RateCellTable.tsx`; opens small modal; POST to `/v1/cells/{cell_id}/comment`. | `ui/src/components/RateCellTable.tsx`, `ui/src/components/CellCommentModal.tsx` (new) |
| **FIX-D8** | Update `README.md` "Measured accuracy" line to: `704 = 99.6%, 483 = 100% on Building zone (83.2% overall including 74 sourced blanks), 537 = 67.4%`. | `README.md` |
| **FIX-D9** | Pass `api_stack.http_api.api_id` into `ObservabilityStack` constructor; use as `ApiId` dimension for the API GW 5xx alarm. (Aurora dimension already works — leave it; document.) | `cdk/app.py`, `cdk/laboraid_cdk/stacks/observability_stack.py`, `cdk/laboraid_cdk/stacks/api_stack.py` (expose `http_api`) |

### Block 5 — Re-run gates + final commit

After every Block 1–4 item is committed:

```bash
cd cdk && uv sync && npx cdk synth                  # all 9 stacks green
cd cdk && uv run ruff check . && uv run black --check . && uv run mypy --strict laboraid_cdk
cd cdk && uv run pytest                              # all stack tests green
cd cdk && uv run pytest ../lambdas                  # all handler tests green (incl. NEW B1/B2/B3 tests)
cd ui && corepack pnpm install --frozen-lockfile && corepack pnpm typecheck && corepack pnpm lint && corepack pnpm exec vitest run && corepack pnpm build
bash tests/e2e/smoke-test.sh                         # kernel smoke still PASSes
cd kernel && uv run python pipeline/run.py --all    # accuracy floors held
```

**Then** the final commit:

```
[FIX-FINAL] all audit blockers + drifts closed; see docs/BUILD_LOG.md
```

---

## 2. Per-item workflow

For each `FIX-XX` in Section 1 (in order, Block 1 → Block 5):

1. Read the audit finding section in `docs/AUDIT_REPORT.md` (search for the
   blocker ID).
2. If the item is in Block 1, also read the matching `D-Bx` section in
   `docs/AUDIT_DECISIONS.md` for the architectural call.
3. Read any Spec/09 section the finding cites.
4. Edit the listed files. Type-annotate everything; `mypy --strict` must pass.
5. If new tests are required (per Acceptance column), write them in the
   matching `tests/` dir.
6. Run the per-fix acceptance check (named in the Acceptance column).
   - For CDK changes: `cd cdk && npx cdk synth && uv run pytest`
   - For Lambda changes: `cd cdk && uv run pytest ../lambdas` (plus the specific new test)
   - For UI changes: `cd ui && corepack pnpm typecheck && corepack pnpm lint && corepack pnpm exec vitest run`
   - For doc changes: `grep -E "<expected new text>" <file>` plus a manual eyeball
7. If acceptance fails, FIX the underlying cause. Do NOT skip with `--no-verify`
   or suppress errors.
8. Commit with message: `[FIX-XX] <one-line title>` (stage only the files this
   fix produced).
9. Append a line to `docs/BUILD_LOG.md`:
   `[FIX-XX] <title> — DONE at <ISO timestamp>`
10. Move to the next item.

If a fix item fails after a reasonable attempt:

- Leave the working tree as-is (do NOT `git checkout` or discard work).
- Write a failure note to `docs/BUILD_LOG.md` (item ID, error, what was tried).
- Stop the run. A future run will read `BUILD_LOG.md` and resume from the
  next unfinished item.

---

## 3. Acceptance gate (final)

Same as `BUILD_INSTRUCTIONS.md` §4.1 — every command exits 0. PLUS the new
gates introduced by Block 2:

- New unit test in `lambdas/api/ratesheet-publish/tests/test_handler.py`: a
  POST with body `{"approval_state":"approved"}` against a target where
  Aurora has `pending_review` MUST return 409.
- New unit test in each of `ratesheet-{approve,reject,unapprove}/tests/`:
  mocks the RDS Data API and `events.PutEvents`; asserts both are called
  with the correct args.
- New unit test in each `lambdas/api/*/tests/test_handler.py` (the 15
  handlers gated per the per-route map in `AUDIT_REPORT.md` B3): the
  handler returns 403 when `cognito:groups` claim is empty or missing.
- New SFN-assertion test in `cdk/tests/test_orchestration_stack.py`:
  parse the synthesised state machine JSON and assert a `Choice` state
  immediately precedes the agent invoke task with the
  `$.agentCfg.Item.enabled.BOOL == True` condition.
- New SFN-integration test in `cdk/tests/test_orchestration_stack.py`:
  the `ExtractViaAgent` step is a `LambdaInvoke` task pointing at the
  `ExtractorInvoker` Lambda (not a `Pass` state).

All listed in Block 5; runner must pass them before the `[FIX-FINAL]` commit.

---

## 4. Out of scope for this fix pass (DO NOT do)

- Anything in `AUDIT_REPORT.md` `## NICE-TO-HAVE` (N1–N7). They are
  cosmetic or v1.1+; the fix pass focuses on BLOCKERS + DRIFTS only.
- Group G (281 / 821 kernel extractors). Still deferred to the kernel
  harness; do not touch.
- Library work (authoring F369 partials from the fixes). That's a
  separate downstream task after this fix pass closes.
- AWS deployment. The acceptance gate is still `cdk synth` exits 0;
  actual `cdk deploy` is the human's call.
- README / PR description rewrite beyond the D8 line.

---

## 5. When the runner finishes

- Final commit: `[FIX-FINAL] all audit blockers + drifts closed`
- Append a one-paragraph summary to `docs/BUILD_LOG.md`:
  - how many `[FIX-XX]` commits landed
  - which gates re-ran green
  - any audit finding that could not be fully closed (with reason)
- Print a one-paragraph summary to stdout for the human.
- Do NOT `git push` or open the PR — outward-facing actions are the
  human's trigger.

---

## TL;DR for the runner

1. Read `AUDIT_REPORT.md`, `AUDIT_DECISIONS.md`, this file, `BUILD_LOG.md`.
2. Execute Block 1 (FIX-B5, FIX-B7, FIX-B8) — architectural-call fixes first.
3. Then Block 2 (FIX-B1, FIX-B2, FIX-B3) — workflow persistence + authz.
4. Then Block 3 (FIX-B4, FIX-B6) — Step Functions wiring.
5. Then Block 4 (FIX-D1..D9) — drift cleanup.
6. Then Block 5 — re-run all gates, write `[FIX-FINAL]` commit.
7. Same hard rules as the build. Same commit + log shape.
8. Stop on first failure. Log + exit. Resume on next run.

Go.
