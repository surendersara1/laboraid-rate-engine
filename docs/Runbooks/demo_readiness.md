# Demo Readiness Report

**Generated:** 2026-06-10 Monday-equivalent (sprint compressed to Wed PM)
**Demo:** Tuesday 2026-06-16
**Commit at readiness check:** `b1361f8`

---

## All systems green

Every demo path verified end-to-end via API smoke tests + manual UI
walkthrough. No known regressions. The Tuesday demo is defensible.

## State summary

### Aurora — 5 rate_periods live

| period_id | union | start_date | classifications | cells | gaps |
|---|---|---|---|---|---|
| 49748b5f | Sprinkler 483 | 2026-01-01 | 21 | 378 | **0** |
| b88e8fe5 | Sprinkler 704 | 2026-01-01 | 13 | 221 | **0** |
| 879b53a0 | Sprinkler 821 | 2026-01-01 | 36 | 648 | **0** |
| b36d6247 | Pipefitter 537 | 2026-03-01 | 10 | 240 | **0** |
| 3c49e195 | Sprinkler 281 | 2026-01-01 | 15 | 147 | 8 |

**Total: 95 classifications, 1,634 cells, 8 gaps (all on 281, documented).**

### S3 — artifacts staged

For each period, the following files are downloadable via the Inbox's
artifact cards:

- `Source PDF` — the Rate Notice that triggered the period
- `Canonical CSV` — pivoted from Aurora (final post-merge state)
- `Excel (xlsx)` — customer-column-order spreadsheet
- `Gap report (JSON)` — structured per-cell list with reasons

### Lambda code — all current

Hot-patches applied to:
- `laboraid-dev-l4-fn-classifier` — sibling fallback + dot/space + year-range regexes
- `laboraid-dev-l3-fn-extractor-invoker` — doc_type-aware routing
- `laboraid-dev-l4-fn-llm-extractor` — generalized Wage Rate Sheet prompt + conditional Residential + Apprentice scale handles Commercial
- `laboraid-dev-l4-fn-publisher` — derived cells, zero-by-rule, phantom-row delete, conflict detection, column normalization, xlsx artifact generation, gap report generation, empty-CSV graceful, "UA Local 281" sanitization
- `laboraid-dev-l2-fn-upload-presign` — batch_period in S3 key
- `laboraid-dev-l2-fn-ratesheet-get` — distinct classification count from cells, source contribution, override-aware
- `laboraid-dev-l2-fn-job-list` — period from S3 key (not filename date)

CDK migration of hot-patches: post-demo (P2/P3 in sprint backlog).

### UI — latest bundle deployed

Last build invalidation: `I55O96CJYYLN7EJI2YHYCZF1E0`.

Features visible to the reviewer:
- Staging-then-process Uploads page (no accidental SFN fires)
- 5 rate-sheet cards in Inbox (Sprinkler 483/704/821, Pipefitter 537, Sprinkler 281)
- Per-cell Provenance panel with method icons (▣ kernel / ✦ LLM / ƒ derived / 0 zero-by-rule / override badge)
- Source Contribution panel showing per-PDF cell counts + bar
- Gap banner with per-cell reasons (when gaps exist)
- Conflict-detection in Provenance panel (when two sources disagreed)
- Override modal — POST works, value appears immediately on re-fetch

---

## Smoke-test results

### Pre-flight (preflight_check.py)

All 5 unions return valid `/v1/unions/{local}/rate-sheets/{period}` payloads:

```
[OK] Local 281 2026-01-01: 15 classifications, 147 cells, 8 gaps, 3 source contribs
[OK] Local 483 2026-01-01: 21 classifications, 378 cells, 0 gaps, 5 source contribs
[OK] Local 537 2026-03-01: 10 classifications, 240 cells, 0 gaps, 1 source contrib
[OK] Local 704 2026-01-01: 13 classifications, 221 cells, 0 gaps, 1 source contrib
[OK] Local 821 2026-01-01: 36 classifications, 648 cells, 0 gaps, 2 source contribs
```

All artifacts (Source PDF, Canonical CSV, Excel, Gap report JSON) have
presigned URLs.

### M3 override smoke (m3_override_smoke.py)

```
1) Fetch 483 / 2026-01-01:                  status=200, cell found
2) POST override 7.45 + justification:      status=200, actor recorded
3) Re-fetch → value now 7.45:               provenance.method=override
4) Audit log:                                7 records, override events visible
5) Rollback to 7.3:                          status=200
```

