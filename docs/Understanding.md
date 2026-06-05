# Understanding the LaborAid Rate Engine — Learning Roadmap

A step-by-step path through the codebase for a new reader. Each step has a small set of files + a single question to answer before moving on. About 10–12 hours total if you read deeply; you can stop after any step and still have value.

> **Tip:** Don't skip the kernel. If you only have time for one step, do Step 2. The kernel is what actually does the work; the AWS stuff is wrapping. Keep a side note of acronyms (CBA, OAC, SteeringHandler, etc.) — most are explained the first time they appear in [09_Technical_Implementation_Spec.md](09_Technical_Implementation_Spec.md) §1. Skip `node_modules/` and `.venv/` — those are dependencies, not code-to-learn.

---

## Step 1 — What the app does (45 min)

Files: [`../README.md`](../README.md), [`../kernel/README.md`](../kernel/README.md), [`../kernel/DESIGN.md`](../kernel/DESIGN.md). Then open one real example: `../kernel/data/sprinkler_fitters_704/cba/2026.01.01.704 Rate Notice.pdf` (the input PDF) and the matching `../kernel/data/sprinkler_fitters_704/ratesheet/2026.01.01.704 Rate Sheet.csv` (the output we have to produce).

**Question:** *Given the input PDF, what does the output CSV look like, row by row?*

---

## Step 2 — Read the kernel (1–2 hours)

This is the deterministic core; everything else wraps it.

In this order:

1. [`../kernel/canonical/model.py`](../kernel/canonical/model.py) — `RateCell`, `ClassificationRow`, `r2()` rounding
2. [`../kernel/canonical/fields.yaml`](../kernel/canonical/fields.yaml) — the field dictionary
3. [`../kernel/profiles/sprinkler_fitters_704.yaml`](../kernel/profiles/sprinkler_fitters_704.yaml) — one per-union profile
4. [`../kernel/pipeline/ingest.py`](../kernel/pipeline/ingest.py) — open the PDF
5. [`../kernel/pipeline/extract.py`](../kernel/pipeline/extract.py) — focus on `extract_704()` only
6. [`../kernel/pipeline/compute.py`](../kernel/pipeline/compute.py) — derived columns (1.5x, P&G multipliers)
7. [`../kernel/pipeline/pivot.py`](../kernel/pipeline/pivot.py) — produce CSV

Then run it:

```bash
cd kernel
uv sync
uv run python pipeline/run.py --union sprinkler_fitters_704
```

Compare its output against the groundtruth ratesheet.

**Question:** *Trace one numeric cell in the output CSV back through pivot → compute → extract → ingest. Where in the PDF did that number come from?*

---

## Step 3 — The architecture spec (45 min)

[`CTO_SUMMARY.md`](CTO_SUMMARY.md) once for shape, then [`09_Technical_Implementation_Spec.md`](09_Technical_Implementation_Spec.md) §0–§4. Skim the CDK code blocks — read the prose.

**Question:** *Why 9 separate stacks and not one big stack?*

---

## Step 4 — CDK foundation (1 hour)

The patterns every stack uses.

1. [`../cdk/app.py`](../cdk/app.py) — entry, stack instantiation order
2. [`../cdk/laboraid_cdk/config/__init__.py`](../cdk/laboraid_cdk/config/__init__.py) + `dev.py` + `prod.py`
3. [`../cdk/laboraid_cdk/util/naming.py`](../cdk/laboraid_cdk/util/naming.py)
4. [`../cdk/laboraid_cdk/aspects/mandatory_tags.py`](../cdk/laboraid_cdk/aspects/mandatory_tags.py)
5. [`../cdk/laboraid_cdk/constructs/`](../cdk/laboraid_cdk/constructs/) — `tagged_bucket.py`, `tagged_lambda.py`, `sns_topic_with_subs.py`, `strands_agent.py`

Run:

```bash
cd cdk
uv sync
npx cdk synth
```

Then explore `cdk.out/` to see the CloudFormation that came out.

**Question:** *Where does the `Project: LaborAid-POC` tag come from for every resource?*

---

## Step 5 — The 9 stacks, in dependency order (2–3 hours)

Read one, then the next. Each builds on what came before.

