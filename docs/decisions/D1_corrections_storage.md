# Decision 1 — Human corrections live in Aurora (`cell_corrections`)

**Status:** ✅ DONE — built, deployed, tested (commit `532dcf4`, 2026-06-13).
**Context:** Phase 2 (human-in-the-loop improvement loop), legal/financial system.

## Decision
Reviewer **comments** and **overrides** are persisted to a structured Aurora child
table, **`cell_corrections`** — the legal source of truth. The DynamoDB `overrides`
table is retired (no longer read or written).

## Why
- A human override changes an **authoritative dollar value** → it belongs with the
  content it changes: relational, FK'd to the exact cell + period, with before/after,
  who/when/why, versioned. An auditor/trustee runs one SQL query to see every correction.
- The improvement loop needs **relational reads** ("all open corrections for this
  period/version, joined to cells") — natural in Aurora, awkward in DynamoDB.
- Consistent with the architecture: rate-sheet content + approval/audit → Aurora;
  operational telemetry → DynamoDB.

## Schema (`cdk/assets/schema_init/schema.sql`)
```
cell_corrections(
  id UUID PK, period_id FK rate_periods, version INT, cell_id FK rate_cells,
  union_local TEXT, period TEXT, zone TEXT, package TEXT, column_name TEXT,
  kind ('comment'|'override'), prior_value TEXT, new_value TEXT, reason TEXT,
  actor TEXT, created_at TIMESTAMPTZ, status ('open'|'applied'|'superseded')
)
```
`union_local` + `period` denormalized so readers key by {local, period} (drop-in for the
old DDB access pattern) and a correction stays legible if a cell is recreated in a new
version. Indexed on (union_local, period, kind, created_at) and (cell_id).

## What changed (CDK-deployed)
- `cell-override` + `cell-comment` → `INSERT INTO cell_corrections` (+ `audit_log` feed).
- `ratesheet-get` + `ratesheet-rework` → read overrides from `cell_corrections`.
- `schema-init` `schemaVersion` 1→2 so CloudFormation re-applies the (idempotent) DDL.
- DynamoDB `overrides` table now unused (formal resource removal deferred — needs the
  cross-stack export-ordering dance; harmless meanwhile).

## How it behaves (UI)
Per correction: reviewer presses **Save** in the cell modal → immediate `POST
/v1/cells/{id}/{override|comment}` → Aurora insert. No auto-save-on-type, no batch submit.
Corrections accumulate as `status='open'`; **"Improve"** later consumes all open rows and
flips them to `applied`. The bottom Activity feed reads `audit_log`.

## Verified
Override via API → row in `cell_corrections` (1.0→99.0, attributed) → `ratesheet-get`
applied it (value 99.0, method=override). Comment → row written. Test rows deleted (704
pristine).
