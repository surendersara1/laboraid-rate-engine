# LaborAid Rate Engine — Design

**Single source of truth for the current system.** Supersedes all prior design,
audit, and build-log documents. Last updated 2026-06-12.

---

## 1. What it does

Union benefit funds publish rates in PDFs — a collective bargaining agreement
(CBA) plus periodic rate notices / wage sheets. LaborAid turns those PDFs into a
clean, structured **rate sheet** (classifications × funds, with overtime and
indenture-cohort handling), stores it in a database, and runs it through a
two-person review/approval workflow before publishing.

**Core principle — extraction + mapping, never fabrication.** Every dollar value
is *extracted by an LLM from the source PDFs* and *mapped to the union's
canonical schema*. The system never copies answers from a client's output sheet,
and never invents a number — values absent from the documents are **flagged as
gaps**, not guessed.

## 2. Architecture at a glance

```
Upload PDFs ──► POST /v1/batches/process ──► Step Functions (one execution/batch)
                                              │
                ┌─────────────────────────────┼─────────────────────────────┐
                ▼                             ▼                             ▼
              PLAN                        SYNTHESIZE                    SYNTH-PUBLISH
        (batch-planner)                 (synthesizer)                 (synth-publish)
   classify + order docs;          load union PROFILE from         clean-replace write to
   resolve union + period          Aurora (auto-onboard from       Aurora rate_periods +
                                   CBA if missing); Bedrock          rate_cells (cohorts in
                                   Claude Opus 4.5 reads ALL         dimensions); record
                                   docs against the profile →        source-PDF lineage;
                                   rate sheet; compute derived       emit CSV + XLSX
                                   columns; emit canonical CSV/JSON
                                              │
                                              ▼
                          Aurora (system of record) ──► Business review
                                              │              (Inbox → grid →
                                              ▼               dual-control →
                          Artifacts in S3 (CSV, XLSX,          approve → publish)
                          source PDFs, audit JSON)
```

The pipeline is intentionally **three stages**. The earlier design (a per-document
kernel + a Strands/AgentCore agent + a mechanical publisher merge) was replaced
by a single **objective-driven synthesis pass** — the LLM reasons over all of a
period's documents *together* against a fixed profile, which eliminated the
duplicate-row/column and cohort-merge failures of the per-document approach.

## 3. Components

| Component | Type | Responsibility |
|---|---|---|
| `batch-process` | API Lambda | `POST /v1/batches/process` — starts one SFN execution per batch (explicit "Process" button; no auto-trigger) |
| `batch-planner` | Lambda (Plan) | Classify each PDF (CBA vs rate notice), order them, resolve union + target period |
| `synthesizer` | Lambda (Synthesize) | Load the union profile from Aurora; **Bedrock Claude Opus 4.5** reads all docs against it; produce the rate sheet; compute OT/derived columns in code; emit CSV + XLSX + JSON. Auto-onboards an unknown union by invoking the profile-builder on its CBA. |
| `synth-publish` | Lambda (SynthPublish) | Clean-replace write to Aurora (`rate_periods` + `rate_cells`); indenture cohorts stored in `rate_cells.dimensions`; record source-PDF lineage in `source_files` + per-cell `provenance` |
| `profile-builder` | Lambda | Read a union's CBA → extract its STRUCTURE (classifications, zones, funds, cohorts, OT multipliers) using canonical Master-List names → save to `unions.profile_yaml` |
| `ratesheet-get` | API Lambda | Serve one period for review: cells (with `dimensions`/`value_type`), counts, gaps, artifacts (source PDFs, CSV, XLSX), source contribution |
| `ratesheet-approve/reject/publish/unapprove` | API Lambdas | Dual-control review lifecycle |
| `profile-list` / `profile-update` | API Lambdas | Read/write `unions.profile_yaml` (Admin **Profiles** tab) |
| `job-list` / `job-status` | API Lambdas | Admin **Jobs** feed + per-job timeline with a **call trace** (classifier, Bedrock, Aurora, S3) and artifacts |

## 4. Profiles — the scaling mechanism

A **profile** is a union's frozen rate-sheet schema: its zones, classifications
(canonical package names), fund columns (with percent flags), indenture-cohort
rules, OT/derived multipliers, and column order. **Structure only — never dollar
values.**

- **System of record:** Aurora `unions.profile_yaml` (jsonb), version-tagged,
  editable from the Admin Profiles tab. Not bundled in code — adding a union is a
  data change, **no redeploy**.
- **Built by the profile-builder** from the union's CBA, mapping every fund and
  classification to the Master-List canonical name.
- **Auto-onboard:** if the synthesizer finds no profile for a local, it invokes
  the profile-builder on the batch's CBA, saves the profile, and continues — in
  one pass. An unseen union becomes processable with **zero code changes**.