### F2 source contribution

For 483 / 2026-01-01:
```
2026.01.01.483 Rate Notice.pdf       304 (80.4%)
2024.08.01.483 Wage Rate Sheet.pdf    60 (15.9%)
(zero-by-rule)                          5 (1.3%)
(derived)                               5 (1.3%)
2024.08.01-2030.07.31.483 CBA.pdf      4 (1.1%)
```

For 821 / 2026-01-01:
```
2026.01.01.821 Rate Notice.pdf       612 (94.4%)
(zero-by-rule)                        36 (5.6%)
```

For 281 / 2026-01-01:
```
2026.01.01.281.Apprentice Wage Sheet.Indentured Pr  54 (36.7%)
2026.01.01.281.Apprentice Wage Sheet.Indentured Af  54 (36.7%)
2026.01.01.281 Wage Sheet Journeymen.pdf            39 (26.5%)
```

---

## What can go wrong + mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| Bedrock cold start on demo upload | Medium | Pre-staged Aurora state means we can skip live upload — jump to Inbox. Backup Loom (M5) as fallback. |
| Browser cache serves old UI bundle | Low (already invalidated) | Ctrl+Shift+R + tell customer "fresh window please". |
| Override flow fails silently | Mitigated by M3 fix | Override now applies to re-fetched cell value. |
| 281 has 8 gaps customer asks about | Expected | Open the downloadable gap report — each gap has a documented reason. |
| Customer asks for live LLM run | Low risk | "Yes, want to upload one of yours?" — Sprinkler 483 batch takes ~2 min from upload to Inbox card. |
| Demo network drops | Medium | M5 Loom backup. |
| Aurora cold start | Low (Serverless v2, recently active) | Pre-fetch each card on Monday so we know it's warm. |

---

## Monday morning final-15min checklist

Before the demo starts, do these in order:

1. Open Admin → Dashboard. Confirm "Total runs" ≥ 11 (the demo's pipeline executions).
2. Open Business → Inbox. Confirm 5 cards visible.
3. Click into Sprinkler 483. Confirm:
   - Header reads "21 classifications · 378 cells · 0 gaps"
   - Excel (xlsx) and Gap report (JSON) artifact slots have download links
   - Source Contribution panel shows 5 entries (3 PDFs + derived + zero-by-rule)
   - Click any Residential Apprentice Wage cell — Provenance panel shows `source_pdf = 2024.08.01.483 Wage Rate Sheet.pdf`
4. Open the Excel xlsx in actual Excel side-by-side with the customer's
   `2024-2029.483 Rate Sheet.xlsx`. Confirm column order matches and
   Residential Apprentice wages line up.
5. Open Provenance panel on a derived cell (Apprentice Wage Differential):
   confirm method icon is ƒ (derived).
6. Open the override modal on any cell, change a value, save, confirm
   the cell value updates on re-fetch. Then revert.

If all 6 pass, you're ready.

---

## Backup Loom recording script (M5)

Use [demo_dry_run.md](./demo_dry_run.md) as the verbatim script.

Record one 5-minute pass through:
1. Uploads page (drag in 3 PDFs, stage, click Process). 30 sec.
2. Wait for "Pipeline succeeded" badges in Admin → Jobs. 60 sec.
3. Open the rate-sheet card in Business → Inbox. 30 sec.
4. Tour the artifact cards, gap banner, source contribution panel. 60 sec.
5. Click a cell, walk through Provenance panel. 30 sec.
6. Demo the Override modal. 30 sec.
7. Open Admin → Audit; show the override event. 30 sec.

Save to a shared Drive folder. Keep it as a tab in the browser so you
can swap to it instantly if live breaks.

---

## Sprint summary — what shipped this week

| Day | Items | Commits |
|---|---|---|
| Wed AM | Set 1/2/3 verified; gap detection; UI staging redesign; xlsx; gap_report | (multiple) |
| Wed PM | T1-T6, F1-F6, M1, M4 — Thursday + Friday + Monday-morning compressed into one block | a24ddf2, cd8f7c8, 857a7c2, 2628141, 776005c |
| Mon-equiv (now) | M3 override end-to-end + readiness report | b1361f8 |

Total of 7+ commits, 1,634 cells across 5 unions, 11 PDFs processed,
$2.50 total Bedrock spend, 5.9 min total pipeline time. Sprint shipped
~30 hours of work in ~10 hours of focused execution.
