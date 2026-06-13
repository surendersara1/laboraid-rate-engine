# Phase 3.0 — Repo-vs-Live Code Audit (R1)

**Date:** 2026-06-12 · **Account/region:** 908106425069 / us-east-2 · **Profile:** laboraid
**Method:** downloaded every live `laboraid-*` function zip (`lambda get-function` →
Code.Location), unzipped, diffed `handler.py` against `lambdas/` **with CRLF normalized**
(`diff --strip-trailing-cr`). Read-only — only downloads. Script: `_TMP_/audit_code.sh`.

## Result: ZERO regression risk — repo is canonical
**No live function has code newer than the repo.** Every live function is either identical
to the repo or runs an OLDER version. So a Phase-3 deploy's code re-push only ever
**upgrades** live — it cannot roll back a live hot-fix.

### Identical to repo (32 functions) — incl. everything the live product uses
- **Dual-control + product API** (the demo path): ratesheet-approve, ratesheet-reject,
  ratesheet-publish, ratesheet-unapprove, ratesheet-get, ratesheet-list, ratesheet-audit,
  ratesheet-rework, profile-list, profile-update, job-status, job-list, job-abort,
  job-retry, cell-comment, cell-override, agent-list, agent-toggle, audit-list,
  upload-presign. ⚠️ The first cdk diff showed huge 154–502-line diffs here — **all were
  pure CRLF (repo) vs LF (live-zip) line-ending noise**; CRLF-normalized diff = 0 real lines.
- **New synthesizer pipeline:** synthesizer, synth-publish, profile-builder, batch-planner,
  batch-process, ocr-preprocess, classifier, llm-extractor, extractor-invoker.
- **Still-used renderer:** renderer-xlsx.

### Repo AHEAD of live (7 functions) — old pipeline, **unused by the new product flow**
Live runs older/stub versions; deploy upgrades them. Not called by `Plan→Synthesize→
SynthPublish`, so zero product impact either way.

| Function | True diff (CRLF-normalized) | Direction |
|---|---|---|
| publisher | 28 | repo adds REPLACE-mode block (synthesizer clean-replace) |
| review-router | 81 | repo = full impl; live = older stub |
| validator-checksum | 95 | repo = full impl; live = ~10-line stub |
| validator-confidence | 79 | repo = full impl; live = stub |
| validator-range | 82 | repo = full impl; live = stub |
| renderer-articles | 90 | repo = full impl; live = stub |
| renderer-csv | 69 | repo = full impl; live = stub |

### Not audited
- `schema-init` (l3): inline/bootstrap function, no `lambdas/` source dir. One-shot DDL;
  not part of the request path. Excluded.

## Decision
**No back-porting required** (repo never trails live). Proceed to Phase 3 import + deploy.
The 7 old-pipeline upgrades are harmless; a follow-up cleanup PR can delete the unused
validators/renderers/publisher after IN_SYNC (see DIFF_REVIEW R4).