1. [`../cdk/laboraid_cdk/stacks/security_stack.py`](../cdk/laboraid_cdk/stacks/security_stack.py) — KMS + Cognito user pool (4 groups)
2. [`../cdk/laboraid_cdk/stacks/storage_stack.py`](../cdk/laboraid_cdk/stacks/storage_stack.py) — 6 buckets + 7 DDB tables + Aurora
3. [`../cdk/laboraid_cdk/stacks/ai_stack.py`](../cdk/laboraid_cdk/stacks/ai_stack.py) — Bedrock PII Guardrail
4. [`../cdk/laboraid_cdk/stacks/processing_stack.py`](../cdk/laboraid_cdk/stacks/processing_stack.py) — Classifier Lambda + ExtractorAgent runtime
5. [`../cdk/laboraid_cdk/stacks/validation_stack.py`](../cdk/laboraid_cdk/stacks/validation_stack.py) — validator Lambdas + 3 SNS topics
6. [`../cdk/laboraid_cdk/stacks/api_stack.py`](../cdk/laboraid_cdk/stacks/api_stack.py) — API Gateway + 19 Lambdas + Cognito authorizer
7. [`../cdk/laboraid_cdk/stacks/ui_stack.py`](../cdk/laboraid_cdk/stacks/ui_stack.py) — S3 + CloudFront + OAC for the SPA
8. [`../cdk/laboraid_cdk/stacks/orchestration_stack.py`](../cdk/laboraid_cdk/stacks/orchestration_stack.py) — Step Function (the glue)
9. [`../cdk/laboraid_cdk/stacks/observability_stack.py`](../cdk/laboraid_cdk/stacks/observability_stack.py) — dashboards + alarms

**Question:** *If I delete the Storage stack, which other stacks break and why?*

---

## Step 6 — Two key Lambdas end-to-end (45 min)

The business approval gate.

1. [`../lambdas/api/ratesheet-publish/handler.py`](../lambdas/api/ratesheet-publish/handler.py) — the 409 guard
2. [`../lambdas/api/ratesheet-approve/handler.py`](../lambdas/api/ratesheet-approve/handler.py) — Aurora write + EventBridge emit
3. [`../lambdas/shared/`](../lambdas/shared/) — the `authz` layer that every handler uses for `cognito:groups`

**Question:** *What stops a Business user from calling `publish` directly via curl?*

---

## Step 7 — The Strands ExtractorAgent (1 hour)

1. [`../agents/extractor/agent.py`](../agents/extractor/agent.py) — `@tool` functions wrapping the kernel
2. [`../agents/extractor/system-prompt.md`](../agents/extractor/system-prompt.md) — what the agent is told to do
3. [`../agents/extractor/Dockerfile`](../agents/extractor/Dockerfile)

**Question:** *Where inside the agent does the deterministic kernel get called? When does the agent fall back to Bedrock Claude instead?*

---

## Step 8 — The React UI (1–2 hours)

The only TypeScript area in the repo.

1. [`../ui/src/main.tsx`](../ui/src/main.tsx) + [`../ui/src/App.tsx`](../ui/src/App.tsx) — entry, Amplify config
2. [`../ui/src/routes.tsx`](../ui/src/routes.tsx) + [`../ui/src/components/RouteGuard.tsx`](../ui/src/components/RouteGuard.tsx)
3. [`../ui/src/layouts/AdminLayout.tsx`](../ui/src/layouts/AdminLayout.tsx) + [`../ui/src/layouts/BusinessLayout.tsx`](../ui/src/layouts/BusinessLayout.tsx)
4. One admin page: [`../ui/src/admin/Jobs.tsx`](../ui/src/admin/Jobs.tsx) (typical CRUD pattern)
5. One business page: [`../ui/src/business/RateSheetReview.tsx`](../ui/src/business/RateSheetReview.tsx) (the 3-panel review + Approve/Reject)

Run the dev server:

```bash
cd ui
corepack pnpm install
corepack pnpm exec vite
```

Open the printed URL in a browser.

**Question:** *How does a Business user see "Approve disabled" when low-confidence cells remain?*

---

## Step 9 — Orchestration (45 min)

How Step Functions ties everything together.

[`../cdk/laboraid_cdk/sfn/`](../cdk/laboraid_cdk/sfn/) — the Step Function definition. Trace the path:

```
S3 ObjectCreated
  → EventBridge
  → Classify (Lambda)
  → AgentEnabled gate (DDB GetItem + Choice)
  → ExtractorInvoker (LambdaInvoke → AgentCore Runtime)
  → Validate (parallel: checksum + range + confidence)
  → Choice (passed / failed)
  → Render (xlsx / csv / articles)
  → Publish (Aurora UPDATE + S3 + SNS)
```

**Question:** *What happens if `agent-config.enabled` is `false` for `extractor`? What path does the SFN take?*

---

## Step 10 — Tests (1 hour)

