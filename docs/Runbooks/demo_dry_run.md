# Demo Dry-Run Script

**Target:** Tuesday 2026-06-16 demo to LaborAid.
**Demo length:** ~20 min walk + 10 min Q&A.
**Audience:** customer's CTO + reviewer team.

The story arc, in order, with click-by-click direction. Practice this
once Monday morning before the live run.

---

## Opening (2 min) — "Why this exists"

Verbal frame:

> "You currently produce 1 rate sheet per union per period by hand. We've
> built a system that produces the same rate sheet automatically from the
> same source PDFs you already use, with full provenance per cell, an
> audit trail, and a reviewer workflow. Demo: 5 unions, 11 PDFs, ~6
> minutes of pipeline time, ~$2.50 of Bedrock spend. End result, an xlsx
> you can open in Excel and diff against your existing one."

Slide cue: 5-union report table (from
[5union_report.md](./5union_report.md)).

---

## Act 1 — Upload (3 min)

**Click 1:** Open Admin → Uploads in a fresh browser tab.

**Verbal:**
> "Today the customer's process is to send a Rate Notice plus a CBA per
> period. Sometimes also a Wage Rate Sheet or a separate Apprentice
> Scale. Our Uploads page stages everything first; nothing reaches AWS
> until you click Process."

**Click 2:** "+ Add PDFs" — pick the 3 Sprinkler 483 PDFs:
- `2026.01.01.483 Rate Notice.pdf`
- `2024.08.01.483 Wage Rate Sheet.pdf`
- `2024.08.01-2030.07.31.483 CBA.pdf`

**Point at the staging table:**
- Role pills correctly classify each (green Rate Notice, blue Wage Rate
  Sheet, indigo CBA)
- "Target rate period: **2026-01-01**" — inferred from the Rate Notice
- No warnings

**Click 3:** "▶ Process this batch"

**Verbal while it runs (~2 min):**
> "Three SFN runs in parallel. The Rate Notice goes through the
> deterministic kernel — hand-coded Python per union, 99-100% accurate
> by design. The Wage Rate Sheet and CBA go through Bedrock Claude Sonnet
> with doc-type-specific prompts. Publisher merges them all into one
> rate_period in Aurora, with cell-level provenance."

---

## Act 2 — Review (5 min)

**Click 4:** Navigate to Business → Inbox.

Point out the **5 union cards** (483, 537, 704, 821, 281). Click into
**Sprinkler 483 · 2026-01-01**.

**Header bar — call out each chip:**
- 21 classifications · 378 cells · **0 gaps**
- Approval state: pending review
- Pipeline succeeded

**Scroll to artifact cards:**
- Source PDF (the 2026 Rate Notice)
- **Canonical CSV** — download, briefly open in Excel
- **Excel (xlsx)** — download, open side-by-side with the customer's
  existing 2024-2029 Rate Sheet.xlsx. *This is the moment.* Same column
  order, matching values down the column.
- **Gap report (JSON)** — open, point out the structured per-cell list
  with reasons.

**Scroll to Source Contribution panel (F2):**
> "The reviewer sees at-a-glance which PDF filled which cells. The Rate
> Notice (kernel) did 280; the Wage Rate Sheet filled the Residential
> Apprentice scale (88 cells); the CBA filled the residual benefit
> columns; 'derived' is the 10 cells the Publisher computed (Wage Diff
> = Wage × 1.15, Apprentice Pension = $0 by Local 483 rule)."

**Click a cell in the cells table — open the Provenance panel:**
- Click on `Residential / Apprentice Class 1 / Wage`
- Provenance shows: ✦ LLM, source_pdf = `2024.08.01.483 Wage Rate
  Sheet.pdf`, confidence 85%
- Click on `Residential / Apprentice Class 1 / Wage Differential`
- Provenance shows: ƒ derived, derived_from = `Wage x 1.15`
- Click on `Residential / Apprentice Class 1 / Pension`
- Provenance shows: 0 zero by rule, rule text visible

**Verbal:**
> "Every cell in this rate sheet is traceable back to either a specific
> PDF + extraction method, a documented derivation formula, or a Local
> 483 convention. Nothing is fabricated."

---

## Act 3 — Reviewer Workflow (5 min)

Demonstrate the human-in-the-loop:

**Override flow (M3):**
- Pick a cell. Click "✎ Override". Type a new value. Save.
- Show the cell now has a yellow Override pill. Original value remains
  in provenance.
- Show audit log entry for the override.