- The profile is the synthesizer's **target schema** (so output names/structure
  are exact) and the offline **validation oracle**.

The synthesizer fills only the *values* by reading the rate notices; the profile
supplies the *vocabulary and shape*. Derived OT columns (Wage 1.5×, 2.0×,
Differential, Temporary Heat, …) are computed in code from the base wage — LLMs
don't do the arithmetic.

## 5. Data model (Aurora Serverless v2, PostgreSQL, via RDS Data API)

- **`unions`** — `local`, `trade`, `parent_intl`, **`profile_yaml` (jsonb)**, `profile_version`
- **`rate_periods`** — one row per (union, period): `start_date`, `end_date`,
  `status`, `approval_state`, `reviewed_by`/`approved_by`/`published_by` (+ timestamps),
  `rejection_reason`/`rejection_tags`, `source_files` (jsonb — uploads, output_csv,
  output_xlsx), `canonical_json` (row/gap summary). CHECK `dual_control_required`
  enforces reviewer ≠ approver on approval.
- **`rate_cells`** — one per (period, zone, package, column): `value`, `value_type`
  (`currency`/`percent`), **`dimensions` (jsonb — indenture cohort: Indentured
  Before/After)**, `provenance` (jsonb — method, model, source_pdfs), `confidence`
- **`audit_log`** — every review/approve/reject/publish action

## 6. Review lifecycle (dual control)

`pending_review` → **Mark Reviewed** (any Business user) → `pending_approval` →
**Approve** (a *different* Business user) → `approved` → **Publish** → `published`.
Self-approval is blocked (`dual_control_violation`). **Reject** (reason +
optional tags: missing_data / wrong_extraction / cba_mismatch / other) returns the
sheet to a terminal `rejected` state. Maker-checker control — no single person can
push rates to production.

## 7. UI

React + Vite SPA on CloudFront/S3, Cognito-authenticated.
- **Admin:** Dashboard, Uploads (stage → Process), **Jobs** (+ Job Detail: stage
  timeline, per-stage **call trace**, artifacts), **Profiles**, Agents, Audit, Costs.
- **Business:** Inbox, By Union, Approved, Rejected, Review Queue, and the rate
  sheet review page — a **pivoted grid** (classifications + indenture cohorts as
  rows, funds as columns), source PDFs, CSV/XLSX downloads, source contribution,
  and the dual-control action bar.

## 8. Security

- **Cognito** user pool; groups **Admins / Operations / Business**; JWT authorizer
  on **API Gateway v2 (HTTP API)**; per-route group gates.
- **Bedrock PII Guardrail** = ANONYMIZE (masks any phone/email/SSN in the source
  PDFs before the model sees them; does not block the document).
- Scoped IAM: each Lambda role gets only the RDS Data API / S3 / Bedrock / invoke
  permissions it needs. KMS-encrypted buckets.
- Customer PDFs and customer output sheets are **never** committed to the repo.

## 9. Model

**Claude Opus 4.5 on Amazon Bedrock** (`us.anthropic.claude-opus-4-5-…`) via
cross-region inference. Opus 4.8 and Fable 5 are not entitled on the account
(AWS-side grant required). Bedrock caps a request at 100 PDF pages total — the
synthesizer keeps the CBA (structure) + current-period notice and drops older
notices to fit; the profile-builder caps a large CBA to its first 100 pages.

## 10. Status & known limitations

- **Validated:** Sprinkler 281 (cohorts — the hardest) extracts row-for-row,
  cell-exact vs the client; 704 matches on all 13 classifications. Onboarding
  proven on fresh unions (709, Pipefitter 12 with a 130-page CBA) with no code
  changes.
- **In progress:** 537/821/483 — structure is exact; some per-classification wage
  derivations (zone premiums, apprentice % ladders) still mis-extract. Some
  national funds (e.g. RESA) aren't in the local PDFs and are correctly flagged as
  gaps rather than fabricated.
- **Deferred:** CDK source is behind the live (boto3-applied) state — a one-time
  reconciliation is required before `cdk deploy`/`destroy` reproduces the running
  system. Standalone gap-report JSON artifact (gaps already surface in the review
  banner).

## 11. Repository map

- `lambdas/processing/` — batch-planner, synthesizer, synth-publish, profile-builder, publisher (legacy CSV path)
- `lambdas/api/` — batch-process, ratesheet-*, profile-*, job-*
- `lambdas/shared/` — master_data (canonical names), pdf_utils
- `profiles/` — per-union profiles (structure only; mirrors `unions.profile_yaml`)
- `ui/` — React SPA
- `cdk/` — infrastructure (pending reconciliation)
- `kernel/` — legacy deterministic kernel (git subtree; no longer in the live path)