To confirm you understand it.

1. [`../cdk/tests/test_stacks.py`](../cdk/tests/test_stacks.py) — synth-assertion pattern
2. [`../cdk/tests/test_strands_agent.py`](../cdk/tests/test_strands_agent.py) — the construct we tested most recently
3. Pick any [`../lambdas/api/`](../lambdas/api/)`<endpoint>/tests/test_handler.py` — handler test pattern
4. [`../ui/src/lib/auth.test.ts`](../ui/src/lib/auth.test.ts)

Run all the gates from repo root:

```bash
cd cdk && uv run pytest
uv run pytest lambdas
cd ../ui && corepack pnpm exec vitest run
```

**Question:** *Mentally change one line in any stack and predict which test would catch you. Then make the change and verify.*

---

## Step 11 — Path C + ProfileDrafterAgent (1-2 hours) — NEW 2026-06-05

The original POC shipped with one Strands agent (`ExtractorAgent`) and one extraction path (deterministic kernel only for the 5 known unions). On 2026-06-05 the system grew to **two agents and three extraction paths**. Read these to understand the additions — the underlying architecture from Steps 1-10 didn't change.

**Path C — generic Claude extractor:**

1. [`../agents/extractor/extract_generic.py`](../agents/extractor/extract_generic.py) — the new ~285-line module. Dual-mode (Bedrock production / Anthropic local dev), enforces never-fabricate rule, returns canonical `ClassificationRow` objects.
2. [`../agents/extractor/agent.py`](../agents/extractor/agent.py) — see the new 7th `@tool` `extract_via_claude_only` and the updated `Agent(tools=[...])` list.
3. [`../agents/extractor/system-prompt.md`](../agents/extractor/system-prompt.md) — the updated SOP showing Path A / B / C decision logic.
4. [`../agents/extractor/tests/test_extract_generic.py`](../agents/extractor/tests/test_extract_generic.py) — 13 tests.

**ProfileDrafterAgent — auto-author profile + extractor:**

5. [`../agents/profile_drafter/`](../agents/profile_drafter/) — the entire new agent directory. Same shape as `agents/extractor/` (Dockerfile, pyproject, system-prompt, agent.py, steering, tests).
6. [`../agents/profile_drafter/agent.py`](../agents/profile_drafter/agent.py) — 5 `@tool` functions: `analyze_groundtruth`, `draft_profile_yaml`, `draft_extractor_python`, `validate_generated`, `iterate_or_finalize`.
7. [`../agents/profile_drafter/orchestrate.py`](../agents/profile_drafter/orchestrate.py) — end-to-end driver function (not a Strands @tool; the testable Python entry point).
8. [`../agents/profile_drafter/commit_helper.py`](../agents/profile_drafter/commit_helper.py) — opens a draft PR via `gh` CLI per drafted union.
9. [`Overnight_Delivery_Report.md`](Overnight_Delivery_Report.md) + [`Overnight_Audit_Report.md`](Overnight_Audit_Report.md) — what shipped + 31/31 self-audit verification.
10. [`Learning_Lessons.md`](Learning_Lessons.md) **Lesson 8** — full Q&A walkthrough of why both pieces exist, how they compose, and what's still gated on credentials.

**Question:** *the customer has 14 sprinkler unions with no kernel extractor today. Walk through what happens on the first PDF upload for one of those unions, then what happens after ProfileDrafterAgent runs on it.*

---

## After you've finished

You should be able to answer all of these:

- What is the input to this system? What is the final output?
- Where does the deterministic extraction happen, and what does the agent add on top of it?
- Who can call `POST /v1/.../publish`? What server-side check ensures the right state?
- Where is the approval workflow enforced — in the UI, in the API, in the database, or all three?
- If a customer reports "my rate sheet got published without business approval" — which file would you open first to investigate?
- What's in scope for the POC vs deferred to v1.1+? (See [`09_Technical_Implementation_Spec.md`](09_Technical_Implementation_Spec.md) §15.)
- **What are the 3 extraction paths and when does each fire?**
- **What does ProfileDrafterAgent do, and why is it a SEPARATE agent from ExtractorAgent (not just another `@tool` on Extractor)?**

If yes to all — you understand the codebase. From here, [`BUILD_LOG.md`](BUILD_LOG.md) gives you the chronological story of how it was built, and [`AUDIT_REPORT.md`](AUDIT_REPORT.md) + [`AUDIT_VERIFICATION.md`](AUDIT_VERIFICATION.md) show you what was checked and confirmed before merge.