**Reject + Rework flow:**
- Click "Reject" on the period. Type a reason ("Residential Pension
  should be $7.45 per 1/1/2026 reallocation notice, not CBA's $7.30").
- Click "Rework with AI" — explains the workflow without actually
  running (Bedrock invocation in live demo = risky).

**Approve flow:**
- Click "Approve". Period goes to approved state.
- Point out: the xlsx is the same xlsx LaborAid's downstream systems
  could pull from this S3 location automatically.

---

## Act 4 — Operational (3 min)

**Click 5:** Admin → Dashboard.

Show metrics: total runs, succeeded/failed mix, duration chart, pending
review count.

**Click 6:** Admin → Jobs.

Show the per-PDF audit list. Click into one job → Step Functions trace
visible.

**Click 7:** Admin → Audit.

Walk through one event: who, when, what (e.g., "user approved
0f207243-…").

---

## Act 5 — The Other 4 Unions (2 min)

Quick tour of the other 4 cards in the Inbox:

- **537** — Pipefitter (different trade). 240 cells from one Rate Notice
  through the deterministic kernel.
- **704** — No Residential package in their CBA. System correctly
  produces Building-only, no phantom rows.
- **821** — 51 classifications across Commercial / Industrial /
  Low-Commercial / Residential. The most complex union by far. 648
  cells, 100% coverage including PAC zero-by-rule.
- **281** — Pattern-C: split-Apprentice cohorts. 3 PDFs merged (Wage
  Sheet Journeymen + 2 cohort Apprentice Wage Sheets) + CBA into one
  rate_period. 95% coverage; the 8 remaining gaps are documented in the
  downloadable gap report.

---

## Closing (2 min)

**Verbal:**
> "What you saw: one batch, three documents, six minutes, fifty cents.
> Output matches your existing xlsx. Every cell has provenance and is
> reviewable. Reviewer can approve/override/reject/rework. Audit trail
> for compliance. And it scales to your 600+ union locals because the
> kernel + LLM split means the deterministic path handles the
> high-volume unions while the LLM picks up the long tail."

**Slide cue:** 5-union report + cost projection.

---

## Q&A prep — likely questions

| Q | A |
|---|---|
| "How do we add a new union?" | Add a kernel profile YAML + extractor class; OR rely on the LLM path which already handles any union. Adding a kernel union takes ~1 day per union by an experienced engineer with a sample Rate Notice + CBA. |
| "What's our cost at scale (600 locals × 12 periods)?" | ~7,200 periods/yr × $0.50 avg = ~$3,600/yr Bedrock. Lambda + Aurora are negligible above that. Maybe $5,000-$8,000/yr total infra. |
| "What if a PDF arrives malformed?" | The SFN run will FAIL on that PDF. Admin → Jobs shows it. Reviewer can re-upload the corrected version or skip. Other PDFs in the same batch aren't blocked. |
| "Can we use our own prompts?" | Yes. Each prompt is a single string constant in `lambdas/processing/llm-extractor/handler.py`. Drop in your prompts, hot-patch the Lambda, done. (P8 in the sprint backlog — pending customer prompts.) |
| "How do you handle a union that changes its layout?" | The kernel is hand-coded so a layout change requires a code patch. The LLM path adapts automatically. We'd flip a kernel union to LLM-fallback as a transition. |
| "Audit / SOC 2 story?" | Cell-level provenance + immutable audit_log in Aurora + CloudTrail + per-action user attribution + version chain on rate_periods. Add KMS encryption (already on), retention policy, and SOC 2 boilerplate. |

---

## Pre-flight checklist (run Monday morning)

- [ ] All 5 union cards visible in Business → Inbox
- [ ] Each opens, shows 0 gaps (or 8 for 281 with documented reasons)
- [ ] xlsx artifact downloads, opens in Excel
- [ ] Gap report JSON downloads, opens
- [ ] Source contribution panel shows for at least 483 (multi-source)
- [ ] Provenance panel renders the 3 method icons (kernel ▣, LLM ✦,
      derived ƒ) and the rule for zero-by-rule cells
- [ ] Admin → Dashboard total runs is reasonable (~15-20)
- [ ] Admin → Jobs shows the 11 demo runs
- [ ] Override flow works on at least one cell

## Backup plan (if live demo breaks)

- M5 Loom recording of the full flow recorded Monday — switch to that
  if Bedrock is throttling or browser cache misbehaves.
- All 5 rate_periods are already in Aurora — if the upload step
  hiccups, jump directly to the Inbox.
