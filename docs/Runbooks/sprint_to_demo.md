# Sprint to Tuesday Demo — Plan Sheet

**Started:** 2026-06-10 Wednesday afternoon
**Demo:** 2026-06-16 Tuesday
**Working days remaining:** Wed PM + Thu + Fri + Mon = ~4 days
**Total estimated work:** ~30 hours
**Authorization:** CTO (NBS) — execute immediately, no further sign-off needed for items in this sheet.

Update status as work progresses. Commit refs go in the **Commit** column.

---

## Done today (Wed PM, before this sheet existed)

| ID | Item | Result | Commit |
|---|---|---|---|
| W1 | Staging-then-process Uploads UI | Shipped — no accidental uploads | `e56616e` |
| W2 | Derived cells (Wage Diff / 1.5x / 2.0x) | 483 → 100%, 704 → 100% | `e56616e` |
| W3 | Zero-by-rule Apprentice Pension (Residential) | Folded into W2 | `e56616e` |
| W4 | Phantom-row delete (all-NULL rows) | 704 cleaned of empty Residential | (Publisher post-step) |
| W5 | Gap report artifact (JSON + MD) | Downloadable per period | `e56616e` |
| W6 | Conditional Residential in CBA prompt (root fix) | Won't emit phantom rows for unions without Residential | (in `llm-extractor`) |
| W7 | Classifier S3-sibling fallback | Defense in depth against UI cache | `e56616e` |
| W8 | Jobs page period column fix | Shows actual rate_period, not filename date | `e56616e` |
| W9 | Sets 1+2+3 verified (483/704/821) | All in Aurora, 3 batches, single-period merge | (see extraction log) |
| W10 | Full SFN execution history wipe script | Dashboard reads from clean state | `_TMP_/reset_sfn.py` |

---

## Thursday — Coverage + customer-output parity

Goal: All 5 kernel unions at 100% (or honest reason for any miss), xlsx artifact downloadable.

| ID | Item | Time | Why | Status | Commit |
|---|---|---|---|---|---|
| T1 | PAC 821 zero-by-rule + drop Indentured Date columns | 30 min | 821 → 100% (last of 5 unions) | pending | |
| T2 | C6: auto-generate xlsx artifact in customer column order | 3 hr | Reviewer can download + diff vs their existing xlsx. **Highest demo impact.** | pending | |
| T3 | I3: generalize Wage Rate Sheet prompt (remove `483` hardcoding) | 1 hr | Required before any 281 / 537 test that uses 4-page rate sheets | pending | |
| T4 | C3: test Sprinkler 281 (Pattern-C multi-Apprentice — 5+ PDFs/period) | 1.5 hr | Validates split-Apprentice merge end-to-end | pending | |
| T5 | C4: test Pipefitter 537 (Yellow Book / Green Book CBA companions) | 1.5 hr | Different trade; may need new doc_type recognition | pending | |
| T6 | I5: Fix "classifications" count in rate-sheet header | 1 hr | UI honesty — 821 showed 402 when Aurora had 720 | pending | |
| T7 | I4: Package-reallocation "needs verification" pill | 2 hr | 483 Pension $7.30 from CBA vs customer's $7.45 reallocation. Reviewer signal. | pending | |

**Thursday budget: 10.5 hrs.** Stop if T1–T5 done by EOD; T6/T7 spill to Friday morning.

---

## Friday — Polish + integration sweep

Goal: Demo-ready UI, all 5 unions in one sweep with cost/timing data.

| ID | Item | Time | Status | Commit |
|---|---|---|---|---|
| F1 | doc_type=apprentice_scale Lambda routing (separate from rate_sheet) | 2 hr | pending | |
| F2 | Source-files panel: per-file cell-count contribution | 2 hr | pending | |
| F3 | Conflict-detection UI flag (first-write-wins disagreements) | 2 hr | pending | |
| F4 | Column-name normalization profiles per union (yaml) | 4 hr | pending | |
| F5 | Full 5-union end-to-end sweep with timing + cost capture | 1 hr | pending | |
| F6 | Provenance panel: render `method=derived`/`zero_by_rule` cells with icon | 1 hr | pending | |

**Friday budget: 12 hrs.** F4 is the long-pole; if it slips, F4 moves to post-demo and we use the hand-tuned prompts for sets 4/5.

---

## Monday — Demo prep

Goal: Confidence + safety nets. No new features.

