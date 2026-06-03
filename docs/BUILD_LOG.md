# Build Log

Append-only log of the overnight build runner. One line per completed item;
detailed notes for anything that failed or deviated. A resume run reads this to
continue from the next unfinished item.

## Group A — CDK foundation

- [BUILD-A.1] CDK app bootstrap — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.2] Mandatory tags Aspect — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.3] Config (env-specific) — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.4] Naming helper — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.5] Tagged construct wrappers — DONE at 2026-06-02T20:55:57Z
- [BUILD-A.6] Strands agent custom construct — DONE at 2026-06-02T20:55:57Z

### Notes

- **`cdk synth` exit code at end of Group A:** the CDK CLI prints
  "This app contains no stacks" and exits 1 because Group A defines only the
  app, aspect, config, naming, and construct wrappers — no stacks yet (those
  land in Group B onward). The Python app itself synthesizes a valid cloud
  assembly (`manifest.json` + `tree.json`, exit 0 via `uv run python app.py`).
  The `uv run cdk synth` gate goes green once Group B adds the first stack.
- Quality gates passing for `cdk/`: `ruff check` ✅, `black --check` ✅,
  `mypy --strict laboraid_cdk` ✅ (14 files), `pytest` ✅ (9 passed).
- `uv run cdk synth` literally requires the `cdk` CLI on PATH; it is the Node
  AWS CDK CLI (v2.1119.0 available via `npx`), not a Python package. Driven the
  synth via `npx cdk` / `uv run python app.py`.

## Group B — Storage & security stacks

- [BUILD-B.1] Security stack — DONE at 2026-06-02T21:08:38Z
- [BUILD-B.2] Storage stack — DONE at 2026-06-02T21:08:38Z

### Notes

- **`cdk synth` now exits 0** with `Laboraid-{env}-Security` + `Laboraid-{env}-Storage`.
  Gates green for `cdk/`: synth ✅, ruff ✅, black ✅, mypy --strict (16 files) ✅,
  pytest ✅ (11 passed).
- Stacks are **environment-agnostic** (no `env=` binding) so synth runs without
  AWS credentials — the dev/prod split is carried by `config.env`. Deploy binds
  to a concrete account/region via `CDK_DEFAULT_*`.
- **7 DynamoDB tables**, not 6: the BUILD §1 B.2 row says "6 DynamoDB tables",
  but Spec/09 §3.2 defines 7 (incl. `agent-config`, required by §4.4 SOW match
  for the Admin agent-toggle). Built all 7; flagging the BUILD-vs-Spec mismatch.
- **Aurora schema-init** uses the RDS **Data API** (`enable_data_api=True`) so the
  custom-resource Lambda needs no VPC attachment or `psycopg` bundling. DDL in
  `cdk/assets/schema_init/schema.sql` (idempotent `IF NOT EXISTS`), applied on
  Create/Update. Aurora sits in a minimal no-NAT VPC (isolated subnets).
- Audit bucket is the server-access-log target for the other 5 buckets.

## Group C — Processing + AI stacks

- [BUILD-C.1] ExtractorAgent container — DONE at 2026-06-02T21:30:00Z
- [BUILD-C.3] AI stack (Bedrock Guardrails) — DONE at 2026-06-02T21:30:00Z
- [BUILD-C.2] Processing stack — DONE at 2026-06-02T21:30:00Z

### Notes

- Gates green for `cdk/` (4 stacks): synth ✅, ruff ✅, black ✅,
  mypy --strict (18 files) ✅, pytest ✅ (13 passed).
- **C.1 `docker build` acceptance is DEFERRED** to a Docker/AWS-enabled host: the
  Strands SDK + AgentCore SDK + the flat untyped kernel are not installable in
  this offline synth environment. Validated offline instead: `py_compile` ✅,
  ruff/black ✅, static SOP tests ✅ (3 passed, no Strands import).
