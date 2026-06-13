# Dashboard Read-Model Design (CQRS on DynamoDB)

**Goal:** every Admin + Business dashboard tab reads ONLY from DynamoDB — fast,
paginated, no live calls to Step Functions or Aurora on the read path. Writes happen
once, on state change, via event-driven writers.

## Principle
```
SOURCE OF TRUTH                 WRITE (once, on event)            READ (every tab)
- Step Functions (run state)    EventBridge -> job-writer ----.
- Aurora (rate-sheet truth)     synth-publish / review -------+--> DynamoDB --> dashboards
                                                              '   (one indexed query)
```
The dashboards never query SFN/Aurora live. DynamoDB is the **read projection**; Aurora
stays the system of record for rate-sheet *content*, projected into Dynamo for *listing*.

## Tables (all PAY_PER_REQUEST, KMS, PITR, retain)
| Table | PK / SK | GSI | Feeds tabs |
|---|---|---|---|
| `jobs` (exists) | `job_id` | **by-recency**: `gsi1pk`="JOB" / `started_at` | Admin Jobs, JobDetail, Dashboard |
| `ratesheets` (new, Phase B) | `union#period` | **by-state**: `review_state` / `updated_at` | Business Inbox/ReviewQueue/Approved/Rejected/ByUnion |
| `audit` (new, Phase C) | `subject` (union#period) | **by-time**: `gsi1pk`="AUDIT" / `ts` | Admin Audit, Dashboard activity |

## Writers (event-driven, write-once)
- **job-writer** (Phase A) — EventBridge rule on *"Step Functions Execution Status Change"*
  for the main SFN. Upserts the `jobs` row: status, union/local/period, started/stopped,
  duration, source PDFs, output artifacts, stage trace. Resolves union/period from the
  execution input/output **once** at the event (not per dashboard load).
- **ratesheet projection** (Phase B) — `synth-publish` writes the row on publish; the
  review Lambdas (approve/reject/unapprove/publish) update `review_state` on each action.
- **audit projection** (Phase C) — review + cell Lambdas append audit rows; optional
  DynamoDB-stream rollup for Dashboard activity counts.

## Read endpoints (rewritten to query Dynamo)
- `GET /v1/jobs` → query `jobs` by-recency GSI, newest-first, paginated, optional status.
- `GET /v1/jobs/{id}` → `get_item` on `jobs` (full trace + artifacts inline).
- Business list endpoints → query `ratesheets` by-state GSI.
- `GET /v1/audit` → query `audit`.
- Dashboard → cheap counts over the GSIs (or maintained counters).

## Rollout (deployed via `cdk deploy`; additive, no manual deletes)
- **A — Jobs:** GSI + job-writer + EventBridge + backfill + rewrite job-list/detail + UI.
- **B — Business projection:** `ratesheets` table + writers + Business tabs read Dynamo.
- **C — Audit + rich Dashboard.**

## Backfill
One-time script replays existing SFN executions through the same resolve logic into the
`jobs` table, so the dashboard shows history immediately (and survives SFN's 90-day cliff).