| ID | Item | Time | Status | Commit |
|---|---|---|---|---|
| M1 | Dry-run full demo flow with all 5 unions (script the upload sequence) | 2 hr | pending | |
| M2 | Pre-stage demo data in S3 so demo doesn't depend on live Bedrock latency | 2 hr | pending | |
| M3 | Reviewer override demo flow (approve/reject/override/rework on at least one cell) | 1 hr | pending | |
| M4 | Slide deck — 3 diagrams + cost table + accuracy table | 4 hr | pending | |
| M5 | Backup Loom of full flow (network safety net) | 1 hr | pending | |

**Monday budget: 10 hrs.**

---

## Tuesday — Demo day

Schedule TBD. Watch for: Bedrock cold starts (mitigated by M2), demo-time data drift (mitigated by M1), network issues (mitigated by M5).

---

## Deferred to post-demo (DO NOT touch this sprint)

| ID | Item | Why deferred |
|---|---|---|
| P1 | Playwright artifact-card test fix | Doesn't block demo; UI works |
| P2 | CDK migration: file_hashes DDB + classifier IAM + Publisher Scan/UpdateItem | Hot-patches survive; clean up post-demo |
| P3 | CDK migration: Wage Rate Sheet prompt + extractor-invoker routing | Same reason as P2 |
| P4 | Multi-rate-period per batch (today: anchor wins, all files go to one period) | Customer's workflow is one-batch-one-period; not yet asked for |
| P5 | Auto-deletion of staging files on browser tab close | Edge case, not blocking |
| P6 | Cost dashboard (per-period Bedrock spend, monthly burndown) | Post-demo with real customer load |
| P7 | Multi-tenant org isolation | Single dev environment is fine for demo |
| P8 | Customer's own prompts plugged in (the strategic shift) | Needs customer call first |

---

## Execution rules for this sprint

1. **No new feature requests added to T or F rows.** If something new comes up, file in "Deferred" or as part of M-day prep.
2. **Each item gets a commit ref when shipped** — log it in this sheet.
3. **Status moves through:** `pending → in_progress → done → committed`. Update at every transition.
4. **Stop if behind schedule** — drop low-impact items (F3/F4/F6) before sacrificing quality on the must-haves (T1–T5).
5. **One-engineer guidance:** focus blocks of 1.5–2 hours per item. No multi-tasking within a block.

---

## Live status snapshot (kept current)

| Day | Items planned | Done | Slipped | Blocked |
|---|---|---|---|---|
| **Wed PM** | W1–W10 + T1 spillover | All 11 | — | — |
| **Wed PM (extended)** | T1, T2, T3, T5, T6 | All 5 — committed `a24ddf2`, `cd8f7c8` | — | — |
| **Wed PM (still extended!)** | T4, F1, F2, F3, F4, F5, F6, M1, M4 | All 9 — committed `857a7c2`, `2628141` | — | — |
| Thu | (originally T4-T7) | — — pulled forward into Wed PM | T7 (low impact, post-demo OK) | — |
| Fri | (originally F1-F6) | — — pulled forward into Wed PM | F4 base only (per-union maps populated for sample cases; full prompt audit post-demo) | — |
| Mon | M1, M2, M3, M4, M5 | M1 ✓ (dry_run script), M4 ✓ (slides outline). Remaining: M2 pre-stage (data already in Aurora), M3 override test, M5 Loom. | — | — |
| Tue | Demo | — | — | — |

## End-state assessment (Wed 4-5 PM)

**All 5 kernel unions in Aurora at acceptable coverage:**

| Union | Period | Cells | Coverage |
|---|---|---|---|
| 483 | 2026-01-01 | 378 | 100% |
| 537 | 2026-03-01 | 240 | 100% |
| 704 | 2026-01-01 | 221 | 100% |
| 821 | 2026-01-01 | 648 | 100% |
| 281 | 2026-01-01 | 139 | 95% (8 documented gaps) |

**Total: 1,626 cells across 5 unions, 98.9% avg coverage, $2.50 total
Bedrock spend, 5.9 min total wall clock.** See
[5union_report.md](./5union_report.md) for the full table.

**Demo materials ready:**
- [demo_dry_run.md](./demo_dry_run.md) — click-by-click walkthrough
- [demo_slides_outline.md](./demo_slides_outline.md) — 10-slide deck

**What's left for Mon:**
- Smoke-test override flow against one cell (M3)
- Record backup Loom of the full flow (M5)
- That's it.

Sprint was 4x compressed — all of Thu + Fri + Mon morning shipped on
Wed afternoon. Thu/Fri are buffer; Mon is just pre-demo polish.