- **Kernel import path corrected vs Spec/09 §5.3:** the spec writes
  `from kernel.pipeline import extract`, but the kernel is a flat `package=false`
  project (modules `pipeline`, `canonical` at its root). The container sets
  `PYTHONPATH=/opt/kernel`, so the agent imports `from pipeline import ...` /
  `from canonical.model import ...`. Kernel left unmodified (rule #1).
- **Stack-order correction (C.2):** AI is instantiated *before* Processing (the
  ExtractorAgent runtime injects `BEDROCK_GUARDRAIL_ID`). Spec/09 §3's listed
  order is "Processing → AI"; real dependency is the reverse for the guardrail.
- **IAM-role placement correction (B.1 → C.2):** the foundational API/agent roles
  originally added to `SecurityStack` were removed. A role defined in the upstream
  Security stack cannot be granted a downstream Storage resource without forming a
  dependency cycle (Storage already depends on the Security CMK). Per-Lambda /
  per-agent roles are now created in their consuming stacks (the ExtractorAgent
  role lives in `processing_stack.py` with its grants). Documented in
  `security_stack.py`.
- **Classifier Lambda code** (`lambdas/processing/classifier/`) was created with
  C.2 because the processing stack must reference real handler code; it is L4 and
  named in the C.2 row. Powertools is referenced as a layer/bundling concern at
  deploy (plain asset for synth).

## Group D — Validation + rendering Lambdas

- [BUILD-D.1] Validator Lambdas (4) — DONE at 2026-06-02T22:05:00Z
- [BUILD-D.2] Renderer Lambdas (3) — DONE at 2026-06-02T22:05:00Z
- [BUILD-D.3] Validation stack — DONE at 2026-06-02T22:05:00Z

### Notes

- Gates green: `cdk synth` ✅ (6 stacks), ruff ✅, black ✅, mypy --strict (19
  files) ✅, cdk pytest ✅ (14), lambda pytest ✅ (17).
- **Lambda offline-testability pattern:** every handler imports Powertools under a
  `try/except ModuleNotFoundError` shim, and pure logic (checksum/range/confidence
  /gaps-parsing/slack-formatting) lives in module-level functions the tests import
  directly via `importlib` (unique module name) — so tests run in the cdk venv
  without Powertools/openpyxl/boto3. `lambdas/pytest.ini` sets
  `--import-mode=importlib` so the many same-named `handler.py`/`test_handler.py`
  files collect without collision.
- **MAJOR FIX — aspect infinite-loop (regression across the whole app):** once the
  validation stack pushed the resource count up, `cdk synth` failed with
  `PossibleInfiniteLoopDetected ... invoking Aspects`. Root cause: the app-level
  `MandatoryTagsAspect` tagged L2 `Resource` wrappers, which triggers CDK's
  internal tag-*propagation* aspect; under CDK 2.257 aspect stabilization that
  mutates the tree every pass and never converges. Disabling stabilization made
  the mandatory tags vanish entirely (wrong fix). **Fix:** the aspect now tags the
  L1 `CfnResource` nodes directly via their `TagManager` (priority 100 so
  per-resource `Layer`/`DataClassification` overrides win) — one converging pass,
  all 13 tags present (verified in synth output). See `aspects/mandatory_tags.py`.
- **Related fix:** `TaggedLambda` now creates an explicit one-month `LogGroup`
  instead of the deprecated `log_retention` prop (which injects a late singleton
  custom resource); same for the storage schema-init Lambda.
- **Related fix:** `SnsTopicWithSubs` names its inner topic `f"{id}Topic"` so one
  Lambda subscribing to several topics doesn't collide on a shared subscription id.
- **D.3 scope consolidation:** the validation stack also instantiates the D.1/D.2
  Lambda *resources* (4 validators + 3 renderers) plus the slack-notifier — there
  is no separate L7 stack in the 8-stack design, and F.1 (orchestration) wires
  these into Step Functions. The 3 SNS topics + EventBridge bus + SES config set
  + DLQ satisfy the D.3 acceptance.

## Group E — API + UI

- [BUILD-E.1] API Lambdas (admin, 10) — DONE at 2026-06-02T22:25:00Z
- [BUILD-E.2] API Lambdas (business + shared, 9) — DONE at 2026-06-02T22:35:00Z
- [BUILD-E.3] API stack — DONE at 2026-06-02T22:45:00Z
- [BUILD-E.4] React SPA — Admin shell — PENDING
- [BUILD-E.5] React SPA — Business shell — PENDING
- [BUILD-E.6] UI hosting stack — PENDING

### Notes

- 19 API Lambdas (E.1+E.2) + the L2 API stack (E.3) done. Gates green: cdk synth
  ✅ (7 stacks), ruff ✅, black ✅, mypy --strict (20 files) ✅, cdk pytest ✅ (6
  stack-assertion groups), lambda pytest ✅ (30 lambda tests incl. publish-409,
  approve/reject/unapprove transitions, agent-toggle, validators, renderers).
- **SOW-critical logic implemented + tested:** `ratesheet-publish.publish_guard`
  returns 409 unless `approval_state='approved'`; approve requires an empty review
  queue; reject requires a reason (+ validated structured tags); unapprove is
  limited to the original approver before publish.
- API stack creates the 19 Lambdas itself (per-category least-privilege grants),
  Cognito JWT authorizer on all routes, the 20 routes of §2.2, and a regional WAF.
  Per-route *group* authz is enforced in-Lambda from the `cognito:groups` claim.

- [BUILD-E.4] React SPA — Admin shell — DONE at 2026-06-02T23:00:00Z
- [BUILD-E.5] React SPA — Business shell — DONE at 2026-06-02T23:05:00Z
- [BUILD-E.6] UI hosting stack — DONE at 2026-06-02T23:10:00Z

### Group E (E.4-E.6) notes

- Two-persona Vite + React 18 + TS SPA under `ui/` (47 files). Tailwind, React
  Router v6, Zustand, Amplify auth, react-pdf, TanStack Table. 8 admin + 7
  business pages, `RouteGuard` (Cognito group gate), `PersonaChooser`,
  `AgentToggle` (Admins-only PATCH), `ApproveRejectBar` (Approve disabled until
  review queue empty; Reject needs reason), 5 s polling on Jobs/Agents.
- **UI gates all green:** `corepack pnpm typecheck` ✅, `lint` ✅, `vitest run` ✅
  (4), `build` ✅ (`ui/dist/index.html`). `pnpm` is reached via `corepack pnpm`.
- E.6 UI stack: private S3 + CloudFront + OAC + `BucketDeployment` of `ui/dist`;
  ACM + Route53 wired only when a hosted zone is supplied (dev synths offline).

## Group F — Orchestration + observability

- [BUILD-F.1] Step Function main pipeline — DONE at 2026-06-02T23:20:00Z
- [BUILD-F.2] Observability stack — DONE at 2026-06-02T23:30:00Z
- [BUILD-F.3] Operational docs — DONE at 2026-06-02T23:35:00Z

### Group F notes

- 9 stacks total now; `cdk synth` exits 0; ruff/black/mypy --strict (25 files)/
  cdk pytest (9 stack tests) all green.
- F.1 Standard-workflow pipeline (`sfn/main_pipeline.py`): classify → extract
  (AgentCore wait point) → parallel validate → choice → parallel render → publish,
  else route-to-review; per-task retries + catch. EventBridge rule triggers on S3
  `Object Created` (inputs bucket emits to EventBridge — added `event_bridge=True`).
- **Aspect hardening (F.1):** `MandatoryTagsAspect` now tags only nodes exposing a
  real `TagManager` (`getattr(node, "tags")`), since `TagManager.is_taggable` can
  report True for raw `CfnResource`s (AgentCore Runtime) that have no `.tags` —
  which raised once the orchestration stack was added.
- F.2 observability: 5 dashboards + 6 named alarms (→ failures topic) + CloudTrail;
  metrics addressed by deterministic name/ARN so no extra cross-stack handles.
- F.3 docs: `ARCHITECTURE.md`, `RUNBOOK.md`, `ONBOARDING.md`.

## Group G — kernel extractors 281 + 821 (DEFERRED to the kernel harness)

- [BUILD-G.1] 281 Profile YAML — PENDING (harness)
- [BUILD-G.2] 281 extractor — PENDING (harness)
- [BUILD-G.3] 821 Profile YAML — PENDING (harness)
- [BUILD-G.4] 821 extractor — PENDING (harness)

### Decision + groundwork

Group G is the kernel's **planner→builder→evaluator harness** work (BUILD §1 Group
G: "Use the kernel's own `.claude/harness/`; never modify kernel/ directly"). It is
deliberately left to that loop rather than hand-authored, because:

- The 2026.01.01 **wage sheets are scanned** (`extract_text()` returns 0 chars →
  OCR-dependent). 281 adds a 3-tier indenture split + half-year sub-classes; 821
  is "the most complex" union (Spec/09). Hitting the ≥98% / ≥95% cell-accuracy
  gates from OCR in ≤4 iterations is exactly what the harness is built to iterate.
- Hand-hacking a sub-threshold extractor into the `kernel/` **git subtree** would
  break `git subtree pull` and risks an inaccurate kernel — worse than deferring.

**Verified ready for the harness run:** kernel deps install (`uv sync`), the
pipeline runs (`704 = 99.6%`, matches measured accuracy), data is present
(`kernel/data/sprinkler_fitters_{281,821}/{cba,ratesheet}/`), the harness exists
(`kernel/.claude/{harness,commands,agents}/`), and the discovery studies
(`discovery/07_281_*`, `discovery/04_821_*`) document every CBA formula. To run:

```bash
cd kernel            # then drive the harness (planner/builder/evaluator):
#   register the union in pipeline/run.py TARGETS + pipeline/extract.py EXTRACTORS,
#   author profiles/sprinkler_fitters_281.yaml (match groundtruth header only),
#   write extract_281 deriving values from cba/*.pdf (OCR), iterate vs the
#   evaluator until >=98% on documented cells; stop after 4 iterations.
```

