# Architecture & Flow

## The pipeline

Every batch of PDFs runs through one AWS Step Functions execution with three
stages. You trigger it explicitly with **Process this batch** — nothing reaches
the AI or the database until you do.

```
Upload PDFs ─► Process ─► Step Functions
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
      PLAN            SYNTHESIZE        SYNTH-PUBLISH
  classify +      Claude Opus 4.5     write to Aurora;
  order docs;     (AWS Bedrock)       cohorts stored as
  resolve union   reads ALL docs      row dimensions;
  + period        against the         record source-PDF
                  union's profile     lineage; emit
                  → rate sheet        CSV + Excel
```

**1 · Plan** — classifies each PDF (CBA vs rate notice), orders them, and resolves
the union and the target rate period.

**2 · Synthesize** — the core step. The union's **profile** is loaded from the
database, and **Claude Opus 4.5 on Amazon Bedrock** reads *all* the documents
together against it, producing the complete rate sheet in one reasoning pass.
Reading the documents together (rather than one at a time) is what lets the model
reason about precedence — a current rate notice supersedes the CBA — and about
indenture cohorts and fund naming. Overtime/derived columns are then computed in
code from the base wage (the AI reasons; code does the arithmetic).

**3 · Publish** — the finished rows are written to the database. Indenture cohorts
are stored as row dimensions; every value records the source PDFs that produced
it; the canonical CSV and Excel are emitted as downloadable artifacts.

## Profiles — the union's schema, learned from its CBA

A **profile** is a union's rate-sheet structure: its zones, classifications, fund
columns, indenture-cohort rules, and overtime multipliers — **structure only,
never dollar values**. It is:

- **Learned by the AI from the union's CBA** and mapped to your canonical names.
- **Stored in the database** as the system of record, editable in the admin
  console — so adding or changing a union needs no redeploy.
- **Auto-built on first upload**: send an unseen union's CBA + notices and the
  system builds its profile and produces the rate sheet in the same run.

The profile is the AI's exact target schema (so names and structure are right)
and the validation oracle (so output can be checked against the client's sheet).

## Review & control

`pending review` → **Mark Reviewed** (a reviewer) → **Approve** (a *different*
person — self-approval is blocked) → **Publish**. Or **Reject** with a reason.
Maker-checker dual control, enforced in the database and fully audited.

## Foundation

- **Compute:** AWS Lambda + Step Functions
- **AI:** Amazon Bedrock — Claude Opus 4.5, with a PII guardrail that masks any
  personal data in the PDFs before the model sees it
- **Data:** Aurora Serverless v2 (PostgreSQL); documents and artifacts in S3
- **Access:** Amazon Cognito with role-based groups; every API route gated
- **UI:** React single-page app on CloudFront

Every value is traceable end to end — from the source PDF, through the AI call,
to the reviewed and published cell.
