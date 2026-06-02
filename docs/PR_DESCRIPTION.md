# feat: AWS + Strands deployment of the LaborAid Rate Engine POC

Builds the AWS-deployable monorepo around Ashwani's deterministic extraction
kernel: 9 Python CDK stacks, one Strands `ExtractorAgent` on AgentCore Runtime,
19 API Lambdas + validators/renderers, a two-persona React SPA with a business
approval gate, Step Functions orchestration, observability, CI, and an e2e smoke.

## What was built (group by group)

| Group | Items | Summary |
|---|---|---|
| **A — CDK foundation** | A.1–A.6 | App bootstrap, `MandatoryTagsAspect` (13 tags), `Config`, `name()` helper, tagged construct wrappers, `StrandsAgentRuntime`. |
| **B — Storage & security** | B.1–B.2 | KMS CMK + Cognito (4 groups, MFA); 6 S3 buckets, 7 DynamoDB tables, Aurora Serverless v2 + Data-API schema-init. |
| **C — Processing + AI** | C.1–C.3 | ExtractorAgent container (6 kernel tools + Bedrock fallback + steering), processing stack (classifier + ECR + AgentCore Runtime), Bedrock PII Guardrail. |
| **D — Validation + rendering** | D.1–D.3 | 4 validators + 3 renderers; validation stack (3 SNS topics, EventBridge bus, SES, Slack-notifier, DLQ). |
| **E — API + UI** | E.1–E.6 | 19 API Lambdas (incl. publish-409 guard); HTTP API + Cognito authorizer + WAF; two-persona Vite/React/TS SPA (15 pages); S3+CloudFront+OAC hosting. |
| **F — Orchestration + observability** | F.1–F.3 | Step Functions main pipeline + S3/EventBridge trigger; 5 dashboards + 6 alarms + CloudTrail; RUNBOOK/ARCHITECTURE/ONBOARDING. |
| **H — Integration** | H.1–H.3 | e2e smoke (704 = 99.6% PASS), CI workflow (backend/ui/kernel), README. |
| **G — Kernel extractors 281+821** | G.1–G.4 | **Deferred to the kernel harness** (scanned wage sheets → OCR; ≥98%/≥95% gates). Groundwork verified; see `docs/BUILD_LOG.md`. |

## Commits

29 `[BUILD-XX]` commits (A.1 → H.3), one per build item, each gated.

## Acceptance status (BUILD §4)

- **§4.1 repo checks:** `npx cdk synth` exits 0 (9 stacks); `ruff` / `black` /
  `mypy --strict` (25 files) / `pytest` (cdk 9 stack tests + 30 lambda tests) all
  green; UI `typecheck`/`lint`/`vitest`/`build` green; kernel `run.py --all`
  reproduces 704 = 99.6%, 483 = 100%, 537 = 67.4%.
- **§4.2 smoke:** local kernel smoke passes; deployed-path upload flow scripted.
- **§4.4 SOW match:** Strands `ExtractorAgent` on AgentCore ✅; Bedrock + Guardrail
  ✅; React SPA on S3+CloudFront+OAC via Python CDK ✅; two-persona UI ✅; business
  approval workflow incl. publish-409 ✅; admin agent-toggle + `agent-config` DDB ✅.

## Known gaps / next steps

- **Group G (281 + 821 extractors)** via the kernel's `.claude/harness/` —
  verified-ready runbook in `docs/BUILD_LOG.md`.
- **Deploy** is the human's call (needs AWS account, Bedrock model access,
  AgentCore in us-east-1). Custom domain (ACM/Route53) activates when a hosted
  zone is supplied.
- **F369 library gaps** surfaced (kit §3): AgentCore-Runtime-as-CFN (G1), business
  approval state machine (G2), Aurora schema-init CR (G5), two-persona route-guard
  SPA (G6) — derived inline; candidates for library extraction.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