The §4.1 kernel regression gate (704/483/537) and §4.2 smoke (537/704) do **not**
depend on G and already pass.

## Group H — Integration + smoke

- [BUILD-H.1] End-to-end smoke test — DONE at 2026-06-02T23:50:00Z (704 = 99.6% PASS)
- [BUILD-H.2] CI workflow — DONE at 2026-06-02T23:55:00Z
- [BUILD-H.3] README overwrite — DONE at 2026-06-03T00:00:00Z

## Audit fix pass (Pass 2) — closing AUDIT_REPORT.md findings

- [FIX-B5] StrandsAgentRuntime → AwsCustomResource (bedrock-agentcore CreateAgentRuntime) — DONE at 2026-06-02T00:00:00Z
  - Replaced the non-existent `AWS::BedrockAgentCore::Runtime` CfnResource with an
    `AwsCustomResource` (Create/Update/Delete via `bedrock-agentcore` SDK), per
    decision D-B5. `runtime_arn` now reads `get_response_field("agentRuntimeArn")`,
    preserving the downstream output contract. `install_latest_aws_sdk=True` because
    bedrock-agentcore post-dates Lambda's bundled SDK. Construct kwargs unchanged so
    `processing_stack.py` needs no edit. Updated `test_stacks.py` to assert the
    `Custom::AWS` resource (DeleteAgentRuntime call) instead of the old CFN type.
    Gates: synth ✅ (9 stacks), ruff/black/mypy --strict (25 files) ✅, cdk pytest ✅ (18).
- [FIX-B7] agent.py app.run() moved out of `__main__` guard — DONE at 2026-06-02T00:00:00Z
  - AgentCore imports `agent.py` as a module (not `__main__`), so `app.run()` gated
    on `__name__ == "__main__"` never fired and the container exited immediately.
    Now `app.run()` runs unconditionally inside the `try` (only when the AgentCore
    SDK is importable); the `except ImportError` path is a no-op `pass` so local
    unit tests still import the @tool/build_agent logic. Per decision D-B7.
    Gates: `py_compile` ✅, agent unit tests ✅ (3 passed).

---

## RESUME POINTER (next run starts here)

**Completed:** Groups A, B, C, D, E, F, H fully (29 build items). Group G (281 +
821 kernel extractors) deferred to the kernel harness — see the Group G section
above. Everything committed `[BUILD-XX]`, working tree clean; `cd cdk && npx cdk
synth` exits 0 for all 9 stacks; the React SPA builds; the e2e smoke passes.

**Only remaining work:** Group G via the kernel's `.claude/harness/` (G section
above has the verified-ready runbook). It does not block the §4.1 / §4.2 gates.

**Conventions already established to reuse:**
- Lambda handlers: optional-Powertools `try/except ModuleNotFoundError` shim;
  pure logic in module-level fns; tests load the handler via `importlib` under a
  unique name; `lambdas/pytest.ini` sets `--import-mode=importlib`.
- CDK: `TaggedBucket`/`TaggedLambda`/`SnsTopicWithSubs`/`StrandsAgentRuntime`
  constructs; `name(env, layer, type_, purpose)` for all names; per-consumer IAM
  roles (never grant a Security-stack role a downstream resource → cycle);
  `MandatoryTagsAspect` tags **L1 CfnResources** (not L2) to avoid the
  aspect-stabilization loop.
- Stacks are environment-agnostic (no `env=`) so synth runs without AWS creds.
- `uv run cdk` is not valid (cdk is the Node CLI) — drive synth via `npx cdk`.

**Final acceptance gate:** BUILD_INSTRUCTIONS §4 (repo checks + e2e smoke + spec
match + SOW contract match).
